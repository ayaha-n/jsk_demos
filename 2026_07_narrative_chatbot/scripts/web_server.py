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
import secrets
import signal
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence
from uuid import uuid4

import requests
from aiohttp import WSMsgType, web

from crowdsourcing import (IDLE_CLOSE_EVENTS, SUPPORTED_SCENARIOS, Progress,
                          ProgressStore, book_ready, book_revision, reached_goal, timestamp)
from web_tts import TTSCache, TTSConfig

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"
DEFAULT_SESSION_TTL_SECONDS = 600.0
DEFAULT_MAX_SESSIONS = 50
SWEEP_INTERVAL_SECONDS = 30.0
MAX_INPUT_CHARS = 500
TOUCH_PARTS = frozenset({"頭", "右腕", "左腕", "おなか", "右足", "左足"})
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
    def observe(self, actions: Sequence[str], with_speech: bool = False) -> SessionOutputLike | None: ...
    def poll(self) -> SessionOutputLike | None: ...
    def follow_up(self) -> SessionOutputLike | None: ...
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
    study: Progress | None = None
    pending_playback: dict[str, float] = field(default_factory=dict)
    # Allows the end button after study_end_after_seconds (crowdsourcing).
    end_timer_task: asyncio.Task | None = None


class SessionRegistry:
    """Owns per-participant sessions, their timers, and TTL disposal."""

    def __init__(
        self,
        factory: SessionFactory,
        *,
        ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
        local_output_listeners: Sequence[Any] = (),
        study_store: ProgressStore | None = None,
        tts: TTSCache | None = None,
        study_end_after_seconds: float | None = None,
    ) -> None:
        self.factory = factory
        self.study_store = study_store
        # Under crowdsourcing, the end button is allowed this long after a
        # session starts even if the story has not reached its goal.
        self.study_end_after_seconds = study_end_after_seconds
        self.tts = tts
        # Given only to sessions started on this PC itself (e.g. the live
        # picture book on the tablet next to the robot).
        self.local_output_listeners = tuple(local_output_listeners)
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self.clock = clock
        self.sessions: dict[str, WebSession] = {}

    async def create(self, *, local: bool = False) -> WebSession | None:
        """A new session; ``local`` when its browser runs on this PC."""
        self.sweep()
        if len(self.sessions) >= self.max_sessions:
            return None
        session_id = uuid4().hex
        # The factory loads a private agent from disk; keep the loop responsive.
        session = await asyncio.to_thread(self.factory, session_id)
        if len(self.sessions) >= self.max_sessions:
            return None
        if local and self.local_output_listeners:
            # Before start(), so the opening reaches them too.
            existing = tuple(getattr(session, "output_listeners", ()))
            session.output_listeners = existing + tuple(
                listener for listener in self.local_output_listeners if listener not in existing
            )
        entry = WebSession(session, last_active=self.clock())
        if self.study_store is not None:
            controller = getattr(session, "controller", None)
            if controller is not None:
                controller.required_events = tuple(
                    event for event in controller.required_events
                    if event.event_id not in IDLE_CLOSE_EVENTS
                )
            log_path = getattr(session, "log_path", None)
            entry.study = Progress(session_id, _session_scenario(entry),
                                   Path(log_path).name if log_path else None, timestamp())
            await asyncio.to_thread(self.study_store.save, entry.study)
        self.sessions[session_id] = entry
        async with entry.lock:
            opening = await asyncio.to_thread(entry.session.start)
        if opening is not None:
            entry.transcript.append({"type": "output", "output": await self._output_dict(opening, entry)})
        entry.timer_task = asyncio.create_task(self._run_timer(entry))
        if entry.study is not None and self.study_end_after_seconds is not None:
            entry.end_timer_task = asyncio.create_task(self._allow_end_later(entry))
        return entry

    async def _allow_end_later(self, entry: WebSession) -> None:
        await asyncio.sleep(self.study_end_after_seconds)
        async with entry.lock:
            if entry.session.ended or entry.study is None or entry.study.end_allowed:
                return
            progress = replace(entry.study, end_allowed_at=timestamp())
            await asyncio.to_thread(self.study_store.save, progress)
            entry.study = progress
        await self._broadcast(entry, {"type": "study_status", "study": progress.public()})

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
        if entry is None:
            return
        for task in (entry.timer_task, entry.end_timer_task):
            if task is not None:
                task.cancel()

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
        await self._sync_study(entry)
        if output is None:
            return
        await self._record(entry, {"type": "output", "output": await self._output_dict(output, entry)})
        if entry.session.ended:
            await self._broadcast(entry, {"type": "ended"})

    async def _output_dict(self, output: SessionOutputLike, entry: WebSession) -> dict[str, Any]:
        output_dict = output.to_dict()
        output_dict["audio_url"] = await self._synthesize_audio(output.bot_response)
        controller = getattr(entry.session, "controller", None)
        if (output_dict["audio_url"] and (entry.sockets or not entry.transcript)
                and callable(getattr(controller, "confirm_speech_finished", None))):
            playback_id = uuid4().hex
            output_dict["playback_id"] = playback_id
            # A stalled player must not suspend timed events indefinitely.
            estimate = controller.speech_seconds(output.bot_response)
            entry.pending_playback[playback_id] = self.clock() + max(120.0, estimate * 3)
        return output_dict

    async def playback_finished(self, entry: WebSession, playback_id: str) -> None:
        finished_queue = False
        async with entry.lock:
            if playback_id not in entry.pending_playback:
                return
            del entry.pending_playback[playback_id]
            if not entry.pending_playback and not entry.session.ended:
                entry.session.controller.confirm_speech_finished()
                finished_queue = True
        if finished_queue:
            # Preserve overdue follow-ups; only future waits are based on the
            # real audio finish. A due beat may intentionally follow at once.
            await self.run_call(entry, entry.session.follow_up, wait_for_playback=True)
        entry.wake.set()

    async def _synthesize_audio(self, text: str) -> str | None:
        """A URL to the line's audio, or None if TTS is off or failed.

        Best-effort like illustrations: a browser missing audio_url just
        shows the text, same as a storybook page that never gets a picture.
        """
        if self.tts is None or not text.strip():
            return None
        try:
            key, _wav = await asyncio.to_thread(self.tts.synthesize, text)
        except (requests.RequestException, ValueError) as exc:
            print(f"[tts] 合成に失敗しました: {type(exc).__name__}: {exc}", file=sys.stderr)
            return None
        return f"/audio/{key}.wav"

    async def _sync_study(self, entry: WebSession) -> None:
        if entry.study is None:
            return
        progress = replace(entry.study)
        if progress.goal_reached_at is None and reached_goal(entry.session):
            progress.goal_reached_at = timestamp()
        if progress.ended_at is None and entry.session.ended:
            progress.ended_at = timestamp()
            progress.ended_before_goal = progress.goal_reached_at is None
        if progress != entry.study:
            await asyncio.to_thread(self.study_store.save, progress)
            entry.study = progress
            await self._broadcast(entry, {"type": "study_status", "study": progress.public()})

    async def run_call(
        self,
        entry: WebSession,
        call: Callable[[], SessionOutputLike | None],
        wait_for_playback: bool = False,
    ) -> None:
        """Run one blocking session call with the per-session lock held."""
        async with entry.lock:
            if entry.session.ended or (wait_for_playback and entry.pending_playback):
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
        # An overdue story event follows Pooh's answer instead of interrupting.
        await self.run_call(entry, entry.session.follow_up, wait_for_playback=True)

    async def close(self, entry: WebSession) -> None:
        await self.run_call(entry, lambda: entry.session.close("web_close"))

    async def touch(self, entry: WebSession, part: str) -> None:
        """Record a touch without a reply or a busy-status transition.

        Like ROS observations, session mutations are serialized with a running
        turn. Touches received during generation wait for that turn to finish.
        """
        if part not in TOUCH_PARTS:
            return
        entry.last_active = self.clock()
        async with entry.lock:
            if entry.session.ended:
                return
            output = await asyncio.to_thread(
                entry.session.observe, [f"参加者がプーの{part}に触れた"]
            )
            await self._emit(entry, output)
            if output is not None:
                await self._broadcast(entry, {"type": "touch_recorded", "part": part})

    async def _run_timer(self, entry: WebSession) -> None:
        """Push due world events without waiting for participant input."""
        while not entry.session.ended:
            entry.wake.clear()
            async with entry.lock:
                expired = [key for key, limit in entry.pending_playback.items() if limit <= self.clock()]
                for key in expired:
                    del entry.pending_playback[key]
                if expired and not entry.pending_playback:
                    entry.session.controller.confirm_speech_finished()
                delay = (min(entry.pending_playback.values()) - self.clock()
                         if entry.pending_playback else entry.session.seconds_until_due())
            if delay is not None:
                delay = max(delay, MIN_TIMER_WAIT_SECONDS)
            try:
                await asyncio.wait_for(entry.wake.wait(), timeout=delay)
                continue
            except asyncio.TimeoutError:
                pass
            await self.run_call(entry, entry.session.poll, wait_for_playback=True)


