"""Optional study progress and durable submission receipts for the Web UI.

Reading events are browser reports, not proof that a participant read the text.
Submission codes are independent of the capability used to access a session.
Each is 64 random bits, so no two sessions get the same one in practice; its
receipt (``<session_id>.json``) ties it to the session's log. A passphrase,
the same for everyone in one study run, can be shown beside it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUPPORTED_SCENARIOS = frozenset({"footprints", "eeyore_birthday"})
IDLE_CLOSE_EVENTS = frozenset({"idle_close_after_wrap_up", "idle_close_after_reveal"})


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def reached_goal(session: Any) -> bool:
    key = getattr(getattr(session, "scenario", None), "key", None)
    state = getattr(getattr(session, "controller", None), "state", None)
    if key == "footprints":
        return bool(getattr(state, "resolved", False))
    if key == "eeyore_birthday":
        return "story_wrap_up" in getattr(state, "completed_event_ids", ())
    return False


def book_revision(book: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(book, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def book_ready(book: dict[str, Any]) -> bool:
    pages = book.get("pages", [])
    return bool(pages) and all(not p.get("illustration") or p.get("illustration_url") for p in pages)


@dataclass
class Progress:
    session_id: str
    scenario: str | None
    log_name: str | None
    started_at: str
    goal_reached_at: str | None = None
    # When ending was allowed by time (Web UI --end-after-minutes) before
    # the goal; ended_before_goal still says whether the goal was reached.
    end_allowed_at: str | None = None
    ended_at: str | None = None
    ended_before_goal: bool | None = None
    book_revision: str | None = None
    book_opened_at: str | None = None
    last_page_reported_at: str | None = None
    read_confirmed_at: str | None = None
    completion_code: str | None = None
    # The passphrase shown with the code (kept, so a reload shows the same
    # one even if the server was restarted with another).
    passphrase: str | None = None

    def public(self) -> dict[str, Any]:
        return {"enabled": True, "goal_reached": self.goal_reached_at is not None,
                "end_allowed": self.end_allowed,
                "ended_before_goal": self.ended_before_goal,
                "book_revision": self.book_revision,
                "book_opened_at": self.book_opened_at,
                "last_page_reported_at": self.last_page_reported_at,
                "read_confirmed_at": self.read_confirmed_at,
                "completion_code": self.completion_code,
                "passphrase": self.passphrase if self.completion_code else None}

    @property
    def end_allowed(self) -> bool:
        """Whether the participant may end with the button (goal or time)."""
        return self.goal_reached_at is not None or self.end_allowed_at is not None

    def report(self, event: str, revision: str, passphrase: str | None = None) -> None:
        if self.completion_code:
            return
        if event == "book_opened":
            if self.book_revision != revision:
                self.book_revision = revision
                self.book_opened_at = self.book_opened_at or timestamp()
                self.last_page_reported_at = None
            return
        if self.book_revision != revision or not self.book_opened_at:
            raise ValueError("絵本を開いてから、最後のページまで進んでください。")
        if event == "last_page":
            self.last_page_reported_at = self.last_page_reported_at or timestamp()
        elif event == "read_confirmed":
            if not self.last_page_reported_at:
                raise ValueError("最後のページまで進んでから読了を確認してください。")
            self.read_confirmed_at = timestamp()
            code = secrets.token_hex(8).upper()
            self.completion_code = "-".join(code[i:i + 4] for i in range(0, len(code), 4))
            self.passphrase = passphrase or None
        else:
            raise ValueError("unknown reading event")


class ProgressStore:
    def __init__(self, directory: Path, passphrase: str | None = None):
        self.directory = directory
        # Shown with each completion code issued from now on.
        self.passphrase = passphrase

    def path(self, session_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("invalid session id")
        return self.directory / f"{session_id}.json"

    def load(self, session_id: str) -> Progress | None:
        path = self.path(session_id)
        if not path.exists():
            return None
        return Progress(**json.loads(path.read_text(encoding="utf-8")))

    def save(self, progress: Progress) -> None:
        target = self.path(progress.session_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".progress-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(progress), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)
