#!/usr/bin/env python3
"""Browser chat UI for the Pooh narrative session.

Each browser tab owns one ``NarrativeSession``.  The server keeps the API key,
serializes calls per session, and pushes timed world events over WebSocket
even when the participant says nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import hmac
import ipaddress
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

from aiohttp import WSMsgType, web

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"
DEFAULT_SESSION_TTL_SECONDS = 600.0
DEFAULT_MAX_SESSIONS = 20
SWEEP_INTERVAL_SECONDS = 30.0
MAX_INPUT_CHARS = 500
MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_LOG_RECORDS = 2000
MIN_TIMER_WAIT_SECONDS = 0.1
TOKEN_HEADER = "X-Pooh-Token"


class SessionOutputLike(Protocol):
    source: str
    bot_response: str

    def to_dict(self) -> dict[str, Any]: ...


class NarrativeSessionLike(Protocol):
    """The subset of ``pooh_narrative_dspy.NarrativeSession`` used here."""

    session_id: str
    ended: bool

    def start(self) -> SessionOutputLike | None: ...
    def submit(self, user_input: str) -> SessionOutputLike | None: ...
    def poll(self) -> SessionOutputLike | None: ...
    def seconds_until_due(self) -> float | None: ...
    def close(self, reason: str = "requested") -> SessionOutputLike | None: ...


SessionFactory = Callable[[str], NarrativeSessionLike]


@dataclass
class WebSession:
    session: NarrativeSessionLike
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    sockets: set[web.WebSocketResponse] = field(default_factory=set)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    last_active: float = field(default_factory=time.monotonic)
    timer_task: asyncio.Task | None = None
    busy: bool = False


class SessionRegistry:
    """Owns per-participant sessions, their timers, and TTL disposal."""

    def __init__(
        self,
        factory: SessionFactory,
        *,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.factory = factory
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.clock = clock
        self.sessions: dict[str, WebSession] = {}

    async def create(self) -> WebSession | None:
        self.sweep()
        if len(self.sessions) >= self.max_sessions:
            return None
        session_id = uuid4().hex
        # The factory loads a private agent from disk; keep the loop responsive.
        session = await asyncio.to_thread(self.factory, session_id)
        if len(self.sessions) >= self.max_sessions:
            return None
        entry = WebSession(session, last_active=self.clock())
        self.sessions[session_id] = entry
        async with entry.lock:
            opening = await asyncio.to_thread(entry.session.start)
        if opening is not None:
            entry.transcript.append({"type": "output", "output": opening.to_dict()})
        entry.timer_task = asyncio.create_task(self._run_timer(entry))
        return entry

    def get(self, session_id: str) -> WebSession | None:
        entry = self.sessions.get(session_id)
        if entry is not None:
            entry.last_active = self.clock()
        return entry

    def sweep(self) -> list[str]:
        """Drop idle, disconnected sessions without the ending performance."""
        now = self.clock()
        expired = [
            session_id
            for session_id, entry in self.sessions.items()
            if not entry.sockets and now - entry.last_active >= self.ttl_seconds
        ]
        for session_id in expired:
            self.discard(session_id)
        return expired

    def discard(self, session_id: str) -> None:
        entry = self.sessions.pop(session_id, None)
        if entry is not None and entry.timer_task is not None:
            entry.timer_task.cancel()

    async def abandon(self, session_id: str) -> bool:
        """End a session on explicit restart: ending once, then free the slot."""
        entry = self.sessions.get(session_id)
        if entry is None:
            return False
        await self.close(entry)
        self.discard(session_id)
        for socket in list(entry.sockets):
            await socket.close()
        return True

    async def shutdown(self) -> None:
        for session_id in list(self.sessions):
            self.discard(session_id)

    async def _broadcast(self, entry: WebSession, message: dict[str, Any]) -> None:
        for socket in list(entry.sockets):
            try:
                await socket.send_json(message)
            except (ConnectionResetError, RuntimeError):
                entry.sockets.discard(socket)

    async def _set_busy(self, entry: WebSession, busy: bool) -> None:
        entry.busy = busy
        await self._broadcast(entry, {"type": "status", "busy": busy})

    async def _record(self, entry: WebSession, message: dict[str, Any]) -> None:
        """Keep a replayable message for reconnecting tabs, then push it."""
        entry.transcript.append(message)
        await self._broadcast(entry, message)

    async def _emit(self, entry: WebSession, output: SessionOutputLike | None) -> None:
        if output is None:
            return
        await self._record(entry, {"type": "output", "output": output.to_dict()})
        if entry.session.ended:
            await self._broadcast(entry, {"type": "ended"})

    async def run_call(
        self,
        entry: WebSession,
        call: Callable[[], SessionOutputLike | None],
    ) -> None:
        """Run one blocking session call with the per-session lock held."""
        async with entry.lock:
            if entry.session.ended:
                return
            await self._set_busy(entry, True)
            try:
                output = await asyncio.to_thread(call)
            except Exception as exc:
                # Keep the session usable; the model error is reported, not hidden.
                print(f"[{entry.session.session_id[:8]}] エラー: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                await self._broadcast(
                    entry,
                    {"type": "error", "message": "応答の生成に失敗しました。もう一度入力してください。"},
                )
                output = None
            finally:
                await self._set_busy(entry, False)
            await self._emit(entry, output)
        # Participant input can arm or reset timers; recompute the wait.
        entry.wake.set()

    async def submit(self, entry: WebSession, text: str) -> None:
        entry.last_active = self.clock()
        if entry.session.ended:
            return
        await self._record(entry, {"type": "user_input", "text": text})
        await self.run_call(entry, lambda: entry.session.submit(text))

    async def close(self, entry: WebSession) -> None:
        await self.run_call(entry, lambda: entry.session.close("web_close"))

    async def _run_timer(self, entry: WebSession) -> None:
        """Push due world events without waiting for participant input."""
        while not entry.session.ended:
            entry.wake.clear()
            async with entry.lock:
                delay = entry.session.seconds_until_due()
            if delay is not None:
                delay = max(delay, MIN_TIMER_WAIT_SECONDS)
            try:
                await asyncio.wait_for(entry.wake.wait(), timeout=delay)
                continue
            except asyncio.TimeoutError:
                pass
            await self.run_call(entry, entry.session.poll)


# ---------------------------------------------------------------------------
# HTTP / WebSocket handlers

REGISTRY_KEY = web.AppKey("registry", SessionRegistry)
TOKEN_KEY = web.AppKey("access_token", str)
INDEX_HTML_KEY = web.AppKey("index_html", str)
LOG_DIR_KEY = web.AppKey("log_dir", Path)


def _authorized(request: web.Request) -> bool:
    expected = request.app[TOKEN_KEY]
    if not expected:
        return True
    supplied = request.headers.get(TOKEN_HEADER) or request.query.get("token", "")
    return hmac.compare_digest(supplied, expected)


async def index(request: web.Request) -> web.Response:
    body = request.app[INDEX_HTML_KEY].replace(
        "{{LOCAL_LOG_STYLE}}", _local_log_style(request.remote)
    )
    return web.Response(text=body, content_type="text/html")


def _is_loopback_address(address: str | None) -> bool:
    """Trust only the TCP peer address, never forwarding headers."""
    if not address:
        return False
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if parsed.is_loopback:
        return True
    return bool(
        isinstance(parsed, ipaddress.IPv6Address)
        and parsed.ipv4_mapped is not None
        and parsed.ipv4_mapped.is_loopback
    )


def _local_log_style(address: str | None) -> str:
    if _is_loopback_address(address):
        return ""
    return ".local-log-feature { display: none !important; }"


def _local_log_access_allowed(request: web.Request) -> bool:
    return _is_loopback_address(request.remote)


async def static_app_script(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "app.js")


async def static_stylesheet(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "style.css")


def _resolve_log_path(log_dir: Path, name: str) -> Path | None:
    """Resolve one server-owned session log without allowing path traversal."""
    if (
        not name.startswith("session_")
        or not name.endswith(".jsonl")
        or Path(name).name != name
        or "/" in name
        or "\\" in name
    ):
        return None
    root = log_dir.resolve()
    candidate = log_dir / name
    if candidate.is_symlink():
        return None
    resolved = candidate.resolve()
    if resolved.parent != root or not resolved.is_file():
        return None
    return resolved


def _list_logs(log_dir: Path) -> list[dict[str, Any]]:
    if not log_dir.is_dir():
        return []
    items = []
    for candidate in log_dir.glob("session_*.jsonl"):
        path = _resolve_log_path(log_dir, candidate.name)
        if path is None:
            continue
        stat = path.stat()
        items.append({
            "name": candidate.name,
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
        })
    return sorted(items, key=lambda item: item["modified_at"], reverse=True)


def _read_log(path: Path) -> tuple[list[dict[str, Any]], int, bool]:
    records: list[dict[str, Any]] = []
    invalid_lines = 0
    truncated = False
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if len(records) >= MAX_LOG_RECORDS:
                truncated = True
                break
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if isinstance(value, dict):
                records.append(value)
            else:
                invalid_lines += 1
    return records, invalid_lines, truncated


async def list_logs(request: web.Request) -> web.Response:
    if not _local_log_access_allowed(request):
        return web.json_response({"error": "local access only"}, status=403)
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    logs = await asyncio.to_thread(_list_logs, request.app[LOG_DIR_KEY])
    return web.json_response({"logs": logs})


async def read_log(request: web.Request) -> web.Response:
    if not _local_log_access_allowed(request):
        return web.json_response({"error": "local access only"}, status=403)
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    path = _resolve_log_path(request.app[LOG_DIR_KEY], request.match_info["name"])
    if path is None:
        return web.json_response({"error": "log not found"}, status=404)
    if path.stat().st_size > MAX_LOG_BYTES:
        return web.json_response({"error": "log is too large"}, status=413)
    records, invalid_lines, truncated = await asyncio.to_thread(_read_log, path)
    return web.json_response({
        "name": path.name,
        "records": records,
        "invalid_lines": invalid_lines,
        "truncated": truncated,
    })


async def create_session(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    registry = request.app[REGISTRY_KEY]
    entry = await registry.create()
    if entry is None:
        return web.json_response(
            {"error": "満員です。しばらくしてからもう一度お試しください。"}, status=503
        )
    return web.json_response({"session_id": entry.session.session_id})


async def delete_session(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    found = await request.app[REGISTRY_KEY].abandon(request.match_info["session_id"])
    return web.json_response({"deleted": found}, status=200 if found else 404)


async def session_socket(request: web.Request) -> web.WebSocketResponse:
    socket = web.WebSocketResponse(heartbeat=30.0)
    await socket.prepare(request)
    registry = request.app[REGISTRY_KEY]
    entry = registry.get(request.match_info["session_id"]) if _authorized(request) else None
    if entry is None:
        await socket.send_json({"type": "expired"})
        await socket.close()
        return socket

    entry.sockets.add(socket)
    await socket.send_json({
        "type": "transcript",
        "messages": entry.transcript,
        "ended": entry.session.ended,
        "busy": entry.busy,
    })
    try:
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            try:
                payload = message.json()
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if kind == "user_input":
                text = str(payload.get("text", "")).strip()[:MAX_INPUT_CHARS]
                if text:
                    await registry.submit(entry, text)
            elif kind == "close":
                await registry.close(entry)
    finally:
        # Disconnection is not an ending; the tab may reconnect until the TTL.
        entry.sockets.discard(socket)
        entry.last_active = registry.clock()
    return socket


async def _sweep_forever(registry: SessionRegistry) -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        for session_id in registry.sweep():
            print(f"[{session_id[:8]}] TTL切れのため破棄しました")


def create_app(
    factory: SessionFactory,
    *,
    ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    access_token: str = "",
    scenario_title: str = "Narrative Chat",
    scene_intro: str = "",
    log_dir: Path | None = None,
) -> web.Application:
    app = web.Application()
    registry = SessionRegistry(factory, ttl_seconds=ttl_seconds, max_sessions=max_sessions)
    app[REGISTRY_KEY] = registry
    app[TOKEN_KEY] = access_token
    app[LOG_DIR_KEY] = log_dir or Path(
        os.getenv("POOH_LOG_DIR", str(PROJECT_ROOT / "logs"))
    )
    index_template = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app[INDEX_HTML_KEY] = index_template.replace(
        "{{SCENARIO_TITLE}}", html.escape(scenario_title)
    ).replace("{{SCENE_INTRO}}", html.escape(scene_intro))

    async def lifecycle(app: web.Application):
        sweeper = asyncio.create_task(_sweep_forever(registry))
        yield
        sweeper.cancel()
        await registry.shutdown()

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/", index)
    app.router.add_get("/api/logs", list_logs)
    app.router.add_get("/api/logs/{name}", read_log)
    app.router.add_post("/api/sessions", create_session)
    app.router.add_delete("/api/sessions/{session_id}", delete_session)
    app.router.add_get("/ws/{session_id}", session_socket)
    # Do not expose index.html as a static file: it would bypass per-peer UI hiding.
    app.router.add_get("/static/app.js", static_app_script)
    app.router.add_get("/static/style.css", static_stylesheet)
    return app


# ---------------------------------------------------------------------------
# Entry point


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def check_bind_security(host: str, access_token: str) -> str | None:
    """Return an error when a non-loopback bind would expose unguarded API use."""
    if host in LOOPBACK_HOSTS or access_token:
        return None
    return (
        f"--host {host} で公開するには POOH_WEB_TOKEN を設定してください"
        "（APIキーの無断利用を防ぐため）。"
    )


class _LockedPublisher:
    """Serialize publishes from several sessions onto one relay connection."""

    def __init__(self, publisher: Any) -> None:
        self._publisher = publisher
        self._lock = threading.Lock()

    def publish(self, **kwargs: Any) -> None:
        with self._lock:
            self._publisher.publish(**kwargs)


def build_factory(args: argparse.Namespace) -> tuple[SessionFactory, str, str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pooh_narrative_dspy as pooh
    from narrative_relay import NarrativeRelayPublisher
    from scenarios import get_scenario

    pooh.validate_environment()
    scenario = get_scenario(args.scenario)
    _, _, train_model, judge_model = pooh.configure_models()
    # Fail fast at startup if the compiled program is missing.
    pooh.load_compiled_program(train_model, judge_model, scenario)
    program_id = pooh.cache_hash(train_model, judge_model, scenario)
    publisher = (
        _LockedPublisher(
            NarrativeRelayPublisher(args.ros_relay_host, args.ros_relay_port, scenario.key)
        )
        if args.ros_relay
        else None
    )

    def factory(session_id: str) -> NarrativeSessionLike:
        # A private agent per session lets different participants' LLM calls
        # run concurrently without sharing module state across threads.
        agent = pooh.load_compiled_program(train_model, judge_model, scenario)
        return pooh.NarrativeSession(
            agent,
            train_model,
            program_id,
            scenario,
            ros_publisher=publisher,
            session_id=session_id,
        )

    return factory, scenario.label, scenario.scene_intro


def parse_args() -> argparse.Namespace:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scenarios import DEFAULT_SCENARIO, SCENARIOS

    parser = argparse.ArgumentParser(description="Pooh narrative Web chat UI")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default=DEFAULT_SCENARIO)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--session-ttl", type=float, default=DEFAULT_SESSION_TTL_SECONDS,
                        help="切断後、再接続を待つ秒数。")
    parser.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    parser.add_argument("--ros-relay", action="store_true",
                        help="指定時、各ターンをROS中継へ送る（入力は受けない）。")
    parser.add_argument("--ros-relay-host", default="127.0.0.1")
    parser.add_argument("--ros-relay-port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    access_token = os.getenv("POOH_WEB_TOKEN", "")
    error = check_bind_security(args.host, access_token)
    if error is not None:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    try:
        factory, scenario_title, scene_intro = build_factory(args)
    except Exception as exc:
        # APIキーやLMリクエスト本文を含む可能性のある詳細tracebackは表示しない。
        print(f"エラー: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    app = create_app(
        factory,
        ttl_seconds=args.session_ttl,
        max_sessions=args.max_sessions,
        access_token=access_token,
        scenario_title=scenario_title,
        scene_intro=scene_intro,
    )
    suffix = f"/?token={access_token}" if access_token else "/"
    print(f"Web UI: http://{args.host}:{args.port}{suffix}")
    web.run_app(app, host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
