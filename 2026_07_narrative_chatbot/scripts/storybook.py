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
import hashlib
import os
import random
import sys
from dataclasses import asdict, dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from narrative_events import fixed_utterance_id, fixed_utterance_motion

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORYBOOK_CONFIG_PATH = PROJECT_ROOT / "config" / "storybook.json"
# Illustrations, at their paths in config/storybook.json
# (e.g. "eeyore_birthday/opening.png").
ILLUSTRATION_ROOT = PROJECT_ROOT / "assets" / "storybook"
FIXED_ILLUSTRATION_BEATS = frozenset({"opening", "pooh_ate_honey"})
# Generated when the scene starts rather than once it is over: what it shows
# (the gift decided in the story state) is known by then, and the scene runs
# on to the end of the session.
ILLUSTRATED_AT_START_BEATS = frozenset({"story_wrap_up"})
CLOSING_FALLBACK_BEATS = frozenset({"story_wrap_up"})
PLUSH_MEMORY_DIR = Path(
    os.getenv("PLUSH_MEMORY_DIR", str(PROJECT_ROOT.parents[1] / "plush_memory"))
)
# The tablet's handwriting font, bundled (assets/fonts/README.md): the same
# file plush_memory's viewer writes with, so PDFs and the Web book lay lines
# out as the tablet does, without plush_memory.
FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "Yomogi-Regular.ttf"
VIEWER_FONT_PATH = PLUSH_MEMORY_DIR / "eink-viewer" / "fonts" / "Yomogi-Regular.ttf"

