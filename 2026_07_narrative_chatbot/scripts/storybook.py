"""Turn a narrative session into picture-book pages.

The same ``StoryBuilder`` serves the live e-ink book (``storybook_live``) and
the after-the-fact review (``storybook_render`` and the Web log viewer), so a
session reads the same in both.  It only retells what the session recorded:
the participant's words, Pooh's lines, the body motion of fixed lines, and
the scripted world events.  Nothing here asks a model to invent narration.

Pages break at the scenario's story beats (the opening and the timed world
events listed in ``config/storybook.json``), each with its illustration, and
when the text no longer fits.  The page geometry matches the
plush_memory_viewer on the reMarkable Paper Pro, which writes the text by
hand with the Yomogi font: lines are wrapped here, measured with the same
font, so the viewer never has to re-wrap them.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from narrative_events import fixed_utterance_id, fixed_utterance_motion

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORYBOOK_CONFIG_PATH = PROJECT_ROOT / "config" / "storybook.json"
# Illustrations, at their paths in config/storybook.json
# (e.g. "eeyore_birthday/opening.png").
ILLUSTRATION_ROOT = PROJECT_ROOT / "assets" / "storybook"
PLUSH_MEMORY_DIR = Path(
    os.getenv("PLUSH_MEMORY_DIR", str(PROJECT_ROOT.parents[1] / "plush_memory"))
)
FONT_PATH = PLUSH_MEMORY_DIR / "eink-viewer" / "fonts" / "Yomogi-Regular.ttf"

# The viewer's handwriting geometry (eink-viewer/src/ink.rs, main.rs, and
# eink_memory_push.TEXT_PARAGRAPH_GAP); positions computed here must agree
# with where the viewer itself continues a multi-paragraph text event.
LINE_SPACING = 1.5
PARAGRAPH_GAP = 0.6
BODY_PX = 56
TITLE_PX = 80
CLOSING_MARGIN = 40
# A passage too long for the rest of the page starts there only if at least
# this many of its lines fit; otherwise it moves to the next page whole.
MIN_SPLIT_LINES = 3

# Kinsoku: characters that must not begin, or end, a line.
NO_LINE_START = set("、。，．・：；？！ー～」』）】〉》’”ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ…‥")
NO_LINE_END = set("「『（【〈《‘“")

Measure = Callable[[str, int], float]


# ---------------------------------------------------------------------------
# Configuration


@dataclass(frozen=True)
class StoryBeat:
    beat_id: str
    narration: str = ""
    illustration: str | None = None
    prompt: str = ""


@dataclass(frozen=True)
class StoryConfig:
    scenario: str
    title: str
    beats: dict[str, StoryBeat]
    closing: str
    motion_phrases: dict[str, dict[str, Any]]
    illustration_style: str = ""
    participant_presence: str = ""

    def illustration_path(self, beat_id: str | None) -> Path | None:
        beat = self.beats.get(beat_id or "")
        if beat is None or not beat.illustration:
            return None
        return find_illustration(beat.illustration)


def find_illustration(name: str) -> Path | None:
    """The PNG at ``name`` under ``ILLUSTRATION_ROOT``, never outside it."""
    root = ILLUSTRATION_ROOT.resolve()
    candidate = (root / name).resolve()
    if root in candidate.parents and candidate.suffix == ".png" and candidate.is_file():
        return candidate
    return None


def load_story_config(scenario: str, path: Path = STORYBOOK_CONFIG_PATH) -> StoryConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    spec = data["scenarios"].get(scenario, {})
    beats = {
        beat_id: StoryBeat(beat_id=beat_id, **beat)
        for beat_id, beat in spec.get("beats", {}).items()
    }
    return StoryConfig(
        scenario=scenario,
        title=spec.get("title", ""),
        beats=beats,
        closing=spec.get("closing", "おしまい"),
        motion_phrases=data.get("motion_phrases", {}),
        illustration_style=data.get("illustration_style", ""),
        participant_presence=data.get("participant_presence", ""),
    )


# ---------------------------------------------------------------------------
# What happened in one session step, from a live output or a log record


@dataclass(frozen=True)
class StoryTurn:
    source: str  # session_open / participant / world_event / session_close
    pooh_text: str
    user_text: str = ""
    world_event_id: str | None = None
    motion: dict[str, str] = field(default_factory=dict)
    ends_story: bool = False


def turn_from_output(output: Any) -> StoryTurn:
    """From a ``pooh_narrative_dspy.SessionOutput``."""
    turn = getattr(output, "turn", None)
    return StoryTurn(
        source=output.source,
        pooh_text=output.bot_response,
        user_text=getattr(turn, "user_action", "") if turn is not None else "",
        world_event_id=output.world_event_id,
        motion=dict(output.motion),
        ends_story=output.source == "session_close" or output.interaction_mode == "exit",
    )


def turn_from_record(record: dict[str, Any]) -> StoryTurn:
    """From one session log line.  Older logs did not store the fixed line's
    id; it is recovered from the text, as the session itself does."""
    text = str(record.get("bot_response", ""))
    utterance_id = record.get("fixed_utterance_id") or fixed_utterance_id(text)
    source = str(record.get("source", "participant"))
    return StoryTurn(
        source=source,
        pooh_text=text,
        user_text=str(record.get("user_action", "")),
        world_event_id=record.get("world_event_id"),
        motion=fixed_utterance_motion(utterance_id) if utterance_id else {},
        ends_story=source == "session_close" or record.get("interaction_mode") == "exit",
    )


def turns_from_log(records: Iterable[dict[str, Any]], opening_line: str) -> list[StoryTurn]:
    """Log lines start after the opening, which is the scenario's fixed line."""
    opening_id = fixed_utterance_id(opening_line)
    turns = [StoryTurn(
        source="session_open",
        pooh_text=opening_line,
        motion=fixed_utterance_motion(opening_id) if opening_id else {},
    )]
    turns.extend(turn_from_record(record) for record in records)
    return turns


