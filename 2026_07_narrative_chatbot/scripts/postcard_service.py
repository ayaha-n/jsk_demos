"""Make each session's postcards in the background once it has ended.

An output listener, like the picture book's: once a session reaches its
story's goal (crowdsourcing.reached_goal: footprints solved, Eeyore's gift
decided), or ends without it, it waits until that session's illustrations
are dealt with (drawn or given up on), so the cards can use the pictures
drawn from this very conversation, then makes the postcards the way
``scripts/postcard.py`` does from the log so far, and saves them next to the
log, as the live book saves its PDF (``<log dir>/postcards/<log name>.pdf``).
At the goal the open scenes are drawn right away (SessionIllustrations.flush),
so a robot session, which may go on until the participant says goodbye,
does not have to end first. Made once per session.
With ``--postcards`` (``web_server.py`` or the robot's
``pooh_narrative_dspy.py``) every session gets its postcards; the Web UI
offers them on this PC only (the session's own book dialog, and the past-log
viewer beside each book, robot sessions' too).
"""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from crowdsourcing import reached_goal

# How long to wait for a session's pictures before making the cards with
# whatever pictures there are (the book's own fallbacks).
ILLUSTRATION_WAIT_SEC = 600.0
POLL_SEC = 2.0


@dataclass
class PostcardJob:
    status: str  # "waiting" (for pictures) | "making" | "ready" | "failed"
    pdf: Path
    error: str = ""


def postcard_path(log_path: Path) -> Path:
    """Where a session's postcards are kept: beside its book (storybooks/)."""
    return log_path.parent / "postcards" / f"{log_path.stem}.pdf"


def make_postcards(log_path: Path, pdf: Path) -> str:
    """The postcards of a finished log, as scripts/postcard.py makes them by
    default; returns how the plan was made ("lm" or "extractive")."""
    import postcard
    from storybook_render import build_from_log

    builder = build_from_log(log_path, "portrait")
    scenes = postcard.scenes_from_book(builder)
    style = postcard.STYLES[postcard.DEFAULT_STYLE]
    source = "lm"
    try:
        plan, errors = postcard.lm_dialogue_plan(scenes, builder.config.title,
                                                 postcard.DEFAULT_CARDS, style)
    except Exception as exc:  # no API key, network, refusal: still make cards
        print(f"[ポストカード] LMを使えません（{type(exc).__name__}）。LMを使わずに作ります。",
              file=sys.stderr)
        plan, errors = [], ["LM unavailable"]
    if errors:
        plan, source = postcard.extractive_dialogue_plan(scenes, postcard.DEFAULT_CARDS, style), "extractive"
        errors = postcard.check_dialogue_plan(plan, scenes, postcard.DEFAULT_CARDS, style)
    postcard.write_pdf(plan, scenes, builder.config.title, pdf, style)
    pdf.with_suffix(".json").write_text(postcard.plan_to_json(plan, style, source, errors),
                                        encoding="utf-8")
    return source


class PostcardMaker:
    """Output listener: one postcard job per ended session."""

    def __init__(self, illustrations: Any = None,
                 make: Callable[[Path, Path], Any] = make_postcards,
                 wait_sec: float = ILLUSTRATION_WAIT_SEC, poll_sec: float = POLL_SEC) -> None:
        self.illustrations = illustrations
        self.make = make
        self.wait_sec = wait_sec
        self.poll_sec = poll_sec
        self.jobs: dict[str, PostcardJob] = {}
        self._lock = threading.Lock()
        self._closing = threading.Event()
        # One at a time: each is a few LM calls, and nobody is waiting live.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="postcards")

    def __call__(self, session: Any, output: Any) -> None:
        ended = (getattr(output, "source", "") == "session_close"
                 or getattr(output, "interaction_mode", "") == "exit")
        log_path = getattr(session, "log_path", None)
        if log_path is None or not Path(log_path).is_file():
            return  # nothing said yet
        session_id = session.session_id
        with self._lock:
            if session_id in self.jobs:
                return
        goal = not ended and reached_goal(session)
        if not ended and not goal:
            return
        with self._lock:
            if session_id in self.jobs:
                return
            self.jobs[session_id] = PostcardJob("waiting", postcard_path(Path(log_path)))
        if goal and hasattr(self.illustrations, "flush"):
            self.illustrations.flush(session_id)
        self._pool.submit(self._run, session_id, Path(log_path), goal)

    def status(self, session_id: str) -> PostcardJob | None:
        with self._lock:
            return self.jobs.get(session_id)

    def _set(self, session_id: str, status: str, error: str = "") -> None:
        with self._lock:
            job = self.jobs[session_id]
            job.status, job.error = status, error

    def _pictures_ready(self, session_id: str, goal: bool) -> bool:
        # At the goal the session goes on, so its scenes stay open: what was
        # asked for so far (the open ones flushed) is enough.
        ready = getattr(self.illustrations, "drawn" if goal else "settled", None)
        return ready is None or ready(session_id)

    def _run(self, session_id: str, log_path: Path, goal: bool = False) -> None:
        deadline = time.monotonic() + self.wait_sec
        while not self._pictures_ready(session_id, goal) and time.monotonic() < deadline:
            if self._closing.wait(self.poll_sec):
                break  # closing: the pictures are as done as they will be
        self._set(session_id, "making")
        pdf = self.jobs[session_id].pdf
        try:
            source = self.make(log_path, pdf)
        except Exception as exc:
            self._set(session_id, "failed", f"{type(exc).__name__}: {exc}")
            print(f"[ポストカード] {log_path.name}: 作れませんでした: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return
        self._set(session_id, "ready")
        print(f"[ポストカード] {log_path.name}: 作りました（{source}）: {pdf}", file=sys.stderr)

    def close(self) -> None:
        """Make the cards of every session that has ended, without waiting
        for pictures any longer (close the illustrations first), then stop.
        A robot run ends right after its session, so its cards are made here."""
        with self._lock:
            pending = sum(job.status in ("waiting", "making") for job in self.jobs.values())
        if pending:
            print(f"[ポストカード] 作り終えるまで待っています…（{pending}件）", file=sys.stderr)
        self._closing.set()
        self._pool.shutdown(wait=True)
