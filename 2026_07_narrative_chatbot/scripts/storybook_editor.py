#!/usr/bin/env python3
"""Correct what the speech recognizer heard, for the picture book.

    python scripts/storybook_editor.py            # http://127.0.0.1:8090/corrections

A page for the operator, on this PC only: pick a session log, retype what a
participant said where it was misheard, and the book is told again with the
corrected words.  The corrections are kept beside the log
(``<log name>_corrections.json``, see ``storybook_corrections``); the log
keeps what was heard.  On saving:

- the PDF of the book is saved again;
- the tablet's finished book, if it is this session's, gets the pages that
  changed: the chatbot's live book does it when it is still running (it
  watches the corrections file), and this editor itself when not;
- the postcards can be made again from the corrected words (a button: the
  LM picks the exchanges again, 20-40 s).

For the robot, which runs without the Web UI, this runs on its own (also
from ``scripts/run.py``); ``web_server.py`` serves the same page at
``/corrections`` to this PC.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent))

import storybook_corrections

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "web"
MAX_LOGS = 50

Guard = Callable[[web.Request], "web.Response | None"]

LOG_DIR_KEY = web.AppKey("corrections_log_dir", Path)
GUARD_KEY = web.AppKey("corrections_guard", object)
OPTIONS_KEY = web.AppKey("corrections_options", dict)
JOBS_KEY = web.AppKey("corrections_postcard_jobs", dict)


def default_log_dir() -> Path:
    return Path(os.getenv("POOH_LOG_DIR", str(PROJECT_ROOT / "logs")))


def resolve_log(log_dir: Path, name: str) -> Path | None:
    """One session log in ``log_dir`` by its name (no other path)."""
    if not (name.startswith("session_") and name.endswith(".jsonl")) or Path(name).name != name:
        return None
    candidate = log_dir / name
    if candidate.is_symlink() or not candidate.is_file():
        return None
    return candidate if candidate.resolve().parent == log_dir.resolve() else None


def read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def list_logs(log_dir: Path) -> list[dict[str, Any]]:
    if not log_dir.is_dir():
        return []
    paths = sorted(log_dir.glob("session_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    items = []
    for path in paths[:MAX_LOGS]:
        records = read_records(path)
        corrections = storybook_corrections.load(path)
        utterances = storybook_corrections.utterances(records, corrections)
        items.append({
            "name": path.name,
            "modified_at": path.stat().st_mtime,
            "scenario": str(records[0].get("scenario", "")) if records else "",
            "utterances": len(utterances),
            "corrected": sum(1 for u in utterances if u.corrected is not None),
        })
    return items


def log_detail(path: Path) -> dict[str, Any]:
    records = read_records(path)
    corrections = storybook_corrections.load(path)
    return {
        "name": path.name,
        "scenario": str(records[0].get("scenario", "")) if records else "",
        "on_tablet": tablet_has(path),
        "utterances": [{
            "utterance": u.utterance,
            "heard": u.heard,
            "said": u.said,
            "corrected": u.corrected,
            "pooh": u.pooh,
        } for u in storybook_corrections.utterances(records, corrections)],
    }


def tablet_has(path: Path) -> bool:
    from storybook_live import read_tablet_state

    state = read_tablet_state()
    return state is not None and state.get("log") == str(path.resolve())


def set_correction(path: Path, utterance: int, heard: str, text: str) -> None:
    """Keep (or, with ``text`` back to what was heard or empty, drop) the
    correction of one utterance; refused if the log no longer says ``heard``
    there."""
    records = read_records(path)
    found = storybook_corrections.utterances(records)
    if utterance < 0 or utterance >= len(found) or found[utterance].heard != heard:
        raise ValueError("その発言はログにありません（ログが変わりました）")
    corrections = storybook_corrections.load(path)
    corrections[utterance] = storybook_corrections.Correction(utterance, heard, text.strip())
    storybook_corrections.save(path, corrections)


def refresh_book(path: Path, eink: bool = True) -> str:
    """Bring the book of a corrected log up to date: the tablet's (when it
    has this book and no live book keeps it up to date) and the PDF.
    Returns what was done, for the page to say."""
    import storybook_live
    from storybook_render import build_from_log, default_pdf_path, write_pdf

    state = storybook_live.read_tablet_state()
    on_tablet = state is not None and state.get("log") == str(path.resolve())
    if on_tablet and storybook_live.process_alive(state.get("pid")):
        # The chatbot's live book (in this process too, under web_server.py)
        # watches the corrections file: it sends the changed pages and saves
        # the PDF itself.
        return "live"
    layout = state.get("layout") if on_tablet and state.get("layout") in storybook_live.LAYOUTS \
        else "landscape"
    builder = build_from_log(path, layout)
    write_pdf(builder, default_pdf_path(path))
    if not (on_tablet and eink):
        return "pdf"
    backend = storybook_live.EinkBackend()
    backend.layout = storybook_live.LAYOUTS[layout]
    # Every page: what the tablet has is not known here.
    storybook_live.push_corrected_book(backend, builder)
    return "tablet"


def remake_postcards(path: Path, paper: str) -> None:
    """The story's cards, then the epilogue's (if anything was said after
    the goal), from the corrected words."""
    from postcard_service import make_epilogue_postcard, make_postcards, postcard_path

    make_postcards(path, postcard_path(path), paper)
    make_epilogue_postcard(path, postcard_path(path), paper)


# ---------------------------------------------------------------------------
# HTTP


def _guarded(handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
    async def wrapped(request: web.Request) -> web.StreamResponse:
        guard = request.app[GUARD_KEY]
        refused = guard(request) if guard is not None else None
        if refused is not None:
            return refused
        return await handler(request)
    return wrapped


def _log(request: web.Request) -> Path | None:
    return resolve_log(request.app[LOG_DIR_KEY], request.match_info["name"])


async def page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_ROOT / "corrections.html", headers={"Cache-Control": "no-cache"})


async def api_logs(request: web.Request) -> web.Response:
    logs = await asyncio.to_thread(list_logs, request.app[LOG_DIR_KEY])
    return web.json_response({"logs": logs})


async def api_log(request: web.Request) -> web.Response:
    path = _log(request)
    if path is None:
        return web.json_response({"error": "log not found"}, status=404)
    detail = await asyncio.to_thread(log_detail, path)
    job = request.app[JOBS_KEY].get(path.name)
    if job is not None:
        detail["postcards"] = job
    return web.json_response(detail)


async def api_correct(request: web.Request) -> web.Response:
    path = _log(request)
    if path is None:
        return web.json_response({"error": "log not found"}, status=404)
    try:
        body = await request.json()
        utterance = int(request.match_info["utterance"])
        heard, text = str(body["heard"]), str(body.get("text", ""))
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "bad request"}, status=400)
    try:
        await asyncio.to_thread(set_correction, path, utterance, heard, text)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    options = request.app[OPTIONS_KEY]
    try:
        done = await asyncio.to_thread(refresh_book, path, options.get("eink", True))
    except Exception as exc:  # the correction is kept: the book catches up later
        print(f"[訂正] 絵本を更新できませんでした: {type(exc).__name__}: {exc}", file=sys.stderr)
        done = "error"
    detail = await asyncio.to_thread(log_detail, path)
    return web.json_response({**detail, "refreshed": done})


async def api_storybook_pdf(request: web.Request) -> web.StreamResponse:
    path = _log(request)
    if path is None:
        return web.json_response({"error": "log not found"}, status=404)
    layout = request.query.get("layout", "landscape")
    if layout not in ("landscape", "portrait"):
        return web.json_response({"error": "unknown layout"}, status=400)

    def build() -> bytes:
        import tempfile

        from storybook_render import build_from_log, write_pdf

        with tempfile.TemporaryDirectory() as directory:
            pdf = write_pdf(build_from_log(path, layout), Path(directory) / "book.pdf")
            return pdf.read_bytes()

    data = await asyncio.to_thread(build)
    return web.Response(body=data, content_type="application/pdf",
                        headers={"Cache-Control": "no-cache"})


async def api_postcards(request: web.Request) -> web.Response:
    path = _log(request)
    if path is None:
        return web.json_response({"error": "log not found"}, status=404)
    jobs = request.app[JOBS_KEY]
    if jobs.get(path.name, {}).get("status") == "making":
        return web.json_response(jobs[path.name])
    jobs[path.name] = {"status": "making"}
    paper = request.app[OPTIONS_KEY].get("paper", "l")

    def run() -> None:
        try:
            remake_postcards(path, paper)
            jobs[path.name] = {"status": "ready"}
        except Exception as exc:
            print(f"[訂正] ポストカードを作れませんでした: {type(exc).__name__}: {exc}", file=sys.stderr)
            jobs[path.name] = {"status": "failed", "error": str(exc)}

    threading.Thread(target=run, name="postcards", daemon=True).start()
    return web.json_response(jobs[path.name])


async def api_postcards_pdf(request: web.Request) -> web.StreamResponse:
    from postcard_service import postcard_path

    path = _log(request)
    pdf = postcard_path(path) if path is not None else None
    if pdf is None or not pdf.is_file():
        return web.json_response({"error": "no postcards"}, status=404)
    return web.FileResponse(pdf, headers={"Content-Type": "application/pdf",
                                          "Cache-Control": "no-cache"})


def add_routes(app: web.Application, log_dir: Path, *, guard: Guard | None = None,
               eink: bool = True, paper: str = "l") -> None:
    """The editor's page and API under ``/corrections``. ``guard`` refuses a
    request (returns the response) that may not correct: this PC only."""
    app[LOG_DIR_KEY] = log_dir
    app[GUARD_KEY] = guard
    app[OPTIONS_KEY] = {"eink": eink, "paper": paper}
    app[JOBS_KEY] = {}
    routes = [
        ("GET", "/corrections", page),
        ("GET", "/corrections/api/logs", api_logs),
        ("GET", "/corrections/api/logs/{name}", api_log),
        ("PUT", "/corrections/api/logs/{name}/utterances/{utterance}", api_correct),
        ("GET", "/corrections/api/logs/{name}/storybook.pdf", api_storybook_pdf),
        ("POST", "/corrections/api/logs/{name}/postcards", api_postcards),
        ("GET", "/corrections/api/logs/{name}/postcards.pdf", api_postcards_pdf),
    ]
    for method, route, handler in routes:
        app.router.add_route(method, route, _guarded(handler))


def local_only(request: web.Request) -> web.Response | None:
    import ipaddress

    try:
        if ipaddress.ip_address(request.remote or "").is_loopback:
            return None
    except ValueError:
        pass
    return web.json_response({"error": "local access only"}, status=403)


def create_app(log_dir: Path | None = None, *, eink: bool = True, paper: str = "l") -> web.Application:
    app = web.Application()
    add_routes(app, log_dir or default_log_dir(), guard=local_only, eink=eink, paper=paper)

    async def root(request: web.Request) -> web.Response:
        raise web.HTTPFound("/corrections")

    app.router.add_get("/", root)
    return app


class BackgroundEditor:
    """The editor served from a thread of its own, for the robot's chatbot
    (``pooh_narrative_dspy.py``), which has no Web UI: the operator opens it
    on this PC while the session runs."""

    def __init__(self, port: int, *, eink: bool = True, paper: str = "l") -> None:
        self.port = port
        self._loop = asyncio.new_event_loop()
        self._runner: web.AppRunner | None = None
        app = create_app(eink=eink, paper=paper)
        self._loop.run_until_complete(self._start(app))
        self._thread = threading.Thread(target=self._loop.run_forever, name="corrections",
                                        daemon=True)
        self._thread.start()

    async def _start(self, app: web.Application) -> None:
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()

    def close(self) -> None:
        if self._runner is not None:
            asyncio.run_coroutine_threadsafe(self._runner.cleanup(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(10)


def start_in_background(port: int, *, eink: bool = True, paper: str = "l") -> BackgroundEditor | None:
    """The editor on ``port``, or None (said why) if it cannot listen there,
    e.g. another chatbot or ``storybook_editor.py`` already serves it."""
    try:
        editor = BackgroundEditor(port, eink=eink, paper=paper)
    except OSError as exc:
        print(f"[訂正] 訂正の画面をポート {port} で開けませんでした: {exc}", file=sys.stderr)
        return None
    print(f"[訂正] 聞き間違いの訂正（この PC から）: http://127.0.0.1:{port}/corrections",
          file=sys.stderr)
    return editor


def main() -> int:
    parser = argparse.ArgumentParser(description="音声認識の聞き間違いを直して、絵本に反映する（この PC から）")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--log-dir", type=Path, default=None,
                        help="セッションログのディレクトリ（既定: POOH_LOG_DIR か logs/）")
    parser.add_argument("--no-eink", action="store_true",
                        help="タブレットには送らない（PDF だけ作り直す）")
    parser.add_argument("--postcard-size", choices=("hagaki", "l"), default="l",
                        help="作り直すポストカードの紙（既定: l）")
    args = parser.parse_args()
    app = create_app(args.log_dir, eink=not args.no_eink, paper=args.postcard_size)
    print(f"訂正: http://127.0.0.1:{args.port}/corrections")
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