# ---------------------------------------------------------------------------
# Narration


@dataclass(frozen=True)
class StoryEntry:
    style: str  # "title" | "narration" | "speech" | "closing"
    text: str
    speaker: str = ""  # "pooh" | "participant" for speech


def motion_sentence(motion: dict[str, str], phrases: dict[str, dict[str, Any]]) -> str:
    """「プーは うれしそうな かおで、みぎてを あげました。」 from a motion."""
    face = phrases.get("eyebrow", {}).get(motion.get("eyebrow", ""), "")
    actions = [
        phrases.get(part, {}).get(motion.get(part, ""))
        for part in ("arm", "neck", "nose")
    ]
    actions = [action for action in actions if action]
    if not face and not actions:
        return ""
    if not actions:
        # 「うれしそうな かおで」 alone: 「プーは うれしそうな かおを しました。」
        return f"プーは {face.removesuffix('で')}を しました。"
    chain = [action[0] for action in actions[:-1]] + [actions[-1][1]]
    lead = f"プーは {face}、" if face else "プーは "
    return lead + "、".join(chain) + "。"


def narrate(turn: StoryTurn, config: StoryConfig) -> tuple[str | None, list[StoryEntry]]:
    """The beat this turn opens (if any) and the entries that retell it."""
    entries: list[StoryEntry] = []
    beat_id: str | None = None
    if turn.source == "session_open":
        beat_id = "opening"
        if config.title:
            entries.append(StoryEntry("title", config.title))
    elif turn.source == "world_event" and turn.world_event_id in config.beats:
        beat_id = turn.world_event_id
    beat = config.beats.get(beat_id or "")
    if beat is not None and beat.narration:
        entries.append(StoryEntry("narration", beat.narration))
    if turn.user_text:
        entries.append(StoryEntry(
            "speech", f"「{turn.user_text}」と、あなたは いいました。", "participant"
        ))
    sentence = motion_sentence(turn.motion, config.motion_phrases)
    if sentence:
        entries.append(StoryEntry("narration", sentence))
    if turn.pooh_text:
        entries.append(StoryEntry("speech", f"「{turn.pooh_text}」", "pooh"))
    return beat_id, entries


# ---------------------------------------------------------------------------
# Page geometry and line wrapping


Box = tuple[int, int, int, int]  # x, y, w, h


@dataclass(frozen=True)
class PageLayout:
    name: str  # "portrait" / "landscape", as the viewer reports it
    size: tuple[int, int]
    picture_box: Box
    # Text columns, filled in order: beside or below the picture, and on a
    # continuation page without one.
    text_columns: tuple[Box, ...]
    plain_text_columns: tuple[Box, ...]


LAYOUTS = {
    # Picture on top, text below, like most picture books held upright.
    "portrait": PageLayout(
        "portrait", (1620, 2160),
        picture_box=(160, 70, 1300, 1000),
        text_columns=((90, 1130, 1440, 960),),
        plain_text_columns=((90, 110, 1440, 1950),),
    ),
    # The open-book spread: picture on the left page, text on the right;
    # without a picture, the text runs on from the left page to the right.
    "landscape": PageLayout(
        "landscape", (2160, 1620),
        picture_box=(60, 120, 980, 1380),
        text_columns=((1120, 100, 980, 1440),),
        plain_text_columns=((60, 100, 980, 1440), (1120, 100, 980, 1440)),
    ),
}