# The viewer's handwriting geometry (eink-viewer/src/ink.rs, main.rs, and
# eink_memory_push.TEXT_PARAGRAPH_GAP); positions computed here must agree
# with where the viewer itself continues a multi-paragraph text event.
LINE_SPACING = 1.5
PARAGRAPH_GAP = 0.6
BODY_PX = 56
TITLE_PX = 80
CLOSING_MARGIN = 40
# The cover, after plush_memory's (scripts/eink_hook.py): a hand-drawn
# double frame just inside the panel edge, the title written large with a
# broad pen, the picture under it, and 「めくる→」 in the frame's
# bottom-right corner, since the tablet ends a book on its cover and a tap
# on the right turns to the first page.
COVER_HINT = "めくる→"
COVER_INNER_FRAME_INSET = 16  # the frame's inner line, inside its outer one
COVER_PADDING = 40  # between the inner line and what is inside it
COVER_TITLE_PEN_R = 4
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
    reference_illustration: str | None = None
    illustration_variants: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StoryConfig:
    scenario: str
    title: str
    beats: dict[str, StoryBeat]
    closing: str
    motion_phrases: dict[str, dict[str, Any]]
    illustration_style: str = ""
    participant_presence: str = ""
    session_id: str = ""
    # The cover picture, a point inside its empty thought bubble (in the
    # picture's pixels), and the beat whose picture is drawn into it.
    cover_illustration: str | None = None
    cover_bubble: tuple[int, int] | None = None
    cover_bubble_beat: str | None = None

    def illustration_name(self, beat_id: str | None) -> str | None:
        beat = self.beats.get(beat_id or "")
        if beat is None:
            return None
        if self.session_id and beat_id not in FIXED_ILLUSTRATION_BEATS:
            name = generated_illustration_name(self.scenario, self.session_id, beat.beat_id)
            metadata = (ILLUSTRATION_ROOT / name).with_suffix(".scene.json")
            saved = {}
            try:
                saved = json.loads(metadata.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    saved = {}
                if saved.get("illustration_needed") is False:
                    return self._closing_fallback(beat, saved) if beat_id in CLOSING_FALLBACK_BEATS else None
            except (OSError, ValueError, AttributeError):
                pass
            if find_illustration(name):
                return name
            fallback = saved.get("fallback_illustration")
            if isinstance(fallback, str) and fallback.startswith("generated/") and find_illustration(fallback):
                return fallback
            if beat_id in CLOSING_FALLBACK_BEATS:
                return self._closing_fallback(beat, saved)
        variants = [name for name in beat.illustration_variants if find_illustration(name)]
        if variants:
            # A session keeps the same random choice across Web/PDF/reopening.
            seed = f"{self.scenario}:{self.session_id or id(self)}:{beat.beat_id}"
            return random.Random(seed).choice(variants)
        return beat.illustration

    def _closing_fallback(self, beat: StoryBeat, metadata: dict) -> str | None:
        if self.scenario == "eeyore_birthday" and metadata.get("balloon_color"):
            name = "eeyore_birthday/story_wrap_up.png"
            if find_illustration(name):
                return name
        return beat.illustration

    def illustration_path(self, beat_id: str | None) -> Path | None:
        name = self.illustration_name(beat_id)
        return find_illustration(name) if name else None


def generated_illustration_name(scenario: str, session_id: str, beat_id: str) -> str:
    # Opaque names also keep session identifiers and arbitrary path components
    # out of the public illustration URL.
    key = hashlib.sha256(json.dumps([scenario, session_id, beat_id]).encode()).hexdigest()
    return f"generated/{key}.png"


def generated_fallback_candidates(scenario: str, beat_id: str) -> list[dict[str, str]]:
    """Saved pictures with their actual successful scene descriptions/prompts."""
    candidates: set[str] = set()
    for metadata_path in (ILLUSTRATION_ROOT / "generated").glob("*.scene.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("scenario") == scenario and metadata.get("beat_id") == beat_id:
                candidates.add("generated/" + metadata_path.name.removesuffix(".scene.json") + ".png")
        except (OSError, ValueError, AttributeError):
            continue
    # Older metadata has no scenario. Recover its opaque image name from the
    # log's session ID rather than mixing images from different scenarios.
    for log_path in (PROJECT_ROOT / "logs").glob("session_*.jsonl"):
        try:
            with log_path.open(encoding="utf-8") as stream:
                record = json.loads(next(stream))
            if record.get("scenario") == scenario and record.get("session_id"):
                candidates.add(generated_illustration_name(scenario, str(record["session_id"]), beat_id))
        except (OSError, ValueError, AttributeError, StopIteration):
            continue
    available = []
    for name in sorted(candidates):
        path = find_illustration(name)
        if path:
            try:
                metadata = json.loads(path.with_suffix(".scene.json").read_text(encoding="utf-8"))
                # A failed redraw may have a newer description than the image.
                description = metadata.get("image_description") or metadata.get("description")
                if isinstance(description, str) and description:
                    available.append({"name": name, "description": description,
                                      "prompt": metadata.get("image_prompt", "")
                                      if not metadata.get("image_description") or metadata["image_description"] == metadata.get("description") else ""})
            except (OSError, ValueError, AttributeError):
                continue
    return available


# How far a picture drawn into the cover's bubble may run over the bubble's
# outline, in the cover picture's pixels; faint parts (its soft shadow) may
# go further.
COVER_BUBBLE_OVERFLOW = 40
COVER_BUBBLE_SOLID_ALPHA = 128


def compose_cover(cover: str, bubble: tuple[int, int], inner: str,
                  create: bool = True) -> str | None:
    """The cover picture with ``inner`` drawn as large as it fits inside the
    thought bubble around ``bubble``, saved once under generated/ (named by
    its inputs) and returned by name; None if it cannot be made, or is not
    made yet and ``create`` is false."""
    key = hashlib.sha256(json.dumps([cover, list(bubble), inner]).encode()).hexdigest()
    name = f"generated/cover-{key}.png"
    if find_illustration(name):
        return name
    if not create:
        return None
    base_path, inner_path = find_illustration(cover), find_illustration(inner)
    if base_path is None or inner_path is None:
        return None
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    base = Image.open(base_path).convert("RGBA")
    # The bubble: the opaque white region around the point, and a little
    # past its outline.
    rgba = np.asarray(base)
    white = (rgba[..., 3] > 200) & (rgba[..., :3].min(axis=2) > 235)
    # (A copy: an image made from an array is read-only, and the fill would
    # then quietly change nothing.)
    region = Image.fromarray((white * 255).astype("uint8")).copy()
    ImageDraw.floodfill(region, tuple(bubble), 128)
    region = region.point(lambda v: 255 if v == 128 else 0)
    centre = np.asarray(region) > 0
    region = region.filter(ImageFilter.MaxFilter(2 * COVER_BUBBLE_OVERFLOW + 1))
    inside = np.asarray(region) > 0
    if not centre.any():
        return None
    ys, xs = np.nonzero(centre)
    cx, cy = xs.mean(), ys.mean()
    picture = Image.open(inner_path).convert("RGBA")
    bbox = picture.getchannel("A").getbbox()
    if bbox:
        picture = picture.crop(bbox)
    # Shrink from the bubble's width until the picture's opaque part fits.
    scale = (xs.max() - xs.min() + 1) / picture.width
    while scale * min(picture.size) >= 8:
        size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
        x, y = round(cx - size[0] / 2), round(cy - size[1] / 2)
        if x >= 0 and y >= 0 and x + size[0] <= base.width and y + size[1] <= base.height:
            resized = picture.resize(size, Image.LANCZOS)
            opaque = np.asarray(resized.getchannel("A")) >= COVER_BUBBLE_SOLID_ALPHA
            if inside[y:y + size[1], x:x + size[0]][opaque].all():
                base.alpha_composite(resized, (x, y))
                path = ILLUSTRATION_ROOT / name
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp.png")
                base.save(temporary)
                temporary.replace(path)
                return name
        scale *= 0.95
    return None


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
    cover = spec.get("cover", {})
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
        cover_illustration=cover.get("illustration"),
        cover_bubble=tuple(cover["bubble"]) if cover.get("bubble") else None,
        cover_bubble_beat=cover.get("bubble_beat"),
    )


# ---------------------------------------------------------------------------
# What happened in one session step, from a live output or a log record


@dataclass(frozen=True)
class StoryTurn:
    source: str  # session_open / participant / world_event / observed / session_close
    pooh_text: str
    user_text: str = ""
    world_event_id: str | None = None
    motion: dict[str, str] = field(default_factory=dict)
    ends_story: bool = False
    situation: dict[str, Any] = field(default_factory=dict)
    # What the robot saw the participant do (source "observed"), and
    # whether it came with the participant's next words
    observed_actions: tuple[str, ...] = ()
    with_speech: bool = False


def illustration_situation(value: Any) -> dict[str, Any]:
    """A compact snapshot for image planning; never used to invent book text."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if not isinstance(value, dict):
        return {}
    snapshot = {key: value[key] for key in ("place", "purpose", "props", "decided") if key in value}
    if isinstance(value.get("events"), list):
        snapshot["recent_events"] = value["events"][-6:]
    return snapshot


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
        situation=illustration_situation(getattr(output, "updated_situation", None)),
        observed_actions=tuple(getattr(output, "observed_actions", ())),
        with_speech=bool(getattr(output, "observed_with_speech", False)),
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
        situation=illustration_situation(record.get("updated_situation")),
        observed_actions=tuple(str(a) for a in record.get("observed_actions") or ()),
        with_speech=bool(record.get("with_speech")),
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
    style: str  # "title" | "narration" | "speech" | "closing" | "hint"
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


def motion_lead(motion: dict[str, str], phrases: dict[str, dict[str, Any]]) -> str:
    """「プーは うれしそうな かおで、みぎてを あげて」, to lead into a line."""
    face = phrases.get("eyebrow", {}).get(motion.get("eyebrow", ""), "")
    actions = [
        phrases.get(part, {}).get(motion.get(part, ""))
        for part in ("arm", "neck", "nose")
    ]
    links = ([face] if face else []) + [action[0] for action in actions if action]
    return "プーは " + "、".join(links) if links else ""


def spoken_part(user_text: str) -> str:
    """What was said, from 「「どうぞ」と言って、風船をプーに手渡す」 as the ROS
    side words a seen handover; the handover itself is retold from the
    observed action recorded just before."""
    if user_text.startswith("「") and "」と言って、" in user_text:
        return user_text[1:user_text.index("」と言って、")]
    return user_text


# The verbs the ROS side words what it saw with, in the book's hiragana: as
# a link in a chain (て), alongside speech (ながら), and to end on (ました).
OBSERVED_VERBS = (
    ("手渡した", "てわたして", "てわたしながら", "てわたしました"),
    ("差し出した", "さしだして", "さしだしながら", "さしだしました"),
    ("見せた", "みせて", "みせながら", "みせました"),
    ("なでた", "なでて", "なでながら", "なでました"),
    ("触れた", "さわって", "さわりながら", "さわりました"),
    ("握った", "にぎって", "にぎりながら", "にぎりました"),
)
# Where Pooh is touched (pooh_body's touch sensors), as the book spells it.
BODY_PLACES = {"左手": "ひだりて", "右手": "みぎて", "左足": "ひだりあし",
               "右足": "みぎあし", "手": "て", "足": "あし"}


def observed_clause(action: str) -> tuple[str, str, str] | None:
    """「参加者がプーの左手をなでた」 -> (「プーの ひだりてを なでて」,
    「… なでながら」, 「… なでました」), or None for a verb not known here."""
    body = action.strip().rstrip("。").removeprefix("参加者が")
    for plain, te, nagara, past in OBSERVED_VERBS:
        if body.endswith(plain):
            body = body.removesuffix(plain)
            break
    else:
        return None
    body = body.replace("をプー", "を プー").replace("プーの", "プーの ")
    for place, reading in sorted(BODY_PLACES.items(), key=lambda item: -len(item[0])):
        if f"プーの {place}" in body:
            body = body.replace(f"プーの {place}", f"プーの {reading}")
            break
    return tuple(f"{body} {verb}" for verb in (te, nagara, past))


def observed_sentence(action: str) -> str:
    """「参加者がプーの左手をなでた」 -> 「あなたは プーの ひだりてを なでました。」"""
    clause = observed_clause(action)
    if clause is not None:
        return f"あなたは {clause[2]}。"
    return "あなたは " + action.strip().rstrip("。").removeprefix("参加者が") + "。"


def participant_entries(
    actions: Iterable[str], said: str = "", after: tuple[str, ...] = ()
) -> list[StoryEntry]:
    """What the participant did and said, retold as one sentence where the
    verbs allow: 「あなたは プーの ひだりてを なでて、はちみつの壺を プーに
    さしだしながら、「どうぞ」と いいました。」 rather than a line for each.
    ``after``: what was retold just before, so the next touch goes on from
    it (「それから、…」, 「もう いちど、…」) instead of starting over."""
    actions = list(actions)
    entries: list[StoryEntry] = []
    chain: list[tuple[str, str, str]] = []
    subject = "あなたは "
    if after and not said:
        subject = "もう いちど、" if actions[:1] == [after[-1]] else "それから、"

    def end_chain() -> None:
        nonlocal subject
        if chain:
            links = [clause[0] for clause in chain[:-1]] + [chain[-1][2]]
            entries.append(StoryEntry("narration", subject + "、".join(links) + "。"))
            subject = "あなたは "
            chain.clear()

    for action in actions:
        clause = observed_clause(action)
        if clause is None:
            end_chain()
            entries.append(StoryEntry("narration", observed_sentence(action)))
            subject = "あなたは "
        else:
            chain.append(clause)
    if not said:
        end_chain()
    elif chain:
        links = [clause[0] for clause in chain[:-1]] + [chain[-1][1]]
        entries.append(StoryEntry(
            "speech", "あなたは " + "、".join(links) + f"、「{said}」と いいました。", "participant"))
    else:
        entries.append(StoryEntry("speech", f"「{said}」と、あなたは いいました。", "participant"))
    return entries


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
    entries.extend(participant_entries(
        turn.observed_actions, spoken_part(turn.user_text) if turn.user_text else ""))
    # The gesture goes with the line it was made with, so it is not read
    # as Pooh's answer to whatever is retold just before.
    lead = motion_lead(turn.motion, config.motion_phrases)
    if turn.pooh_text:
        said = f"{lead} いいました。" if lead else ""
        entries.append(StoryEntry("speech", f"{said}「{turn.pooh_text}」", "pooh"))
    elif lead:
        entries.append(StoryEntry("narration", motion_sentence(turn.motion, config.motion_phrases)))
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
    # Where a picture still to be generated goes, and the text beside it:
    # the text is written first, from where a reader starts.
    late_picture_box: Box | None = None
    late_text_columns: tuple[Box, ...] | None = None


@dataclass(frozen=True)
class CoverLayout:
    frame_margin: int  # from the panel edge to the frame's outer line
    title_y: int
    title_px: int
    picture_box: Box


COVER_LAYOUTS = {
    "portrait": CoverLayout(frame_margin=50, title_y=170, title_px=150,
                            picture_box=(160, 480, 1300, 1430)),
    "landscape": CoverLayout(frame_margin=50, title_y=120, title_px=120,
                             picture_box=(380, 340, 1400, 1080)),
}


def frame_image(size: tuple[int, int], margin: int) -> Any:
    """plush_memory's cover frame: a hand-drawn-looking double rectangle
    just inside the panel edge, transparent elsewhere."""
    from PIL import Image, ImageDraw

    w, h = size
    image = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    rnd = random.Random(7)  # the same wobble every time

    def wobbly_rect(inset: int, width: int) -> None:
        x0, y0, x1, y1 = margin + inset, margin + inset, w - margin - inset, h - margin - inset
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        points = []
        for (ax, ay), (bx, by) in zip(corners, corners[1:]):
            n = max(2, int(max(abs(bx - ax), abs(by - ay)) / 60))
            for i in range(n):
                t = i / n
                points.append((ax + (bx - ax) * t + rnd.uniform(-1.5, 1.5),
                               ay + (by - ay) * t + rnd.uniform(-1.5, 1.5)))
        points.append(points[0])
        draw.line(points, fill=(0, 0, 0, 255), width=width, joint="curve")

    wobbly_rect(0, 5)
    wobbly_rect(COVER_INNER_FRAME_INSET, 2)
    return image


LAYOUTS = {
    # Picture on top, text below, like most picture books held upright.
    "portrait": PageLayout(
        "portrait", (1620, 2160),
        picture_box=(160, 70, 1300, 1000),
        text_columns=((90, 1130, 1440, 960),),
        plain_text_columns=((90, 110, 1440, 1950),),
    ),
    # The open-book spread: picture on the left page, text on the right; a
    # picture still to be generated goes on the right, after the text; without
    # a picture, the text runs on from the left page to the right.
    "landscape": PageLayout(
        "landscape", (2160, 1620),
        picture_box=(60, 120, 980, 1380),
        text_columns=((1120, 100, 980, 1440),),
        late_picture_box=(1120, 120, 980, 1380),
        late_text_columns=((60, 100, 980, 1440),),
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
    when the font file is missing."""
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


def text_after(text: str, lines: list[str]) -> str:
    """What is left of ``text`` once its first ``lines`` are written: a
    sentence of 分かち書き may end in a quote that has no spaces, so the
    rest is cut from the text rather than joined back from its lines."""
    rest = text.strip()
    for line in lines:
        rest = rest.lstrip()[len(line):]
    return rest.lstrip()


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
    pen_r: int | None = None  # a broader pen than the viewer's own (a cover's title)

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
    # Its picture is generated from the scene once the scene is over, so the
    # page is laid out around the picture box and the picture comes later
    # (StoryBuilder.attach_illustration).
    awaits_illustration: bool = False
    # The cover's own picture box, and its frame (frame_image).
    picture_box: Box | None = None
    frame_margin: int | None = None

    @property
    def text_box(self) -> Box:
        return self.text_columns[self.column]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "beat_id": self.beat_id,
            "illustration": self.illustration,
            **({"picture_box": list(self.picture_box)} if self.picture_box else {}),
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
        awaits_illustration: Callable[[str], bool] | None = None,
        compose_cover: bool = False,
    ) -> None:
        self.config = config
        self.layout = layout
        self.measure = measure
        # Whether a beat's picture is still to be generated (the live book);
        # without it, pages take whatever picture there is now.
        self.awaits_illustration = awaits_illustration or (lambda beat_id: False)
        # Draw the ending into the cover's bubble (the live book); otherwise
        # (a book read back from its log) only a cover drawn then is used.
        self.compose_cover = compose_cover
        self.pages: list[StoryPage] = []
        # Made when the book finishes: the tablet ends on it, the PDF opens
        # with it.
        self.cover: StoryPage | None = None
        self.finished = False
        # What the robot saw with the participant's words (a handover seen
        # as 「どうぞ」 was said), kept to be retold with them: the session
        # records the two one after the other.
        self._seen: tuple[str, ...] = ()
        # What was just retold on its own, for a touch that follows it.
        self._just_seen: tuple[str, ...] = ()

    def add(self, turn: StoryTurn) -> list[PageEvent]:
        if self.finished:
            return []
        events: list[PageEvent] = []
        if turn.source == "observed" and turn.with_speech:
            events.extend(self._retell_seen())
            self._seen = turn.observed_actions
            return events
        if turn.source == "participant":
            turn = replace(turn, observed_actions=self._seen + turn.observed_actions)
            self._seen = ()
        else:
            events.extend(self._retell_seen())
        if turn.source == "observed":
            # A touch, or what was done while Pooh was busy and the words
            # were dropped, is retold on its own, as it happens.
            events.extend(self._retell_on_its_own(turn.observed_actions))
            return events
        self._just_seen = ()
        beat_id, entries = narrate(turn, self.config)
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
        events = self._retell_seen()
        self.finished = True
        self.cover = self._cover_page()
        return events + self._place([StoryEntry("closing", self.config.closing)])

    def refresh_cover(self) -> None:
        """Make the cover again, once a picture it shows has come."""
        if self.cover is not None:
            self.cover = self._cover_page()

    def _cover_illustration(self) -> str | None:
        """The cover picture, thinking of how the story ended: the picture
        of the bubble's beat in its bubble, or the beat's bundled one when
        the book has none (the story stopped before it, or it was not drawn)
        rather than another scene's."""
        cover = self.config.cover_illustration
        if not cover or find_illustration(cover) is None:
            return None
        if self.config.cover_bubble is None:
            return cover
        ending = next((page.illustration for page in reversed(self.pages)
                       if page.illustration and page.beat_id == self.config.cover_bubble_beat), None)
        if ending is None:
            beat = self.config.beats.get(self.config.cover_bubble_beat or "")
            if beat is not None and beat.illustration and find_illustration(beat.illustration):
                ending = beat.illustration
        if ending is None:
            return cover
        try:
            return compose_cover(cover, self.config.cover_bubble, ending,
                                 create=self.compose_cover) or cover
        except Exception as exc:
            print(f"[絵本] 表紙を合成できませんでした: {type(exc).__name__}: {exc}", file=sys.stderr)
            return cover

    def attach_illustration(self, page: StoryPage) -> str | None:
        """Give a page laid out around a picture still to come the picture
        its beat has now (generated, or the bundled one if that failed)."""
        name = self.config.illustration_name(page.beat_id)
        if not name or find_illustration(name) is None:
            name = None
        previous = next((p.illustration for p in reversed(self.pages[:page.index])
                         if p.illustration), None)
        page.illustration = None if name == previous else name
        return page.illustration

    def _cover_page(self) -> StoryPage:
        """In the frame: the title at the top, the cover picture under it and
        「めくる→」 in the bottom-right corner."""
        cover = COVER_LAYOUTS[self.layout.name]
        width, height = self.layout.size
        inner = cover.frame_margin + COVER_INNER_FRAME_INSET + COVER_PADDING
        page = StoryPage(
            index=-1, beat_id=None, illustration=self._cover_illustration(),
            text_columns=((inner, inner, width - 2 * inner, height - 2 * inner),),
            picture_box=cover.picture_box, frame_margin=cover.frame_margin,
        )
        if self.config.title:
            lines = wrap_text(self.config.title, cover.title_px, width - 2 * inner, self.measure)
            page.placed.append(PlacedEntry(
                StoryEntry("title", self.config.title), lines, inner, cover.title_y,
                width - 2 * inner, cover.title_px, "center", COVER_TITLE_PEN_R))
        hint_w = int(self.measure(COVER_HINT, BODY_PX)) + 1
        page.placed.append(PlacedEntry(
            StoryEntry("hint", COVER_HINT), [COVER_HINT], width - inner - hint_w,
            height - inner - line_height(BODY_PX), hint_w, BODY_PX))
        return page

    def _retell_seen(self) -> list[PageEvent]:
        """What was seen with words that never got answered, on its own."""
        seen, self._seen = self._seen, ()
        return self._retell_on_its_own(seen)

    def _retell_on_its_own(self, actions: tuple[str, ...]) -> list[PageEvent]:
        if not actions or not self.pages:
            return []
        entries = participant_entries(actions, after=self._just_seen)
        self._just_seen = actions
        return self._place(entries)

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
        if self._illustration_for_beat(beat_id) is not None or self.awaits_illustration(beat_id):
            return False
        if page.column + 1 >= len(page.text_columns):
            return False
        if page.cursor is not None:
            page.column += 1
            page.cursor = None
        return True

    def _new_page(self, beat_id: str | None) -> NewPage:
        beat = self.config.beats.get(beat_id or "")
        # A picture still to be generated keeps its box free; a beat without
        # any picture has an all-text page, rather than a blank half on the
        # tablet.
        awaited = beat_id is not None and self.awaits_illustration(beat_id)
        picture = None if awaited else self._illustration_for_beat(beat_id)
        if awaited:
            text_columns = self.layout.late_text_columns or self.layout.text_columns
        elif picture is not None:
            text_columns = self.layout.text_columns
        else:
            text_columns = self.layout.plain_text_columns
        page = StoryPage(
            index=len(self.pages),
            beat_id=beat_id,
            illustration=picture,
            text_columns=text_columns,
            awaits_illustration=awaited,
            picture_box=self.layout.late_picture_box if awaited else None,
        )
        self.pages.append(page)
        return NewPage(page)

    def _illustration_for_beat(self, beat_id: str | None) -> str | None:
        name = self.config.illustration_name(beat_id)
        if not name or self.config.illustration_path(beat_id) is None:
            return None
        previous = next((page.illustration for page in reversed(self.pages) if page.illustration), None)
        return None if name == previous else name

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
                    entry = StoryEntry(entry.style, text_after(entry.text, head.lines),
                                       entry.speaker)
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
    records = list(records)
    config = load_story_config(scenario)
    if records:
        config = replace(config, session_id=str(records[0].get("session_id", "")))
    builder = StoryBuilder(config, LAYOUTS[layout], measure)
    for turn in turns_from_log(records, opening_line):
        builder.add(turn)
    builder.finish()
    return builder