# ---------------------------------------------------------------------------
# HTTP / WebSocket handlers

REGISTRY_KEY = web.AppKey("registry", SessionRegistry)
TOKEN_KEY = web.AppKey("access_token", str)
INDEX_HTML_KEY = web.AppKey("index_html", str)
LOG_DIR_KEY = web.AppKey("log_dir", Path)
# The local port the public tunnel forwards to (--public), if any.
PUBLIC_PORT_KEY = web.AppKey("public_port", int)


def _authorized(request: web.Request) -> bool:
    expected = request.app[TOKEN_KEY]
    if not expected:
        return True
    supplied = request.headers.get(TOKEN_HEADER) or request.query.get("token", "")
    return hmac.compare_digest(supplied, expected)


def _asset_version() -> str:
    """Changes whenever a frontend asset does: the page asks for them by
    this, so a browser never keeps using a copy cached before an update."""
    return str(max(
        (WEB_ROOT / name).stat().st_mtime_ns for name in ("app.js", "style.css", "bear.js", "crowdsourcing.js")
    ))


async def index(request: web.Request) -> web.Response:
    body = request.app[INDEX_HTML_KEY].replace(
        "{{LOCAL_ONLY_STYLE}}", _local_only_style(_local_peer(request))
    ).replace("{{ASSET_VERSION}}", _asset_version())
    return web.Response(text=body, content_type="text/html", headers=NO_CACHE)


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