def line_height(px: int) -> int:
    return int(px * LINE_SPACING)


def paragraph_gap(px: int) -> int:
    return int(PARAGRAPH_GAP * px)


# Yomogi's ascender + descender, in ems.  The viewer's ``px`` (ab_glyph's
# PxScale) is that whole height, while PIL's font size is the em, so text the
# viewer writes at ``px`` has an em of ``px / YOMOGI_HEIGHT_EM``.  This
# constant stands in only when the font file is missing.
YOMOGI_HEIGHT_EM = 1.448


@lru_cache(maxsize=1)
def _height_em() -> float:
    from PIL import ImageFont

    ascent, descent = ImageFont.truetype(str(FONT_PATH), 1000).getmetrics()
    return (ascent + descent) / 1000


@lru_cache(maxsize=16)
def _font(px: int) -> Any:
    """The handwriting font at the size the viewer writes ``px`` text."""
    from PIL import ImageFont

    return ImageFont.truetype(str(FONT_PATH), px / _height_em())


def default_measure(text: str, px: int) -> float:
    """Advance width as the viewer measures it, or a full-width estimate
    when plush_memory's font is not available."""
    if FONT_PATH.is_file():
        return float(_font(px).getlength(text))
    return px / YOMOGI_HEIGHT_EM * len(text)


def wrap_text(text: str, px: int, width: int, measure: Measure = default_measure) -> list[str]:
    """Greedy wrap that keeps the text's own line breaks (the viewer starts a
    new line at each one too), prefers the spaces of 分かち書き, otherwise
    breaks between any two characters, honoring kinsoku."""
    lines: list[str] = []
    for paragraph in text.strip().splitlines():
        lines.extend(_wrap_paragraph(paragraph, px, width, measure))
    return lines


def _wrap_paragraph(text: str, px: int, width: int, measure: Measure) -> list[str]:
    lines: list[str] = []
    rest = text.strip()
    while rest:
        if measure(rest, px) <= width:
            lines.append(rest)
            break
        fit = 1
        while fit < len(rest) and measure(rest[:fit + 1], px) <= width:
            fit += 1
        cut = fit
        space = rest.rfind(" ", 0, fit + 1)
        if space > fit // 2:
            cut = space
        else:
            # Pull the break back so a closing mark never starts a line and
            # an opening bracket never ends one.
            while cut > 1 and (rest[cut] in NO_LINE_START or rest[cut - 1] in NO_LINE_END):
                cut -= 1
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return lines


@dataclass
class PlacedEntry:
    entry: StoryEntry
    lines: list[str]
    x: int
    y: int
    w: int
    px: int
    align: str | None = None

    @property
    def bottom(self) -> int:
        return self.y + len(self.lines) * line_height(self.px)


@dataclass
class StoryPage:
    index: int
    beat_id: str | None
    illustration: str | None  # found with find_illustration()
    text_columns: tuple[Box, ...]
    placed: list[PlacedEntry] = field(default_factory=list)
    column: int = 0  # the column being filled
    cursor: int | None = None  # bottom of its last entry; None at its top

    @property
    def text_box(self) -> Box:
        return self.text_columns[self.column]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "beat_id": self.beat_id,
            "illustration": self.illustration,
            "entries": [
                {**asdict(item.entry), "lines": item.lines, "x": item.x, "y": item.y,
                 "w": item.w, "px": item.px, "align": item.align}
                for item in self.placed
            ],
        }


@dataclass(frozen=True)
class NewPage:
    page: StoryPage


@dataclass(frozen=True)
class WriteEntries:
    page: StoryPage
    placed: list[PlacedEntry]


PageEvent = NewPage | WriteEntries


