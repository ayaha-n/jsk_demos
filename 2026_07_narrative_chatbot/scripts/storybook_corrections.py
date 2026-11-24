"""Corrections to what the speech recognizer heard, kept beside a session log.

The log stays the record of what was heard; the operator's corrections go in
``<log name>_corrections.json`` next to it, and everything made from the log
afterwards (the picture book on the Web and as a PDF, the postcards, the
pictures generated from the conversation) reads the log through
``apply_corrections``, so they all tell the corrected words.

A correction names the participant's utterance by its place among the
session's utterances (0, 1, …: the log lines with a non-empty
``user_action``, in order) and keeps the text it replaces: it applies only
while that utterance still says so, so a correction never lands on another
line.  Only what was said is corrected: for 「「どうぞ」と言って、風船をプーに
手渡す」 (speech with a handover the robot saw), the part in 「」.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SUFFIX = "_corrections.json"
SPOKEN_LEAD = "「"
SPOKEN_TAIL = "」と言って、"


def corrections_path(log_path: Path) -> Path:
    return log_path.with_name(f"{log_path.stem}{SUFFIX}")


@dataclass(frozen=True)
class Correction:
    utterance: int  # its place among the session's utterances
    original: str  # the user_action it corrects, as logged
    text: str  # what was said, corrected

    def to_dict(self) -> dict[str, Any]:
        return {"utterance": self.utterance, "original": self.original, "text": self.text}


def spoken_part(user_action: str) -> str:
    """What was said: the part in 「」 of 「「どうぞ」と言って、…」."""
    if user_action.startswith(SPOKEN_LEAD) and SPOKEN_TAIL in user_action:
        return user_action[len(SPOKEN_LEAD):user_action.index(SPOKEN_TAIL)]
    return user_action


def with_spoken(user_action: str, text: str) -> str:
    """``user_action`` saying ``text`` instead, the rest kept."""
    if user_action.startswith(SPOKEN_LEAD) and SPOKEN_TAIL in user_action:
        return SPOKEN_LEAD + text + user_action[user_action.index(SPOKEN_TAIL):]
    return text


def load(log_path: Path) -> dict[int, Correction]:
    """The corrections kept for a log (none if there is no file, or it is
    unreadable: the log is then told as it was heard)."""
    path = corrections_path(log_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"[訂正] {path.name} を読めませんでした: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return {}
    found: dict[int, Correction] = {}
    for item in data.get("corrections", []) if isinstance(data, dict) else []:
        try:
            correction = Correction(int(item["utterance"]), str(item["original"]), str(item["text"]))
        except (KeyError, TypeError, ValueError):
            continue
        found[correction.utterance] = correction
    return found


def save(log_path: Path, corrections: dict[int, Correction]) -> Path:
    """Write the corrections in one go (a reader never sees half a file).
    A correction back to what was heard is dropped."""
    path = corrections_path(log_path)
    kept = [c for _, c in sorted(corrections.items())
            if c.text.strip() and c.text != spoken_part(c.original)]
    data = {
        "log": log_path.name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "corrections": [c.to_dict() for c in kept],
    }
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return path


@dataclass(frozen=True)
class Utterance:
    utterance: int
    record_index: int
    heard: str  # the logged user_action
    said: str  # what was said, as heard (spoken_part)
    corrected: str | None  # the correction's text, if one applies
    pooh: str  # Pooh's answer to it


def utterances(records: Iterable[dict[str, Any]],
               corrections: dict[int, Correction] | None = None) -> list[Utterance]:
    """The participant's utterances in a log, for the editor."""
    corrections = corrections or {}
    found = []
    for index, record in enumerate(records):
        heard = str(record.get("user_action") or "")
        if not heard:
            continue
        number = len(found)
        correction = corrections.get(number)
        corrected = correction.text if correction is not None and correction.original == heard else None
        found.append(Utterance(number, index, heard, spoken_part(heard), corrected,
                               str(record.get("bot_response") or "")))
    return found


def apply_corrections(records: Iterable[dict[str, Any]],
                      corrections: dict[int, Correction]) -> list[dict[str, Any]]:
    """The log's records with the corrected words in ``user_action`` (the
    heard words kept in ``heard_user_action``)."""
    records = list(records)
    if not corrections:
        return records
    out = []
    number = 0
    for record in records:
        heard = str(record.get("user_action") or "")
        if heard:
            correction = corrections.get(number)
            number += 1
            if correction is not None and correction.original == heard:
                record = {**record, "user_action": with_spoken(heard, correction.text),
                          "heard_user_action": heard}
        out.append(record)
    return out


def apply_to_turns(turns: Iterable[Any], corrections: dict[int, Correction]) -> list[Any]:
    """The same for ``storybook.StoryTurn``s (the live book, which has the
    session's turns rather than its log)."""
    turns = list(turns)
    if not corrections:
        return turns
    out = []
    number = 0
    for turn in turns:
        heard = turn.user_text
        if heard:
            correction = corrections.get(number)
            number += 1
            if correction is not None and correction.original == heard:
                turn = replace(turn, user_text=with_spoken(heard, correction.text))
        out.append(turn)
    return out


def read_corrected_log(log_path: Path) -> list[dict[str, Any]]:
    """A session log's records, as corrected."""
    records = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return apply_corrections(records, load(log_path))