def _local_only_style(address: str | None) -> str:
    """Hides host-PC-only controls (past logs, response details) elsewhere."""
    if _is_loopback_address(address):
        return ""
    return ".local-only-feature { display: none !important; }"


def _via_public_tunnel(request: web.Request) -> bool:
    """Whether the request came in through the --public tunnel. The tunnel
    connects from 127.0.0.1 too, so it is told apart by the port it was
    given, never by a forwarding header (which anyone can send)."""
    app = getattr(request, "app", None)
    public_port = app.get(PUBLIC_PORT_KEY) if app is not None else None
    transport = getattr(request, "transport", None)
    if public_port is None or transport is None:
        return False
    sockname = transport.get_extra_info("sockname")
    return bool(sockname) and sockname[1] == public_port


def _local_peer(request: web.Request) -> str | None:
    """The peer address, for the loopback checks; none through the tunnel."""
    return None if _via_public_tunnel(request) else request.remote


def _is_local_request(request: web.Request) -> bool:
    """Made on this PC itself: allowed the past-log viewer and the tablet."""
    return _is_loopback_address(_local_peer(request))


def _local_log_access_allowed(request: web.Request) -> bool:
    return _is_local_request(request)


# Always revalidated: with only Last-Modified, a browser may keep using an
# older app.js after an update, out of step with the freshly served page.
NO_CACHE = {"Cache-Control": "no-cache"}


async def static_app_script(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "app.js", headers=NO_CACHE)


async def static_stylesheet(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "style.css", headers=NO_CACHE)


async def static_bear_script(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "bear.js", headers=NO_CACHE)


async def static_crowdsourcing_script(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "crowdsourcing.js", headers=NO_CACHE)


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


STORYBOOK_LAYOUTS = ("landscape", "portrait")


def _storybook_layout(request: web.Request) -> str | None:
    layout = request.query.get("layout", "landscape")
    return layout if layout in STORYBOOK_LAYOUTS else None


def _build_storybook(
    records: list[dict[str, Any]], layout: str, scenario_key: str | None = None
) -> Any:
    import storybook
    from scenarios import get_scenario

    scenario = get_scenario(scenario_key or str(records[0].get("scenario", "")))
    return storybook.build_book(
        records, scenario=scenario.key, opening_line=scenario.opening_line, layout=layout
    )