class StoryBuilder:
    """Lays out a session's turns onto pages, one turn at a time."""

    def __init__(
        self,
        config: StoryConfig,
        layout: PageLayout = LAYOUTS["portrait"],
        measure: Measure = default_measure,
    ) -> None:
        self.config = config
        self.layout = layout
        self.measure = measure
        self.pages: list[StoryPage] = []
        self.finished = False

    def add(self, turn: StoryTurn) -> list[PageEvent]:
        if self.finished:
            return []
        beat_id, entries = narrate(turn, self.config)
        events: list[PageEvent] = []
        if not self.pages or (beat_id is not None and not self._continue_in_next_column(beat_id)):
            events.append(self._new_page(beat_id))
        events.extend(self._place(entries))
        if turn.ends_story:
            events.extend(self.finish())
        return events

    def finish(self) -> list[PageEvent]:
        if self.finished or not self.pages:
            self.finished = True
            return []
        self.finished = True
        return self._place([StoryEntry("closing", self.config.closing)])

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.config.scenario,
            "title": self.config.title,
            "layout": self.layout.name,
            "size": list(self.layout.size),
            "picture_box": list(self.layout.picture_box),
            "pages": [page.to_dict() for page in self.pages],
        }

    def _continue_in_next_column(self, beat_id: str) -> bool:
        """A new scene without a picture to show starts in the next, still
        empty column (the right page of a spread) instead of turning the
        page, so the spread is not left half blank."""
        page = self.pages[-1]
        if self.config.illustration_path(beat_id) is not None:
            return False
        if page.column + 1 >= len(page.text_columns):
            return False
        if page.cursor is not None:
            page.column += 1
            page.cursor = None
        return True

    def _new_page(self, beat_id: str | None) -> NewPage:
        beat = self.config.beats.get(beat_id or "")
        # Until a beat's illustration exists, its page is all text, rather
        # than a blank half on the tablet.
        has_picture = self.config.illustration_path(beat_id) is not None
        page = StoryPage(
            index=len(self.pages),
            beat_id=beat_id,
            illustration=beat.illustration if has_picture else None,
            text_columns=(
                self.layout.text_columns if has_picture else self.layout.plain_text_columns
            ),
        )
        self.pages.append(page)
        return NewPage(page)

    def _place(self, entries: list[StoryEntry]) -> list[PageEvent]:
        events: list[PageEvent] = []
        batch: list[PlacedEntry] = []

        def put(page: StoryPage, placed: PlacedEntry) -> None:
            page.placed.append(placed)
            page.cursor = placed.bottom
            batch.append(placed)

        for entry in entries:
            while True:
                page = self.pages[-1]
                placed = self._fit(page, entry)
                if placed is not None:
                    put(page, placed)
                    break
                head = self._split(page, entry)
                if head is not None:
                    # A long passage starts here and runs on in the next
                    # column or overleaf.
                    put(page, head)
                    rest = wrap_text(entry.text, head.px, head.w, self.measure)[len(head.lines):]
                    # 分かち書き broke at its spaces; speech broke between characters.
                    joiner = " " if " " in entry.text else ""
                    entry = StoryEntry(entry.style, joiner.join(rest), entry.speaker)
                elif page.cursor is None:
                    # Too long for an empty column and not splittable.
                    put(page, self._fit(page, entry, force=True))
                    break
                if page.column + 1 < len(page.text_columns):
                    page.column += 1
                    page.cursor = None
                    continue
                if batch:
                    events.append(WriteEntries(page, batch))
                    batch = []
                events.append(self._new_page(None))
        if batch:
            events.append(WriteEntries(self.pages[-1], batch))
        return events

    def _split(self, page: StoryPage, entry: StoryEntry) -> PlacedEntry | None:
        """The part of a long passage that fills the rest of this column, if
        enough room is left to be worth starting it here."""
        if entry.style not in ("speech", "narration"):
            return None
        x, top, w, h = page.text_box
        px = BODY_PX
        y = top if page.cursor is None else page.cursor + paragraph_gap(px)
        room = (top + h - y) // line_height(px)
        lines = wrap_text(entry.text, px, w, self.measure)
        if room < MIN_SPLIT_LINES or len(lines) - room < 1:
            return None
        return PlacedEntry(entry, lines[:room], x, y, w, px)

    def _fit(self, page: StoryPage, entry: StoryEntry, force: bool = False) -> PlacedEntry | None:
        x, top, w, h = page.text_box
        px = TITLE_PX if entry.style == "title" else BODY_PX
        align = "center" if entry.style in ("title", "closing") else None
        y = top if page.cursor is None else page.cursor + paragraph_gap(px)
        lines = wrap_text(entry.text, px, w, self.measure)
        bottom = y + len(lines) * line_height(px)
        if entry.style == "closing":
            # 「おしまい」 may use the bottom margin rather than turn the page.
            h = self.layout.size[1] - CLOSING_MARGIN - top
        if bottom > top + h and not force:
            return None
        if bottom > top + h:
            # Longer than a whole column: keep what fits; the session log
            # keeps the full line.
            keep = max(1, (top + h - y) // line_height(px))
            lines = lines[:keep - 1] + [lines[keep - 1].rstrip("。") + "…"]
        return PlacedEntry(entry, lines, x, y, w, px, align)


def build_book(
    records: Iterable[dict[str, Any]],
    *,
    scenario: str,
    opening_line: str,
    layout: str = "portrait",
    measure: Measure = default_measure,
) -> StoryBuilder:
    """The whole book of a finished (or interrupted) session log."""
    builder = StoryBuilder(load_story_config(scenario), LAYOUTS[layout], measure)
    for turn in turns_from_log(records, opening_line):
        builder.add(turn)
    builder.finish()
    return builder