def _storybook_dict(
    records: list[dict[str, Any]], layout: str, scenario_key: str | None = None
) -> dict[str, Any]:
    import storybook

    book = _build_storybook(records, layout, scenario_key).to_dict()
    for page in book["pages"]:
        illustration = page.get("illustration")
        available = illustration and storybook.find_illustration(illustration) is not None
        page["illustration_url"] = f"/storybook/{illustration}" if available else None
    return book


async def _storybook_records(request: web.Request) -> tuple[Path, list[dict[str, Any]]] | web.Response:
    if not _local_log_access_allowed(request):
        return web.json_response({"error": "local access only"}, status=403)
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    path = _resolve_log_path(request.app[LOG_DIR_KEY], request.match_info["name"])
    if path is None:
        return web.json_response({"error": "log not found"}, status=404)
    if path.stat().st_size > MAX_LOG_BYTES:
        return web.json_response({"error": "log is too large"}, status=413)
    records, _, _ = await asyncio.to_thread(_read_log, path)
    if not records:
        return web.json_response({"error": "empty log"}, status=404)
    return path, records


async def read_storybook(request: web.Request) -> web.Response:
    """The picture book of one past session, for review after the fact."""
    layout = _storybook_layout(request)
    if layout is None:
        return web.json_response({"error": "unknown layout"}, status=400)
    found = await _storybook_records(request)
    if isinstance(found, web.Response):
        return found
    path, records = found
    try:
        book = await asyncio.to_thread(_storybook_dict, records, layout)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=422)
    return web.json_response({"name": path.name, **book})


async def read_storybook_pdf(request: web.Request) -> web.StreamResponse:
    """The book as a PDF: the one the live book saved, laid out as the tablet
    showed it, or else one rendered now in the requested layout."""
    layout = _storybook_layout(request)
    if layout is None:
        return web.json_response({"error": "unknown layout"}, status=400)
    found = await _storybook_records(request)
    if isinstance(found, web.Response):
        return found
    path, records = found
    saved = request.app[LOG_DIR_KEY] / "storybooks" / f"{path.stem}.pdf"
    return await _storybook_pdf_response(request, path.stem, saved, records, layout)


async def _storybook_pdf_response(
    request: web.Request,
    stem: str,
    saved: Path | None,
    records: list[dict[str, Any]],
    layout: str,
    scenario_key: str | None = None,
) -> web.StreamResponse:
    headers = {"Content-Disposition": f'inline; filename="{stem}.pdf"'}
    newer_picture = False
    if saved is not None and saved.is_file() and records and records[0].get("session_id"):
        import storybook

        config = storybook.load_story_config(scenario_key or str(records[0].get("scenario", "")))
        for beat_id in config.beats:
            name = storybook.generated_illustration_name(
                config.scenario, str(records[0]["session_id"]), beat_id)
            image = storybook.find_illustration(name)
            if image and image.stat().st_mtime_ns > saved.stat().st_mtime_ns:
                newer_picture = True
                break
    if "layout" not in request.query and saved is not None and saved.is_file() and not newer_picture:
        return web.FileResponse(saved, headers=headers)

    def render() -> bytes:
        from storybook_render import write_pdf

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "book.pdf"
            write_pdf(_build_storybook(records, layout, scenario_key), target)
            return target.read_bytes()

    try:
        body = await asyncio.to_thread(render)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=422)
    return web.Response(body=body, content_type="application/pdf", headers=headers)


def _own_session_book(request: web.Request) -> tuple[WebSession, Path | None] | web.Response:
    """The session the request names, for its own participant. Unlike the
    past-log viewer this is open beyond loopback: holding a session's id
    (random, known only to its browser) is what lets one read its book, and
    only its book."""
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    entry = request.app[REGISTRY_KEY].get(request.match_info["session_id"])
    if entry is None:
        return web.json_response({"error": "session not found"}, status=404)
    log_path = getattr(entry.session, "log_path", None)
    return entry, Path(log_path) if log_path is not None else None


def _session_records(log_path: Path | None) -> list[dict[str, Any]]:
    """Its log so far; none before the first turn (the book is then just
    the opening)."""
    if log_path is None or not log_path.is_file():
        return []
    records, _, _ = _read_log(log_path)
    return records


def _session_scenario(entry: WebSession) -> str | None:
    scenario = getattr(entry.session, "scenario", None)
    return getattr(scenario, "key", None)


async def read_session_storybook(request: web.Request) -> web.Response:
    """The book of the requester's own session, so far or finished."""
    layout = _storybook_layout(request)
    if layout is None:
        return web.json_response({"error": "unknown layout"}, status=400)
    found = _own_session_book(request)
    if isinstance(found, web.Response):
        return found
    entry, log_path = found
    records = await asyncio.to_thread(_session_records, log_path)
    scenario_key = _session_scenario(entry)
    if not records and scenario_key is None:
        return web.json_response({"error": "no storybook"}, status=404)
    try:
        book = await asyncio.to_thread(_storybook_dict, records, layout, scenario_key)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=422)
    if entry.study is not None:
        book["study_revision"] = book_revision(book)
    return web.json_response(book)


async def session_completion(request: web.Request) -> web.Response:
    """Restore a receipt or accept browser-reported book reading events."""
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    registry = request.app[REGISTRY_KEY]
    if registry.study_store is None:
        return web.json_response({"error": "crowdsourcing is disabled"}, status=404)
    session_id = request.match_info["session_id"]
    try:
        entry = registry.get(session_id)
        progress = entry.study if entry else await asyncio.to_thread(registry.study_store.load, session_id)
    except ValueError:
        return web.json_response({"error": "invalid session id"}, status=400)
    if progress is None:
        return web.json_response({"error": "session not found"}, status=404)
    if request.method == "GET":
        return web.json_response(progress.public(), headers=NO_CACHE)
    if entry is None:
        return web.json_response({"error": "session expired"}, status=410)
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if (not isinstance(payload, dict) or not isinstance(payload.get("event"), str)
            or payload["event"] not in {"book_opened", "last_page", "read_confirmed"}):
        return web.json_response({"error": "unknown reading event"}, status=400)
    layout = payload.get("layout", "landscape")
    if layout not in STORYBOOK_LAYOUTS:
        return web.json_response({"error": "unknown layout"}, status=400)
    async with entry.lock:
        if not entry.session.ended:
            return web.json_response({"error": "会話が終わってから絵本を確認してください。"}, status=409)
        if entry.study.completion_code:
            return web.json_response(entry.study.public(), headers=NO_CACHE)
        log_path = getattr(entry.session, "log_path", None)
        records = await asyncio.to_thread(_session_records, Path(log_path) if log_path else None)
        try:
            book = await asyncio.to_thread(_storybook_dict, records, layout, _session_scenario(entry))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=422)
        if not book_ready(book) or payload.get("revision") != book_revision(book):
            return web.json_response({"error": "絵本を準備中です。表示が更新されてから確認してください。"}, status=409)
        progress = replace(entry.study)
        try:
            progress.report(payload["event"], payload["revision"], registry.study_store.passphrase)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        await asyncio.to_thread(registry.study_store.save, progress)
        entry.study = progress
        return web.json_response(progress.public(), headers=NO_CACHE)


async def read_session_storybook_pdf(request: web.Request) -> web.StreamResponse:
    layout = _storybook_layout(request)
    if layout is None:
        return web.json_response({"error": "unknown layout"}, status=400)
    found = _own_session_book(request)
    if isinstance(found, web.Response):
        return found
    entry, log_path = found
    records = await asyncio.to_thread(_session_records, log_path)
    scenario_key = _session_scenario(entry)
    if not records and scenario_key is None:
        return web.json_response({"error": "no storybook"}, status=404)
    stem = log_path.stem if log_path is not None else "storybook"
    saved = log_path.parent / "storybooks" / f"{stem}.pdf" if log_path is not None else None
    return await _storybook_pdf_response(request, stem, saved, records, layout, scenario_key)


def _resolve_illustration(name: str) -> Path | None:
    import storybook

    return storybook.find_illustration(name)


async def storybook_illustration(request: web.Request) -> web.StreamResponse:
    path = _resolve_illustration(request.match_info["name"])
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def storybook_font(request: web.Request) -> web.StreamResponse:
    import storybook

    if not storybook.FONT_PATH.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(storybook.FONT_PATH)


# Content-addressed by the synthesis text+settings, so it never changes.
AUDIO_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


async def synthesized_audio(request: web.Request) -> web.StreamResponse:
    tts = request.app[REGISTRY_KEY].tts
    name = request.match_info["name"]
    if tts is None or not name.endswith(".wav"):
        raise web.HTTPNotFound()
    wav = tts.get(name[:-len(".wav")])
    if wav is None:
        raise web.HTTPNotFound()
    return web.Response(body=wav, content_type="audio/wav", headers=AUDIO_CACHE_HEADERS)


async def create_session(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    registry = request.app[REGISTRY_KEY]
    entry = await registry.create(local=_is_local_request(request))
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
        "touch_supported": True,
        "pending_playback": list(entry.pending_playback),
        "study": entry.study.public() if entry.study else {"enabled": False},
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
                if entry.study is not None and not entry.study.end_allowed:
                    await socket.send_json({"type": "error", "message": "まだお話の途中です。お話がひと区切りつくと、「終了して絵本を見る」を押せるようになります。"})
                else:
                    await registry.close(entry)
            elif kind == "touch":
                await registry.touch(entry, payload.get("part") if isinstance(payload.get("part"), str) else "")
            elif kind == "playback_finished" and isinstance(payload.get("playback_id"), str):
                await registry.playback_finished(entry, payload["playback_id"])
    finally:
        # Disconnection is not an ending; the tab may reconnect until the TTL.
        entry.sockets.discard(socket)
        if not entry.sockets:
            for playback_id in list(entry.pending_playback):
                await registry.playback_finished(entry, playback_id)
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
    local_output_listeners: Sequence[Any] = (),
    crowdsourcing: bool = False,
    passphrase: str | None = None,
    end_after_minutes: float | None = None,
    tts: TTSCache | None = None,
) -> web.Application:
    app = web.Application()
    effective_log_dir = log_dir or Path(os.getenv("POOH_LOG_DIR", str(PROJECT_ROOT / "logs")))
    registry = SessionRegistry(
        factory,
        ttl_seconds=ttl_seconds,
        max_sessions=max_sessions,
        local_output_listeners=local_output_listeners,
        study_store=(ProgressStore(effective_log_dir / "crowdsourcing", passphrase)
                     if crowdsourcing else None),
        tts=tts,
        study_end_after_seconds=(end_after_minutes * 60
                                 if crowdsourcing and end_after_minutes is not None else None),
    )
    app[REGISTRY_KEY] = registry
    app[TOKEN_KEY] = access_token
    app[LOG_DIR_KEY] = effective_log_dir
    index_template = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    app[INDEX_HTML_KEY] = index_template.replace(
        "{{SCENARIO_TITLE}}", html.escape(scenario_title)
    ).replace("{{SCENE_INTRO}}", html.escape(scene_intro)).replace(
        "{{CROWDSOURCING}}", "true" if crowdsourcing else "false"
    ).replace(
        "{{TTS}}", "true" if tts is not None else "false"
    )

    async def lifecycle(app: web.Application):
        sweeper = asyncio.create_task(_sweep_forever(registry))
        yield
        sweeper.cancel()
        await registry.shutdown()

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/", index)
    app.router.add_get("/api/logs", list_logs)
    app.router.add_get("/api/logs/{name}", read_log)
    app.router.add_get("/api/logs/{name}/storybook", read_storybook)
    app.router.add_get("/api/logs/{name}/storybook.pdf", read_storybook_pdf)
    app.router.add_get("/storybook/{name:.+\\.png}", storybook_illustration)
    app.router.add_get("/static/storybook-font.ttf", storybook_font)
    app.router.add_get("/audio/{name}", synthesized_audio)
    app.router.add_post("/api/sessions", create_session)
    app.router.add_delete("/api/sessions/{session_id}", delete_session)
    app.router.add_get("/api/sessions/{session_id}/storybook", read_session_storybook)
    app.router.add_get("/api/sessions/{session_id}/storybook.pdf", read_session_storybook_pdf)
    app.router.add_get("/api/sessions/{session_id}/completion", session_completion)
    app.router.add_post("/api/sessions/{session_id}/completion", session_completion)
    app.router.add_get("/ws/{session_id}", session_socket)
    # Do not expose index.html as a static file: it would bypass per-peer UI hiding.
    app.router.add_get("/static/app.js", static_app_script)
    app.router.add_get("/static/style.css", static_stylesheet)
    app.router.add_get("/static/bear.js", static_bear_script)
    app.router.add_get("/static/crowdsourcing.js", static_crowdsourcing_script)
    return app


# ---------------------------------------------------------------------------
# Entry point


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def resolve_access_token(configured: str, *, public: bool) -> str:
    """POOH_WEB_TOKEN if set; published without one, a fresh unguessable key
    for this launch (shown on the terminal only, never stored)."""
    if configured or not public:
        return configured
    return secrets.token_urlsafe(24)


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

    def publish_chunk(self, **kwargs: Any) -> bool:
        with self._lock:
            return self._publisher.publish_chunk(**kwargs)


def build_factory(
    args: argparse.Namespace,
) -> tuple[SessionFactory, str, str, list[Any]]:
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

    # The live book on the tablet, for sessions started on this PC only (see
    # SessionRegistry.create): one book, following the newest of them.
    listeners = pooh.create_storybook_listeners(args)
    from storybook_illustrations import SessionIllustrations
    shared_listeners = [listener for listener in listeners
                        if isinstance(listener, SessionIllustrations)]

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
            output_listeners=shared_listeners,
        )

    return factory, scenario.label, scenario.scene_intro, listeners


def build_parser() -> argparse.ArgumentParser:
    """The command-line options (also read by scripts/launch.py, which offers
    only the ones this version has)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scenarios import DEFAULT_SCENARIO, SCENARIOS

    parser = argparse.ArgumentParser(description="Pooh narrative Web chat UI")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default=DEFAULT_SCENARIO)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--session-ttl", type=float, default=DEFAULT_SESSION_TTL_SECONDS,
                        help="切断後、再接続を待つ秒数。")
    parser.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS)
    parser.add_argument("--crowdsourcing", action="store_true",
                        help="ゴール後に終了ボタンを有効化し、絵本の読了確認後に提出用コードを発行する。")
    parser.add_argument("--end-after-minutes", type=float, default=10.0, metavar="MIN",
                        help="--crowdsourcing と使う。ゴール前でも、開始からこの分数たつと終了ボタンを"
                             "押せるようにする（既定10分。0 以下で無効）。")
    parser.add_argument("--passphrase", default=None, metavar="TEXT",
                        help="--crowdsourcing と使う。完了コードの下に出す合言葉（全員同じ。"
                             "クラウドソーシングの回の数だけ変えられる）。例: アイコトバ")
    parser.add_argument("--tts-server-url", default=os.getenv("POOH_TTS_SERVER_URL", ""),
                        help="Style-Bert-VITS2のeditorサーバーURL（例: http://127.0.0.1:8000）。"
                             "指定時のみプーの発話を音声合成する。失敗時は音声なしで継続する。")
    parser.add_argument("--ros-relay", action="store_true",
                        help="指定時、各ターンをROS中継へ送る（入力は受けない）。")
    parser.add_argument("--ros-relay-host", default="127.0.0.1")
    parser.add_argument("--ros-relay-port", type=int, default=8765)
    parser.add_argument("--public", action="store_true",
                        help="Cloudflare quick tunnel で公開用HTTPS URLを出す（キー付き、終了で停止）。"
                             "公開経由では過去ログとタブレットは使えない。")
    from pooh_narrative_dspy import add_storybook_arguments

    add_storybook_arguments(parser)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.crowdsourcing and args.scenario not in SUPPORTED_SCENARIOS:
        parser.error("--crowdsourcing は footprints / eeyore_birthday で使えます。")
    if args.end_after_minutes is not None and args.end_after_minutes <= 0:
        args.end_after_minutes = None
    args.passphrase = (args.passphrase or "").strip() or None
    if args.passphrase and not args.crowdsourcing:
        parser.error("--passphrase は --crowdsourcing と一緒に使います。")
    return args


def main() -> int:
    args = parse_args()
    access_token = resolve_access_token(os.getenv("POOH_WEB_TOKEN", ""), public=args.public)
    error = check_bind_security(args.host, access_token)
    if error is not None:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    cloudflared = None
    if args.public:
        from public_tunnel import find_cloudflared

        cloudflared = find_cloudflared()
        if cloudflared is None:
            print("エラー: --public には cloudflared が必要です（PATH か .tools/cloudflared。"
                  "README の「Publish the Web UI」を参照）。", file=sys.stderr)
            return 1
    try:
        factory, scenario_title, scene_intro, listeners = build_factory(args)
    except Exception as exc:
        # APIキーやLMリクエスト本文を含む可能性のある詳細tracebackは表示しない。
        print(f"エラー: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    tts = TTSCache(TTSConfig(server_url=args.tts_server_url.rstrip("/"))) if args.tts_server_url else None
    app = create_app(
        factory,
        ttl_seconds=args.session_ttl,
        max_sessions=args.max_sessions,
        access_token=access_token,
        scenario_title=scenario_title,
        scene_intro=scene_intro,
        local_output_listeners=listeners,
        crowdsourcing=args.crowdsourcing,
        passphrase=args.passphrase,
        end_after_minutes=args.end_after_minutes,
        tts=tts,
    )
    if args.crowdsourcing:
        print(f"合言葉: {args.passphrase}" if args.passphrase
              else "合言葉: なし（--passphrase で、完了コードの下に出せます）")
    suffix = f"/?token={access_token}" if access_token else "/"
    print(f"Web UI: http://{args.host}:{args.port}{suffix}")
    try:
        if cloudflared is None:
            web.run_app(app, host=args.host, port=args.port, print=None)
        else:
            asyncio.run(serve_public(app, args.host, args.port, cloudflared, suffix, args.scenario))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"エラー: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        # Let the picture book finish the page it is writing.
        for listener in listeners:
            listener.close()
    return 0


async def serve_public(
    app: web.Application, host: str, port: int, cloudflared: str, suffix: str,
    scenario: str = "",
) -> None:
    """Serve as usual, plus a public HTTPS address through a quick tunnel.

    The tunnel gets a loopback port of its own: requests arriving there are
    treated as coming from outside (no past logs, no tablet), even though
    cloudflared connects from 127.0.0.1. The tunnel ends with the server."""
    from public_tunnel import QuickTunnel

    tunnel_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tunnel_socket.bind(("127.0.0.1", 0))
    app[PUBLIC_PORT_KEY] = tunnel_socket.getsockname()[1]
    runner = web.AppRunner(app)
    await runner.setup()
    tunnel = QuickTunnel(cloudflared)
    link = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await web.TCPSite(runner, host, port).start()
        await web.SockSite(runner, tunnel_socket).start()
        print("公開用トンネルを起動しています…")
        url = await tunnel.start(app[PUBLIC_PORT_KEY])
        # The key is printed here only: not logged or written anywhere.
        print(f"公開URL: {url}{suffix}")
        link = await point_fixed_link(url, suffix, scenario)
        print("終了すると公開も止まります（Ctrl+C）。")
        stopped = asyncio.ensure_future(stop.wait())
        tunnel_ended = asyncio.ensure_future(tunnel.wait())
        await asyncio.wait({stopped, tunnel_ended}, return_when=asyncio.FIRST_COMPLETED)
        for task in (stopped, tunnel_ended):
            task.cancel()
        if not stop.is_set():
            # Serving on without it would look published while it is not.
            raise RuntimeError(
                f"公開トンネル（cloudflared）が終了しました（終了コード {tunnel_ended.result()}）。"
                "公開URLは使えないので、サーバも止めます。"
            )
    finally:
        if link is not None:
            await withdraw_fixed_link(*link)
        await tunnel.stop()
        await runner.cleanup()


async def point_fixed_link(url: str, suffix: str, scenario: str) -> tuple | None:
    """Point this scenario's fixed link (scripts/public_link.py) at this
    tunnel, if it is set up and CLOUDFLARE_API_TOKEN is given; returns what
    withdrawing it needs. A failure only warns: the tunnel's own address
    still works."""
    import public_link

    config = public_link.load_config()
    if config is None or not scenario:
        return None
    token = os.getenv(public_link.TOKEN_ENV, "")
    if not token:
        print(f"固定リンクは使いません（{public_link.TOKEN_ENV} が未設定）。")
        return None
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, public_link.publish, url, scenario, config, token)
    except Exception as exc:
        print(f"警告: 固定リンクを更新できませんでした: {exc}", file=sys.stderr)
        return None
    print(f"固定リンク: {public_link.scenario_link(config, scenario)}{suffix}"
          "（切り替わるまで1分ほどかかることがあります）")
    if not os.getenv("POOH_WEB_TOKEN"):
        print("注意: POOH_WEB_TOKEN が未設定なので、キー（?token=）は起動ごとに変わります。"
              "固定リンクを配るなら POOH_WEB_TOKEN を決めておいてください。")
    return scenario, config, token


async def withdraw_fixed_link(scenario: str, config: dict, token: str) -> None:
    import public_link

    try:
        await asyncio.get_running_loop().run_in_executor(
            None, public_link.withdraw, scenario, config, token)
    except Exception as exc:
        print(f"警告: 固定リンクの行き先を消せませんでした: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
