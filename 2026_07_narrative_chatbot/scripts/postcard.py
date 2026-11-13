#!/usr/bin/env python3
"""Condense a session's picture book onto a few printable postcards.

    python scripts/postcard.py logs/session_<...>.jsonl
    python scripts/postcard.py <log>.jsonl --out /tmp/cards.pdf --png /tmp/cards
    python scripts/postcard.py <log>.jsonl --style prose            # a short retelling instead of a poem
    python scripts/postcard.py <log>.jsonl --plan /tmp/cards.json   # re-render an edited plan
    python scripts/postcard.py <log>.jsonl --no-llm                 # extract only, no API call

The book itself is untouched: its scenes, text and illustrations come from
``storybook.build_book`` exactly as the PDF book is made, and only regrouped
here. Each card is portrait 100 x 148 mm (Japanese postcard size; what a
Canon SELPHY prints) with one existing illustration. Two styles:

- ``poem`` (default): the picture is the card; under it a few short lines,
  centered, inside a hand-drawn honey-colored frame.
- ``prose``: a smaller picture above a short retelling.

The text may be written by an LM, but Python checks it (``check_plan``):
the scenes stay in order and are all covered, every quote 「…」 is the
session's own words (the LM points at quotes by number and Python inserts
them), the participant's own words appear somewhere, and the text fits the
card at a readable size. A second LM call looks for events or feelings the
session never had. A plan that still fails after retries falls back to an
extract-only one (``extractive_plan``), built from the book's own sentences.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pydantic import BaseModel

import storybook
from storybook import StoryEntry, find_illustration

MM = 72 / 25.4  # PDF points per millimetre
CARD_W_MM, CARD_H_MM = 100.0, 148.0
# Paper to print on: (short side, long side) in mm, as the printer's page
# (Canon SELPHY: PageSize=Postcard / 89x119mm). Cards are laid out at
# postcard size and drawn scaled uniformly to fit other paper, centered (the
# same card, a little smaller; white margins where the proportions differ).
PAPER_SIZES = {
    "hagaki": (100.0, 148.0),  # はがき
    "l": (89.0, 119.0),        # L判（SELPHY の L サイズ）
}
PAPER_LABELS = {"hagaki": "はがき", "l": "L判"}
# Kept clear at every edge when a card is scaled to the paper: borderless
# printing runs a little past the paper's edges, which would cut the frame.
# 4 mm on L: at 2.5 mm a SELPHY CP1500 cut the loops of the frame's bow
# (2026-11-09), so it trims a little more than that.
PAPER_SAFE_MARGIN_MM = {"hagaki": 0.0, "l": 4.0}
DEFAULT_PAPER = "hagaki"
DEFAULT_CARDS = 3
CLOSING = "おしまい"
LM_ATTEMPTS = 3
# Summarizing faithfully and judging it took a stronger model than the
# chat's own gpt-4o: with it, the fact check flagged omissions it was told
# to allow and every plan fell back to extractive (2026-11-07).
DEFAULT_MODEL = "openai/gpt-5.5"

# Colors (RGB 0-1): the poem card's warm frame and ink.
HONEY = (0.80, 0.58, 0.25)
INK_BROWN = (0.30, 0.22, 0.14)
FOOTER_BROWN = (0.62, 0.52, 0.40)

Box = tuple[float, float, float, float]  # x, y (from the top), w, h in mm


# ---------------------------------------------------------------------------
# Styles


@dataclass(frozen=True)
class Style:
    name: str
    picture: Box
    title_on_first: bool  # the title above the picture on card 1
    text_box: Box
    text_box_first: Box
    body_pt_max: float
    body_pt_min: float
    leading: float
    paragraph_gap: float  # extra space between lines given as paragraphs, in lines
    centered: bool
    closing: bool  # 「おしまい」 under the last card's text
    target_chars: int
    max_chars: int
    max_quotes: int
    instructions: str
    page: tuple[float, float] = (CARD_W_MM, CARD_H_MM)  # width, height in mm
    dialogue: bool = False  # one real exchange, picked rather than written


PROSE_INSTRUCTIONS = """\
- textは、そのまとまりの出来事を、ふつうの漢字かな交じりの文（です・ます調、「あなた」は参加者）で、
  max_chars字前後にまとめなおす。全部の発言をなぞらず、物語の流れがわかる要点だけを書く。
  段落は改行で区切る。1枚に発言は2つまで。"""

POEM_INSTRUCTIONS = """\
- カードは絵が主役。textは、絵の下に中央ぞろえで添える2〜4行の短い詩にする。行は改行で区切り、
  1行は16字以内、全体でmax_chars字以内。絵本と同じ、ひらがな中心の分かち書き（文節ごとに
  半角スペース）で書く。
- 出来事を説明しきらず、そのまとまりのいちばん大事な瞬間を、リズムのよい短いことばで切り取る。
  呼びかけ・くりかえし・擬音・体言止めなどの詩的な表現を使ってよい。3枚を続けて読むと、
  はじまり・なかほど・おわりの流れがわかるようにする。
- 詩的な表現でも、会話にない出来事・気持ち・結果を事実として書かない（「わらった」
  「うれしかった」なども、書かれていなければ書かない）。
- 発言は1枚に1つまで、1行に収まる短いものだけ。行まるごと、または行の一部として入れる。"""


PROSE = Style(
    name="prose",
    picture=(6.0, 6.0, 88.0, 76.0),
    title_on_first=True,
    text_box=(6.0, 85.0, 88.0, 53.5),
    text_box_first=(6.0, 92.3, 88.0, 46.2),
    body_pt_max=11.0, body_pt_min=9.0, leading=1.5, paragraph_gap=0.35,
    centered=False, closing=True,
    target_chars=110, max_chars=150, max_quotes=2,
    instructions=PROSE_INSTRUCTIONS,
)
POEM = Style(
    name="poem",
    picture=(10.0, 10.5, 80.0, 82.0),
    title_on_first=False,
    text_box=(10.0, 98.5, 80.0, 38.0),
    text_box_first=(10.0, 98.5, 80.0, 38.0),
    body_pt_max=13.0, body_pt_min=11.0, leading=1.8, paragraph_gap=0.0,
    centered=True, closing=False,
    target_chars=45, max_chars=60, max_quotes=1,
    instructions=POEM_INSTRUCTIONS,
)
# One real exchange from the conversation: in landscape (the default)
# flowing around the picture's drawn shape, with the beat's small item
# picture (flow_layout()); in portrait under the picture (dialogue_layout()).
# Only the name, page and dialogue flag of these matter.
DIALOGUE_PORTRAIT = Style(
    name="dialogue_portrait",
    picture=POEM.picture, title_on_first=False,
    text_box=POEM.text_box, text_box_first=POEM.text_box_first,
    body_pt_max=10.5, body_pt_min=8.5, leading=1.6, paragraph_gap=0.45,
    centered=False, closing=False,
    target_chars=60, max_chars=120, max_quotes=2,
    instructions="", dialogue=True,
)
DIALOGUE = replace(DIALOGUE_PORTRAIT, name="dialogue", page=(CARD_H_MM, CARD_W_MM))
STYLES = {style.name: style for style in (DIALOGUE, DIALOGUE_PORTRAIT, POEM, PROSE)}
DEFAULT_STYLE = "dialogue"


# ---------------------------------------------------------------------------
# Scenes, from the book


# How much a scene's picture belongs to this session: drawn from this very
# conversation, or not.
PICTURE_SESSION = "session"  # generated for this session
PICTURE_GENERATED = "generated"  # generated for another session (a saved fallback)
PICTURE_BUNDLED = "bundled"  # the scenario's own stock picture
PICTURE_RANK = {PICTURE_SESSION: 2, PICTURE_GENERATED: 1, PICTURE_BUNDLED: 0}


@dataclass
class Scene:
    beat_id: str | None
    illustration: str | None
    entries: list[StoryEntry] = field(default_factory=list)
    picture_kind: str | None = None  # PICTURE_*; None without a picture
    # The book's own narration for this beat (config/storybook.json), not
    # anything written here.
    narration: str = ""
    # The beat's small item for the postcard (postcard_items.Item; see
    # config/postcard/<scenario>.json), and the scenario's card frame
    # (postcard_items.Frame).
    item: Any = None
    frame: Any = None
    # The scenario lists this beat with no item: a card showing its picture
    # gets none (see with_item).
    no_item: bool = False

    def __post_init__(self) -> None:
        if self.illustration and self.picture_kind is None:
            self.picture_kind = (PICTURE_GENERATED if self.illustration.startswith("generated/")
                                 else PICTURE_BUNDLED)


def picture_kind(name: str | None, config: storybook.StoryConfig, beat_id: str | None) -> str | None:
    if not name:
        return None
    if config.session_id and beat_id and name == storybook.generated_illustration_name(
            config.scenario, config.session_id, beat_id):
        return PICTURE_SESSION
    return PICTURE_GENERATED if name.startswith("generated/") else PICTURE_BUNDLED


def best_pictures(scenes: list[Scene], first: int, last: int) -> list[int]:
    """The scenes in first..last whose picture is the most this session's
    own (a picture generated from this conversation over any other)."""
    pictured = [i for i in range(first, last + 1) if scenes[i].picture_kind]
    if not pictured:
        return []
    best = max(PICTURE_RANK[scenes[i].picture_kind] for i in pictured)
    return [i for i in pictured if PICTURE_RANK[scenes[i].picture_kind] == best]


def item_for_beat(scenario: str, beat_id: str | None) -> Any:
    """The beat's small item (postcard_items.Item), if configured and made."""
    import postcard_items

    return postcard_items.item_for_beat(scenario, beat_id)


def with_item(card: "CardPlan", scenes: list[Scene]) -> "CardPlan":
    """The card with its item: its picture's beat's, else the first in its
    scenes (config/postcard/<scenario>.json)."""
    order = ([card.picture_scene] if card.picture_scene is not None else []) + list(
        range(card.first_scene, card.last_scene + 1))
    item = next((scenes[i].item for i in order if 0 <= i < len(scenes) and scenes[i].item), None)
    if card.picture_scene is not None and 0 <= card.picture_scene < len(scenes) \
            and scenes[card.picture_scene].no_item:
        item = None  # its picture asks for none (already full of footprints, say)
    frame = next((scene.frame for scene in scenes if scene.frame is not None), None)
    if frame is not None:
        card = replace(card, frame_style=frame.style, frame_color=list(frame.color),
                       frame_bow=frame.bow, frame_bow_anchor=frame.bow_anchor,
                       frame_bow_size_mm=frame.bow_size_mm)
    if item is None:
        return replace(card, item="", item_trail=[], item_count=0)
    return replace(card, item=item.picture, item_anchor=item.anchor, item_size_mm=item.size_mm,
                   item_trail=list(item.trail), item_count=item.count,
                   item_wiggle_mm=item.wiggle_mm)


def _is_continuation(previous: StoryEntry, entry: StoryEntry) -> bool:
    """A long passage split across columns/pages: the first piece keeps the
    whole text, the rest is its tail (StoryBuilder._place)."""
    return (entry.style == previous.style and entry.speaker == previous.speaker
            and entry.text != previous.text and previous.text.endswith(entry.text))


def scenes_from_book(builder: storybook.StoryBuilder) -> list[Scene]:
    """The book's pages regrouped by scene: a page that opens a beat starts
    a scene, a page without one continues the scene before it."""
    import postcard_items

    frame = postcard_items.load_frame(builder.config.scenario)
    scenes: list[Scene] = []
    # The story only: what was said after its goal (the epilogue) is not.
    for page in builder.story_pages:
        if page.beat_id is not None or not scenes:
            beat = builder.config.beats.get(page.beat_id or "")
            scenes.append(Scene(page.beat_id, None, narration=beat.narration if beat else "",
                                item=item_for_beat(builder.config.scenario, page.beat_id),
                                frame=frame,
                                no_item=postcard_items.beat_hides_item(
                                    builder.config.scenario, page.beat_id)))
        scene = scenes[-1]
        if page.illustration and scene.illustration is None:
            scene.illustration = page.illustration
            scene.picture_kind = picture_kind(page.illustration, builder.config, page.beat_id)
        for placed in page.placed:
            entry = placed.entry
            if entry.style in ("title", "closing", "hint"):
                continue
            if scene.entries and _is_continuation(scene.entries[-1], entry):
                continue
            scene.entries.append(entry)
    return scenes


def top_level_quotes(text: str) -> list[tuple[int, int, str]]:
    """Each outermost 「…」 as (start, end, inside): a quote that itself
    quotes a name (「…「ウズウズ」って…」) stays one quote."""
    quotes, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char == "「":
            if depth == 0:
                start = index
            depth += 1
        elif char == "」" and depth > 0:
            depth -= 1
            if depth == 0:
                quotes.append((start, index + 1, text[start + 1:index]))
    return quotes


def _normalize(text: str) -> str:
    """For matching a quote against what was said: the words only. Speech
    recognition leaves stray punctuation (「うずまきがたの、、かたつむり！」),
    which a card may tidy; the words themselves must stay."""
    return "".join(char for char in unicodedata.normalize("NFKC", text)
                   if not unicodedata.category(char).startswith(("P", "Z", "S", "C")))


def participant_words(entries: list[StoryEntry]) -> list[str]:
    """What the participant actually said, as the book quotes it."""
    words = []
    for entry in entries:
        if entry.speaker == "participant":
            words.extend(inside for _, _, inside in top_level_quotes(entry.text))
    return [word for word in words if word.strip()]


# ---------------------------------------------------------------------------
# The plan: which scenes each card retells, its picture and text


@dataclass
class CardPlan:
    first_scene: int
    last_scene: int
    picture_scene: int | None
    text: str
    # The small item picture beside the text (landscape dialogue style),
    # and where it goes (postcard_items.ANCHORS) and how large.
    item: str = ""
    item_anchor: str = "top_right"
    item_size_mm: float = 16.0
    # Or footprints down the right edge: their pictures (taking turns), how
    # many and how winding (postcard_items.Item.trail).
    item_trail: list[str] = field(default_factory=list)
    item_count: int = 0
    item_wiggle_mm: float = 0.0
    # The card's frame (postcard_items.Frame): double "lines" of
    # frame_color (honey if none), or a "ribbon" of frame_color all round
    # with a bow picture on one corner.
    frame_style: str = "lines"
    frame_color: list[float] = field(default_factory=list)
    frame_bow: str = ""
    frame_bow_anchor: str = "top_left"
    frame_bow_size_mm: float = 0.0


Measure = Callable[[str, float], float]


def _register_font() -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    if "Yomogi" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Yomogi", str(storybook.FONT_PATH)))


def _pdf_measure(text: str, size: float) -> float:
    from reportlab.pdfbase import pdfmetrics

    _register_font()
    return pdfmetrics.stringWidth(text, "Yomogi", size)


def _text_box(style: Style, index: int) -> Box:
    return style.text_box_first if index == 0 else style.text_box


def wrap_card_text(text: str, size: float, width_pt: float,
                   measure: Measure = _pdf_measure) -> list[list[str]]:
    """Paragraphs (split at newlines) wrapped with the book's own line
    breaking rules (kinsoku), measured in PDF points."""
    return [storybook.wrap_text(paragraph.strip(), size, width_pt, measure)
            for paragraph in text.split("\n") if paragraph.strip()]


def _text_height(paragraphs: list[list[str]], size: float, style: Style) -> float:
    lines = sum(len(paragraph) for paragraph in paragraphs)
    gaps = max(0, len(paragraphs) - 1)
    return (lines + gaps * style.paragraph_gap) * size * style.leading


def _with_closing(text: str, style: Style, last: bool) -> str:
    return f"{text}\n{CLOSING}" if last and style.closing else text


def fit_size(text: str, index: int, last: bool, style: Style = POEM,
             measure: Measure = _pdf_measure) -> float | None:
    """The largest body size the card's text fits at, or None."""
    _, _, width_mm, height_mm = _text_box(style, index)
    text = _with_closing(text, style, last)
    size = style.body_pt_max
    while size >= style.body_pt_min - 1e-9:
        paragraphs = wrap_card_text(text, size, width_mm * MM, measure)
        if _text_height(paragraphs, size, style) <= height_mm * MM:
            return size
        size -= 0.5
    return None


def check_plan(plan: list[CardPlan], scenes: list[Scene], cards: int,
               style: Style = POEM, measure: Measure = _pdf_measure) -> list[str]:
    """Everything wrong with a plan, in words an LM can act on; empty if none."""
    errors = check_structure(plan, scenes, cards)
    if errors:
        return errors
    source = _normalize("".join(entry.text for scene in scenes for entry in scene.entries))
    for number, card in enumerate(plan, start=1):
        card_quotes = [inside for _, _, inside in top_level_quotes(card.text)]
        for quote in card_quotes:
            if _normalize(quote) not in source:
                errors.append(f"カード{number}: 「{quote}」は会話にない言葉です。"
                              "かぎかっこの中は会話の言葉をそのまま（一続きの一部でもよい）使ってください。")
        # A name in brackets (「ウズウズ」) inside a quote is not a second quote.
        if len(card_quotes) > style.max_quotes:
            errors.append(f"カード{number}: 発言の引用は{style.max_quotes}つまでにしてください"
                          f"（{len(card_quotes)}つ）。")
        length = len(card.text.replace("\n", ""))
        if length > style.max_chars:
            errors.append(f"カード{number}: {length}字は長すぎます。"
                          f"{style.target_chars}字前後にしてください。")
        elif fit_size(card.text, number - 1, number == cards, style, measure) is None:
            errors.append(f"カード{number}: 文章がカードに入りません。"
                          + ("1行を短くするか、行を減らしてください。" if style.centered
                             else "もっと短くしてください。"))
    return errors + check_participant_quoted(plan, scenes)


def check_participant_quoted(plan: list[CardPlan], scenes: list[Scene]) -> list[str]:
    said = [_normalize(word) for scene in scenes for word in participant_words(scene.entries)]
    quoted = [_normalize(inside) for card in plan for _, _, inside in top_level_quotes(card.text)]
    if said and not any(q and q in word for q in quoted for word in said):
        return ["参加者（あなた）が実際に言った言葉を、どれかのカードに少なくとも1つ"
                "そのまま入れてください。"]
    return []


def check_structure(plan: list[CardPlan], scenes: list[Scene], cards: int) -> list[str]:
    """The scene ranges (in order, no gaps or overlaps) and each card's
    picture (this session's own first)."""
    errors: list[str] = []
    if len(plan) != cards:
        return [f"カードは{cards}枚にしてください（{len(plan)}枚でした）。"]
    expected_first = 0
    for number, card in enumerate(plan, start=1):
        if card.first_scene != expected_first or card.last_scene < card.first_scene:
            errors.append(
                f"カード{number}: 場面は順番どおり、重ならず、抜けなく分けてください"
                f"（{expected_first}から始まるはずが {card.first_scene}〜{card.last_scene}）。")
        expected_first = card.last_scene + 1
    if plan and plan[-1].last_scene != len(scenes) - 1:
        errors.append(f"最後のカードは最後の場面（{len(scenes) - 1}）まで含めてください。")
    if errors:
        return errors
    for number, card in enumerate(plan, start=1):
        group = scenes[card.first_scene:card.last_scene + 1]
        pictured = [card.first_scene + i for i, scene in enumerate(group) if scene.illustration]
        if card.picture_scene is None:
            if pictured:
                errors.append(f"カード{number}: 挿絵のある場面（{pictured}）から1つ選んでください。")
        elif card.picture_scene not in pictured:
            errors.append(f"カード{number}: picture_sceneは挿絵のある場面 {pictured} から選んでください。")
        elif card.picture_scene not in best_pictures(scenes, card.first_scene, card.last_scene):
            best = best_pictures(scenes, card.first_scene, card.last_scene)
            errors.append(f"カード{number}: この会話から生成した絵を優先して、picture_sceneは {best} から"
                          "選んでください。")
    return errors


def _balanced_groups(scenes: list[Scene], cards: int) -> list[tuple[int, int]]:
    """Consecutive scene ranges, each starting at a scene with a picture
    where possible, splitting the pictures as evenly as possible. (Grouping
    by this session's own pictures instead left the ending without its
    card: those are mostly early scenes.)"""
    pictured = [i for i, scene in enumerate(scenes) if scene.illustration]
    cards = max(1, min(cards, len(scenes)))
    if len(pictured) >= cards:
        starts = [pictured[round(k * len(pictured) / cards)] for k in range(cards)]
        starts[0] = 0
    else:
        starts = [round(k * len(scenes) / cards) for k in range(cards)]
    ends = [start - 1 for start in starts[1:]] + [len(scenes) - 1]
    return list(zip(starts, ends))


def extractive_plan(scenes: list[Scene], cards: int, style: Style = POEM,
                    measure: Measure = _pdf_measure) -> list[CardPlan]:
    """A plan made of the book's own sentences only: each scene's narration,
    and the participant's longest utterance in the group, shortened by
    dropping sentences until it fits."""
    plan = []
    groups = _balanced_groups(scenes, cards)
    for index, (first, last) in enumerate(groups):
        group = scenes[first:last + 1]
        voices = [entry for scene in group for entry in scene.entries
                  if entry.speaker == "participant"]
        voice = max(voices, key=lambda entry: len(entry.text), default=None)
        # In the order they happened, so cause stays before effect.
        parts = [entry.text for scene in group for position, entry in enumerate(scene.entries)
                 if entry is voice or (position == 0 and entry.style == "narration")]
        is_last = index == len(groups) - 1

        def fits(parts: list[str]) -> bool:
            text = "\n".join(parts)
            return (len(text.replace("\n", "")) <= style.max_chars
                    and fit_size(text, index, is_last, style, measure) is not None)

        while len(parts) > 1 and not fits(parts):
            # Drop the latest scene narration, then the participant's words.
            others = [i for i, text in enumerate(parts) if voice is None or text != voice.text]
            parts.pop(max(others) if others else -1)
        picture = next(iter(best_pictures(scenes, first, last)), None)
        plan.append(CardPlan(first, last, picture, "\n".join(parts)))
    return plan


# ---------------------------------------------------------------------------
# The LM retelling


@dataclass(frozen=True)
class Quote:
    """One quoted utterance 「…」 in the book, referred to as {q<n>}."""
    token: str
    scene: int
    speaker: str
    text: str


_TOKEN = re.compile(r"\{q(\d+)\}")


def quotes_of(scenes: list[Scene]) -> list[Quote]:
    quotes = []
    for index, scene in enumerate(scenes):
        for entry in scene.entries:
            for _, _, inside in top_level_quotes(entry.text):
                quotes.append(Quote(f"{{q{len(quotes)}}}", index, entry.speaker, inside))
    return quotes


def _scenes_for_lm(scenes: list[Scene], quotes: list[Quote]) -> str:
    """The scenes with every quote replaced by its token, so the LM can
    only refer to what was said, never retype it."""
    by_scene: dict[int, list[Quote]] = {}
    for quote in quotes:
        by_scene.setdefault(quote.scene, []).append(quote)
    rows = []
    for index, scene in enumerate(scenes):
        pending = list(by_scene.get(index, []))
        lines = []
        for entry in scene.entries:
            text = entry.text
            for start, end, _ in reversed(top_level_quotes(text)):
                text = text[:start] + "\0" + text[end:]
            for _ in top_level_quotes(entry.text):
                text = text.replace("\0", pending.pop(0).token, 1)
            lines.append(text)
        rows.append({"scene": index, "picture": scene.picture_kind, "lines": lines})
    return json.dumps({
        "scenes": rows,
        "quotes": {q.token: {"speaker": "あなた" if q.speaker == "participant" else "プー",
                             "text": q.text} for q in quotes},
    }, ensure_ascii=False, indent=1)


def tidy_quote(text: str) -> str:
    """Speech recognition's punctuation, in the book's: 「わわ，たくさん」 ->
    「わわ、たくさん」, 「、、」 -> 「、」. The words are left alone."""
    text = text.replace("，", "、").replace(",", "、").replace("．", "。")
    return re.sub(r"([、。])\1+", r"\1", text).strip()


def expand_draft(text: str, first: int, last: int, quotes: list[Quote]) -> tuple[str, list[str]]:
    """The draft with its tokens replaced by the exact quotes 「…」, and
    what is wrong with its use of them."""
    errors: list[str] = []
    by_token = {q.token: q for q in quotes}

    def replace(match: re.Match) -> str:
        quote = by_token.get(match.group(0))
        if quote is None:
            errors.append(f"{match.group(0)} はありません。")
            return match.group(0)
        if not first <= quote.scene <= last:
            errors.append(f"{match.group(0)} はこのカードの場面（{first}〜{last}）の発言ではありません。")
        return f"「{tidy_quote(quote.text)}」"

    # The LM tends to write 「あなたは {q3} と」 with spaces around a token.
    text = re.sub(r"[ 　]*(\{q\d+\})[ 　]*", r"\1", text)
    return _TOKEN.sub(replace, text), errors


class Problem(BaseModel):
    card: int
    # "added": not in the session; "order": sequence or cause changed;
    # "speaker": said by someone else; "spoiler": told before it happens;
    # "omission": left out (allowed: a card is a summary).
    kind: str
    phrase: str
    reason: str


class PostcardDraft(BaseModel):
    first_scene: int
    last_scene: int
    picture_scene: int | None
    text: str


PLAN_INSTRUCTIONS = """ぬいぐるみロボットのプーと参加者（大人）との会話から作った絵本を、参加者が持ち帰る
ポストカード数枚にまとめなおす。scenes.scenesは絵本の場面を順に並べたもので、linesはその場面の
文章（地の文、参加者「あなた」の言葉、プーの言葉）。発言は {{q番号}} に置きかえてあり、中身は
scenes.quotesにある。

- 場面を、順番どおり・重ならず・抜けなく、card_count個のまとまりに分ける
  （first_scene〜last_scene）。物語の始まり・展開・結末がそれぞれ1枚になるように分ける。
- 各カードのpicture_sceneは、そのまとまりの中で絵のある場面（pictureがnullでない）から1つ選ぶ。
  pictureが"session"の絵はこの会話から生成した絵なので最優先し、まとまりに"session"の絵があれば
  必ずその中から選ぶ。なければ"generated"、それもなければ"bundled"から選ぶ。同じ優先度の中では、
  そのカードの内容をいちばんよく表す場面にする。分け方を決めるときも、なるべく各カードに
  "session"の絵が入るようにする（ただし物語の流れが優先）。
{style}
- 発言を入れるときは、quotesの {{q番号}} をそのまま書く（Pythonがその発言を「」付きで原文どおりに
  差し込む）。発言を自分で書き写さない。（「ウズウズ」のような名前を「」でくくるのはよいが、
  会話にある表記のままにする。）プーの発言は、なるべく {{q番号}} を使わず地の文で短く言う
  （内容は変えない）。
- 参加者の発言は音声認識の結果なので、言いよどみ・誤認識・あいづちだけのものがある。
  そうしたものは使わず、意味がはっきりしていて物語を動かした発言（名前をつけた、考えを言った、
  決めた、など）を選ぶ。全体で少なくとも1つは参加者の発言を入れる。
- linesにない出来事・気持ち・理由・結果を足さない（笑った、喜んだ、なども書かれていなければ書かない）。
  出来事の順番と因果関係（なぜそうなったか）はlinesのとおりに保つ。あとの場面でわかることを
  前のカードに書かない。
- 「おしまい」は書かない。
- feedbackがあれば、前回の案（previous_cards）とその問題点。前回の案をもとに、
  指摘された部分だけを直す（指摘のない部分は変えない）。"""


CHECK_INSTRUCTIONS = """ポストカードの文章（cards）が、もとの絵本の文章（scenes）に忠実かを調べる。
カードは要約や短い詩なので、省略・言いかえ・語順の整理・表記（漢字かな、分かち書き、句読点）の
違いは問題ではない。比喩・呼びかけ・くりかえし・擬音・体言止めなどの詩的な表現も、会話にない
出来事や気持ちを事実として述べていなければ問題ではない。
次のものだけを、カードの文から該当部分をそのまま抜き出して（phrase）problemsに書く。
- added: scenesのどこにも書かれていない出来事・行動・気持ち・理由・結果（笑った、喜んだ、など）
- order: 出来事の順番や因果関係（なぜそうなったか）がscenesと入れかわっている
- speaker: 発言や行動をした人が取りちがえられている
- spoiler: そのカードの場面より後でわかることを先に書いている
省略に気づいた場合はkindをomissionとする（問題には数えない）。
問題がなければ空のリストにする。"""


def _make_lm() -> Any:
    import dspy

    model = os.getenv("POSTCARD_MODEL", DEFAULT_MODEL)
    # Reasoning models (gpt-5 family) take only the default temperature and
    # need room to think; DSPy refuses them otherwise.
    reasoning = re.search(r"/(gpt-5|o\d)", model) is not None
    return dspy.LM(model, api_key=os.environ["OPENAI_API_KEY"], timeout=300, num_retries=2,
                   **({"temperature": 1.0, "max_tokens": 16000} if reasoning else {}))


def lm_plan(scenes: list[Scene], title: str, cards: int, style: Style = POEM,
            measure: Measure = _pdf_measure) -> tuple[list[CardPlan], list[str]]:
    """Ask the LM for a plan, retrying with check_plan's (and the fact
    check's) complaints. Returns the last plan and what is still wrong."""
    import dspy

    PlanPostcards = type("PlanPostcards", (dspy.Signature,), {
        "__doc__": PLAN_INSTRUCTIONS.format(style=style.instructions),
        "__annotations__": {
            "title": str, "scenes": str, "card_count": int, "max_chars": int,
            "previous_cards": str, "feedback": str, "cards": list[PostcardDraft],
        },
        "title": dspy.InputField(),
        "scenes": dspy.InputField(),
        "card_count": dspy.InputField(),
        "max_chars": dspy.InputField(),
        "previous_cards": dspy.InputField(),
        "feedback": dspy.InputField(),
        "cards": dspy.OutputField(),
    })
    CheckPostcards = type("CheckPostcards", (dspy.Signature,), {
        "__doc__": CHECK_INSTRUCTIONS,
        "__annotations__": {"scenes": str, "cards": str, "problems": list[Problem]},
        "scenes": dspy.InputField(),
        "cards": dspy.InputField(),
        "problems": dspy.OutputField(),
    })

    lm = _make_lm()
    predict = dspy.Predict(PlanPostcards)
    check = dspy.Predict(CheckPostcards)
    original = json.dumps([{"scene": i, "lines": [entry.text for entry in scene.entries]}
                           for i, scene in enumerate(scenes)], ensure_ascii=False, indent=1)
    quotes = quotes_of(scenes)
    feedback, previous, plan, errors = "", "", [], ["未実行"]
    for attempt in range(1, LM_ATTEMPTS + 1):
        with dspy.context(lm=lm):
            result = predict(title=title, scenes=_scenes_for_lm(scenes, quotes),
                             card_count=cards, max_chars=style.target_chars,
                             previous_cards=previous, feedback=feedback)
        plan, errors = [], []
        for number, draft in enumerate(result.cards, start=1):
            text, problems = expand_draft(draft.text.strip(), draft.first_scene,
                                          draft.last_scene, quotes)
            errors.extend(f"カード{number}: {problem}" for problem in problems)
            plan.append(CardPlan(draft.first_scene, draft.last_scene, draft.picture_scene, text))
        errors += check_plan(plan, scenes, cards, style, measure)
        if not errors:
            with dspy.context(lm=lm):
                verdict = check(scenes=original, cards=json.dumps(
                    [{"card": n, "scenes": f"{c.first_scene}〜{c.last_scene}", "text": c.text}
                     for n, c in enumerate(plan, start=1)], ensure_ascii=False, indent=1))
            errors = [f"カード{p.card}: 「{p.phrase}」 → {p.reason}（{p.kind}）"
                      for p in verdict.problems if p.kind != "omission"]
        if not errors:
            break
        print(f"[ポストカード] 案{attempt}の問題:\n  " + "\n  ".join(errors), file=sys.stderr)
        feedback = "\n".join(errors)
        previous = json.dumps([draft.model_dump() for draft in result.cards],
                              ensure_ascii=False, indent=1)
    return plan, errors


# ---------------------------------------------------------------------------
# Dialogue style: one real exchange from the conversation under each picture


SPEAKER_NAMES = {"participant": "あなた", "pooh": "プー"}
_NAMED_LINE = re.compile(r"^(あなた|プー)?\s*(「.*」)\s*$")
_SENTENCE_END = re.compile(r"(?<=[。！？!?…])(?![。！？!?…」』）)])")

# The card, in mm from its top: the frame's inside, and what goes in it.
DIALOGUE_TOP, DIALOGUE_BOTTOM = 10.5, 134.5
DIALOGUE_TEXT_X, DIALOGUE_TEXT_W = 11.0, 78.0
DIALOGUE_PICTURE_MAX, DIALOGUE_PICTURE_MIN = 80.0, 60.0
DIALOGUE_DIVIDER = 7.0  # between the picture and the text, the dots in its middle
DIALOGUE_SIZES = (9.5, 9.0, 8.5, 8.0)
DIALOGUE_LEADING = 1.6
PARTICIPANT_INK = (0.58, 0.36, 0.10)
# A line takes at most this share of the column, so Pooh's (left) and the
# participant's (right) stay visibly apart; and the space between them, in lines.
CHAT_WIDTH = 0.86
CHAT_GAP = 0.6


# The only exchanges a card shows: Pooh (left) first, so the eye moves
# left to right, and back to the left for his answer.
DIALOGUE_SHAPES = (["pooh", "participant", "pooh"], ["pooh", "participant"])


_BUDOUX: Any = None
# Where the built-in phrase splitter may break: after these, and before an
# opening bracket (storybook.NO_LINE_END).
_BREAK_AFTER = set("、。，．！？!?…‥」』）")
_PARTICLES = set("のはがをにでともへやか")


def _script(char: str) -> str:
    code = ord(char)
    if 0x3041 <= code <= 0x309F:
        return "hiragana"
    if 0x30A0 <= code <= 0x30FF or char == "ー":
        return "katakana"
    if 0x4E00 <= code <= 0x9FFF or char in "々〆":
        return "kanji"
    return "other"


def phrases(text: str) -> list[str]:
    """The text cut into phrases (文節) a line may break between: by BudouX
    when it is installed, else by a simple rule -- after punctuation, before
    an opening bracket, and where hiragana gives way to kanji, katakana or
    anything else (「見た目は｜どんな｜感じだと｜思う？」). The rule never
    breaks inside a word, but finds fewer places to break in long hiragana."""
    global _BUDOUX
    try:
        import budoux
    except ImportError:
        budoux = None
    if budoux is not None:
        if _BUDOUX is None:
            _BUDOUX = budoux.load_default_japanese_parser()
        return [part for part in _BUDOUX.parse(text) if part]
    def kana_ends_phrase(current: str) -> bool:
        # The hiragana at the end of ``current`` closes a phrase -- unless it
        # is one okurigana character of a kanji word (見た｜目 is one word).
        run = len(current) - len(current.rstrip("".join(
            c for c in set(current) if _script(c) == "hiragana")))
        if run >= 2 or len(current) == run:
            return True
        return current[-1] in _PARTICLES or _script(current[-2]) != "kanji"

    pieces, current = [], ""
    for char in text:
        if current:
            previous = current[-1]
            if (previous in _BREAK_AFTER and char not in _BREAK_AFTER
                    and char not in storybook.NO_LINE_START) \
                    or char in storybook.NO_LINE_END \
                    or (_script(previous) == "hiragana" and _script(char) != "hiragana"
                        and char not in storybook.NO_LINE_START and char not in _BREAK_AFTER
                        and kana_ends_phrase(current)):
                pieces.append(current)
                current = ""
        current += char
    if current:
        pieces.append(current)
    return pieces


def wrap_phrases(text: str, size: float, width_pt: float,
                 measure: Measure = _pdf_measure) -> list[str]:
    """Lines broken only between phrases (see phrases()); a phrase too long
    for a whole line is the only thing broken inside, by the book's rules."""
    lines, line = [], ""
    for phrase in phrases(text.strip()):
        if measure(line + phrase, size) <= width_pt:
            line += phrase
            continue
        if line:
            lines.append(line)
            line = ""
        if measure(phrase, size) <= width_pt:
            line = phrase
            continue
        # Too long for a line (a long stretch of hiragana the rule could
        # not cut): after particles first, so 「こっちのあしあとは｜きみの…」,
        # and only then anywhere, by the book's rules.
        for part in _particle_parts(phrase):
            if measure(line + part, size) <= width_pt:
                line += part
                continue
            if line:
                lines.append(line)
            if measure(part, size) <= width_pt:
                line = part
            else:
                pieces = storybook.wrap_text(part, size, width_pt, measure)
                lines.extend(pieces[:-1])
                line = pieces[-1]
    if line:
        lines.append(line)
    return lines


def _particle_parts(phrase: str) -> list[str]:
    """A long phrase cut after each particle that is followed by more
    hiragana (a guess at word ends, used only where nothing better is known)."""
    parts, current = [], ""
    for index, char in enumerate(phrase):
        current += char
        following = phrase[index + 1:index + 2]
        if char in _PARTICLES and following and _script(following) == "hiragana" \
                and following not in _PARTICLES and len(current) >= 3:
            parts.append(current)
            current = ""
    if current:
        parts.append(current)
    return parts


def split_sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_END.split(text) if part.strip()]


@dataclass(frozen=True)
class Utterance:
    """One thing said, in conversation order, as sentences to pick from."""
    id: str
    scene: int
    speaker: str
    sentences: tuple[str, ...]


def utterances_of(scenes: list[Scene]) -> list[Utterance]:
    return [Utterance(f"u{index}", quote.scene, quote.speaker,
                      tuple(split_sentences(tidy_quote(quote.text))))
            for index, quote in enumerate(quotes_of(scenes))]


def dialogue_line(utterance: Utterance, first: int, last: int) -> str:
    name = SPEAKER_NAMES.get(utterance.speaker, "")
    return f"{name}「{''.join(utterance.sentences[first:last + 1])}」"


@dataclass
class DialogueLayout:
    size: float
    picture_mm: float
    top_mm: float  # where the picture starts; the block is centered
    rows: list[tuple[str, list[str]]]  # (speaker, wrapped lines)


def chat_rows(text: str) -> list[tuple[str, str]]:
    """The card's lines as (speaker, words): the plan keeps 「プー「…」」 so
    it says who spoke, but the card shows the words alone, Pooh's on the
    left and the participant's on the right, like a chat."""
    rows = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        match = _NAMED_LINE.match(line)
        name, quote = (match.group(1) or "", match.group(2)) if match else ("", line)
        if quote.startswith("「") and quote.endswith("」"):
            quote = quote[1:-1]
        rows.append(("participant" if name == SPEAKER_NAMES["participant"] else "pooh", quote))
    return rows


def dialogue_layout(card: CardPlan, measure: Measure = _pdf_measure) -> DialogueLayout | None:
    """Text as large as it can be while the picture keeps at least
    DIALOGUE_PICTURE_MIN; the picture takes what is left (up to its
    maximum), and picture and text together sit in the middle of the card,
    so a short exchange leaves no hole under it."""
    parsed = chat_rows(card.text)
    space = DIALOGUE_BOTTOM - DIALOGUE_TOP
    for size in DIALOGUE_SIZES:
        line_pt = size * DIALOGUE_LEADING
        width = DIALOGUE_TEXT_W * MM
        rows = [(speaker, wrap_phrases(words, size, width * CHAT_WIDTH, measure))
                for speaker, words in parsed]
        height = sum(len(lines) for _, lines in rows) * line_pt
        height += max(0, len(rows) - 1) * CHAT_GAP * line_pt
        text_mm = height / MM
        picture = min(DIALOGUE_PICTURE_MAX, space - DIALOGUE_DIVIDER - text_mm)
        if picture >= DIALOGUE_PICTURE_MIN:
            block = picture + DIALOGUE_DIVIDER + text_mm
            return DialogueLayout(size, picture, DIALOGUE_TOP + (space - block) / 2, rows)
    return None


def check_dialogue_plan(plan: list[CardPlan], scenes: list[Scene], cards: int,
                        style: Style | None = None,
                        measure: Measure = _pdf_measure) -> list[str]:
    """check_plan for the dialogue style: what each card quotes must be
    said in the conversation, in its own scenes, and fit the card."""
    errors = check_structure(plan, scenes, cards)
    if errors:
        return errors
    for number, card in enumerate(plan, start=1):
        group = scenes[card.first_scene:card.last_scene + 1]
        source = _normalize("".join(entry.text for scene in group for entry in scene.entries))
        for _, _, inside in top_level_quotes(card.text):
            if _normalize(inside) not in source:
                errors.append(f"カード{number}: 「{inside}」はこのカードの場面にない言葉です。")
        if not dialogue_fits(card, style or DIALOGUE, scenes, measure):
            errors.append(f"カード{number}: 会話が長すぎてカードに入りません。"
                          "発言の文を減らしてください。")
    return errors + check_participant_quoted(plan, scenes)


class SentencePick(BaseModel):
    utterance: str
    first_sentence: int
    last_sentence: int


class DialogueDraft(BaseModel):
    first_scene: int
    last_scene: int
    picture_scene: int | None
    lines: list[SentencePick]


DIALOGUE_INSTRUCTIONS = """ぬいぐるみロボットのプーと参加者（大人）との会話から作った絵本を、参加者が持ち帰る
ポストカード数枚にする。各カードは絵が主役で、その下に、会話から実際のやりとりを1往復だけ、
そのまま載せる。scenesは絵本の場面を順に並べたもので、pictureはその場面の絵の種類、narrationは
絵本の地の文、utterancesはその場面の発言（会話の順。sentencesは文ごとに分けたもの）。

- 場面を、順番どおり・重ならず・抜けなく、card_count個のまとまりに分ける
  （first_scene〜last_scene）。物語の始まり・展開・結末がそれぞれ1枚になるように分ける。
- 各カードのpicture_sceneは、そのまとまりの中で絵のある場面（pictureがnullでない）から1つ選ぶ。
  pictureが"session"の絵はこの会話から生成した絵なので最優先し、まとまりに"session"の絵があれば
  必ずその中から選ぶ。なければ"generated"、それもなければ"bundled"から選ぶ。同じ優先度の中では、
  載せるやりとりにいちばん合う絵にする。
- linesには、その絵に合うひとつづきのやりとりを選ぶ（utteranceには発言のid、例えば"u12"を書く）。
  やりとりは必ずpicture_sceneの場面の発言から選ぶ。pictureが"session"以外の絵は、その場面の
  はじまり（narrationに書かれた瞬間）を描いた絵なので、その場面の最初の4つの発言（positionが0〜3）
  のどれかから始まるやりとりにする。"session"の絵はその場面の会話全体から描いた絵なので、
  その場面のどのやりとりでもよい。絵とやりとりの内容が食いちがわないようにする。
  ただし最後の場面（締めくくり）の絵のカードだけは、プーの締めくくりのセリフに参加者が答えて
  いないことが多いので、そのカードに含まれる直前の場面の最後の4つの発言から始まるやりとりも
  選べる（締めくくり直前のやりとり。最後のプーの返事が締めくくりのセリフになってもよい）。
  カードではプーのセリフを左、参加者のセリフを右に置くので、視線が左から右へ流れるよう、
  必ずプーから始める。形は「プー→参加者→プー」（プーの問いかけやひとこと、参加者の答え、
  プーの返事）か「プー→参加者」の2通りだけ。idは連続していること。
  ただし、選べる範囲にプー→参加者と続くやりとりが一つもない（プーがひとりで話している）
  場合だけは、プーの発言1つにする。
  そのまとまりの外の発言は使わない。
- 発言は書きかえない。長い発言は、sentencesの連続した範囲（first_sentence〜last_sentence、0から）
  だけを選んで短くする。1枚の会話は、合わせて80字くらいまでが目安。
- 参加者の発言は音声認識の結果なので、言いよどみ・誤認識・あいづちだけのものがある。
  そうしたものは避け、意味がはっきりしていて物語を動かした発言（名前をつけた、考えを言った、
  決めた、など）を含むやりとりを選ぶ。全体で少なくとも1枚は参加者の発言を含める。
- feedbackがあれば、前回の案（previous_cards）とその問題点。前回の案をもとに、
  指摘された部分だけを直す。"""


def _dialogue_scenes_for_lm(scenes: list[Scene], utterances: list[Utterance]) -> str:
    return json.dumps([
        {"scene": index, "picture": scene.picture_kind, "narration": scene.narration,
         "utterances": [{"id": u.id, "position": position,
                         "speaker": SPEAKER_NAMES.get(u.speaker, u.speaker),
                         "sentences": list(u.sentences)}
                        for position, u in enumerate(
                            [u for u in utterances if u.scene == index])]}
        for index, scene in enumerate(scenes)
    ], ensure_ascii=False, indent=1)


# A stock picture (bundled, or drawn for another session) shows the moment
# its scene opens with, so the words beside it come from that scene's first
# few utterances; a picture drawn for this session shows its whole scene.
NEAR_START = 4


def opening_window(scenes: list[Scene], utterances: list[Utterance],
                   picture: int | None, first_scene: int | None = None) -> list[int]:
    """Indices into ``utterances`` an exchange beside this picture may start
    at: its own scene's, and only its first NEAR_START unless the picture
    was drawn for this session. The closing scene (the book's last) often
    opens on Pooh's own closing line with no answer to it, so its card may
    also take an exchange from the last NEAR_START of the scene before
    (when that scene is on the same card): what was said just before the
    end."""
    if picture is None or not 0 <= picture < len(scenes):
        return []
    own = [i for i, u in enumerate(utterances) if u.scene == picture]
    window = own if scenes[picture].picture_kind == PICTURE_SESSION else own[:NEAR_START]
    lead_in = picture - 1
    if picture == len(scenes) - 1 and lead_in >= max(0, first_scene or 0):
        window = [i for i, u in enumerate(utterances) if u.scene == lead_in][-NEAR_START:] + window
    return window


def has_exchange(utterances: list[Utterance], starts: list[int]) -> bool:
    """Whether a Pooh line answered by the participant starts at one of
    ``starts`` (and stays in the same scene)."""
    return any(i + 1 < len(utterances) and utterances[i].speaker == "pooh"
               and utterances[i + 1].speaker == "participant"
               and utterances[i + 1].scene == utterances[i].scene for i in starts)


def build_dialogue_card(draft: DialogueDraft, scenes: list[Scene],
                        utterances: list[Utterance]) -> tuple[CardPlan, list[str]]:
    """The card a draft describes, and what is wrong with its picks."""
    errors: list[str] = []
    by_id = {u.id: (index, u) for index, u in enumerate(utterances)}

    def find(reference: str) -> tuple[int, Utterance] | None:
        # The id, or (as the LM sometimes writes) the utterance's own text.
        if reference.strip() in by_id:
            return by_id[reference.strip()]
        wanted = _normalize(reference)
        matches = [(i, u) for i, u in enumerate(utterances)
                   if wanted and wanted in _normalize("".join(u.sentences))
                   and draft.first_scene <= u.scene <= draft.last_scene]
        return matches[0] if len(matches) == 1 else None

    lines, picked = [], []
    starts = opening_window(scenes, utterances, draft.picture_scene, draft.first_scene)
    alone = not has_exchange(utterances, starts)
    if not (1 if alone else 2) <= len(draft.lines) <= 3:
        errors.append(f"発言は2つか3つにしてください（{len(draft.lines)}つ）。")
    for pick in draft.lines:
        found = find(pick.utterance)
        if found is None:
            errors.append(f"utteranceには発言のid（u12 など）を書いてください（{pick.utterance}）。")
            continue
        index, utterance = found
        if not draft.first_scene <= utterance.scene <= draft.last_scene:
            errors.append(f"{pick.utterance} はこのカードの場面（{draft.first_scene}〜"
                          f"{draft.last_scene}）の発言ではありません。")
        if not 0 <= pick.first_sentence <= pick.last_sentence < len(utterance.sentences):
            errors.append(f"{pick.utterance} の文の範囲 {pick.first_sentence}〜{pick.last_sentence} は"
                          f"ありません（0〜{len(utterance.sentences) - 1}）。")
            continue
        picked.append((index, utterance))
        lines.append(dialogue_line(utterance, pick.first_sentence, pick.last_sentence))
    if any(b != a + 1 for (a, _), (b, _) in zip(picked, picked[1:])):
        errors.append("発言は、続けて交わされた（idが連続する）ものにしてください。")
    allowed_scenes = {utterances[i].scene for i in starts} | {draft.picture_scene}
    if picked and (any(u.scene not in allowed_scenes for _, u in picked)
                   or picked[0][0] not in starts):
        where = (f"場面{draft.picture_scene}の発言" if draft.picture_scene is not None
                 else "絵のある場面の発言")
        if starts:
            where += ("で、" + "・".join(utterances[i].id for i in starts)
                      + " のどれかから始まるもの")
        errors.append(f"絵と合うように、やりとりは{where}にしてください。")
    speakers = [utterance.speaker for _, utterance in picked]
    if speakers not in DIALOGUE_SHAPES and not (alone and speakers == ["pooh"]):
        errors.append("やりとりは「プー→参加者→プー」か「プー→参加者」にしてください。")
    return CardPlan(draft.first_scene, draft.last_scene, draft.picture_scene,
                    "\n".join(lines)), errors


PICTURE_MATCH_INSTRUCTIONS = """pictureはポストカードの挿絵、exchangeはその横に載せる、実際の会話のやりとり
（プーと参加者「あなた」）。narrationは、その絵の場面の絵本の地の文。
読んだ人が、絵を見ながらこの会話を「この場面での会話だ」と受け取れるかを判定する。
- 会話が、絵に描かれた状況そのもの、またはその状況を受けて交わされた話（その状況でどうするか、
  何を思ったか）なら、matchesはtrue。絵に描かれていない物や先の計画が出てきてもよい。
  例：絵がはちみつの空になった壺 → 「空っぽの壺をどうしよう」「リボンをかけて小物入れにする」はtrue。
- 会話が、絵の状況とは関係のない別の話題・別の時点の話だったり、絵と矛盾したりするならfalse。
  例：絵がはちみつの空になった壺 → 壺と関係なく「風船の色はどれにする？」だけを話しているならfalse。
  絵では壺が空なのに、会話では壺がはちみつでいっぱいだと言っているならfalse。
- 参加者の発言は音声認識の結果なので、多少の言い間違い・誤認識は判定の理由にしない。
reasonには、絵に何が描かれているかと、判定の理由を短く書く。"""


def _picture_for_lm(name: str) -> Any:
    """The picture on white, small: enough to see what happens in it."""
    from PIL import Image

    image = _illustration_rgba(name)
    flat = Image.new("RGB", image.size, (255, 255, 255))
    flat.paste(image, mask=image.split()[-1])
    flat.thumbnail((512, 512))
    return flat


def check_pictures_match(plan: list[CardPlan], scenes: list[Scene], lm: Any) -> list[str]:
    """Ask the LM, looking at each card's picture, whether the exchange beside
    it belongs with it; the problems, in words it can act on."""
    import dspy

    CheckPicture = type("CheckPicture", (dspy.Signature,), {
        "__doc__": PICTURE_MATCH_INSTRUCTIONS,
        "__annotations__": {"picture": dspy.Image, "narration": str, "exchange": str,
                            "matches": bool, "reason": str},
        "picture": dspy.InputField(),
        "narration": dspy.InputField(),
        "exchange": dspy.InputField(),
        "matches": dspy.OutputField(),
        "reason": dspy.OutputField(),
    })
    check = dspy.Predict(CheckPicture)
    errors = []
    for number, card in enumerate(plan, start=1):
        picture = _scene_picture(scenes, card)
        if not picture or _illustration_rgba(picture) is None or not card.text:
            continue
        exchange = "\n".join(
            f"{SPEAKER_NAMES.get(speaker, speaker)}：{words}" for speaker, words in chat_rows(card.text))
        with dspy.context(lm=lm):
            verdict = check(picture=dspy.Image(_picture_for_lm(picture)),
                            narration=scenes[card.picture_scene].narration, exchange=exchange)
        if not verdict.matches:
            errors.append(f"カード{number}: 絵とやりとりが合いません（{verdict.reason}）。"
                          "絵の場面に合うやりとりを選び直してください。")
    return errors


def lm_dialogue_plan(scenes: list[Scene], title: str, cards: int, style: Style | None = None,
                     measure: Measure = _pdf_measure) -> tuple[list[CardPlan], list[str]]:
    """The LM only picks (scene ranges, pictures, which exchange and which
    of its sentences); every word on the card is the session's own, so no
    fact check is needed -- but whether each exchange belongs with its
    picture is checked by looking at the picture (check_pictures_match)."""
    import dspy

    PickDialogue = type("PickDialogue", (dspy.Signature,), {
        "__doc__": DIALOGUE_INSTRUCTIONS,
        "__annotations__": {
            "title": str, "scenes": str, "card_count": int,
            "previous_cards": str, "feedback": str, "cards": list[DialogueDraft],
        },
        "title": dspy.InputField(),
        "scenes": dspy.InputField(),
        "card_count": dspy.InputField(),
        "previous_cards": dspy.InputField(),
        "feedback": dspy.InputField(),
        "cards": dspy.OutputField(),
    })
    predict = dspy.Predict(PickDialogue)
    lm = _make_lm()
    utterances = utterances_of(scenes)
    feedback, previous, plan, errors = "", "", [], ["未実行"]
    for attempt in range(1, LM_ATTEMPTS + 1):
        with dspy.context(lm=lm):
            result = predict(title=title, scenes=_dialogue_scenes_for_lm(scenes, utterances),
                             card_count=cards, previous_cards=previous, feedback=feedback)
        plan, errors = [], []
        for number, draft in enumerate(result.cards, start=1):
            card, problems = build_dialogue_card(draft, scenes, utterances)
            errors.extend(f"カード{number}: {problem}" for problem in problems)
            plan.append(with_item(card, scenes))
        if not errors:
            errors = check_dialogue_plan(plan, scenes, cards, style, measure)
        if not errors:
            # Same scene is not yet the same moment: look at the pictures.
            errors = check_pictures_match(plan, scenes, lm)
        if not errors:
            break
        print(f"[ポストカード] 案{attempt}の問題:\n  " + "\n  ".join(errors), file=sys.stderr)
        feedback = "\n".join(errors)
        previous = json.dumps([draft.model_dump() for draft in result.cards],
                              ensure_ascii=False, indent=1)
    return plan, errors


def extractive_dialogue_plan(scenes: list[Scene], cards: int, style: Style | None = None,
                             measure: Measure = _pdf_measure) -> list[CardPlan]:
    """Without an LM: under each card's best picture, from the exchanges its
    scene allows (opening_window), the one where the participant said the
    most right after a Pooh line: that Pooh line (its last sentence:
    usually what he asked), the participant's words, and Pooh's answer if
    it follows (DIALOGUE_SHAPES); the answer is trimmed, then dropped, then
    the participant's words are cut from the end, until it fits (another
    exchange if even that does not). A card with no such exchange (Pooh talking on his own)
    gets the first sentence of his first line there."""
    utterances = utterances_of(scenes)
    plan = []
    for first, last in _balanced_groups(scenes, cards):
        picture = next(iter(best_pictures(scenes, first, last)), None)
        starts = opening_window(scenes, utterances, picture, first)
        candidates = [i + 1 for i in starts if has_exchange(utterances, [i])]
        if not candidates:
            own = [utterances[i] for i in starts if utterances[i].speaker == "pooh"]
            text = dialogue_line(own[0], 0, 0) if own else ""
            plan.append(with_item(CardPlan(first, last, picture, text), scenes))
            continue
        fits = lambda card: dialogue_fits(card, style or DIALOGUE, scenes, measure)

        def card_from(said: int) -> CardPlan:
            """The exchange around utterances[said], cut until it fits: Pooh's
            answer first, then the participant's words from the end (the
            first sentence is always kept)."""
            question = utterances[said - 1]
            # (utterance, first sentence, last sentence)
            before = (question, len(question.sentences) - 1, len(question.sentences) - 1)
            reply = (utterances[said], 0, len(utterances[said].sentences) - 1)
            after: tuple[Utterance, int, int] | None = None
            if said + 1 < len(utterances) and utterances[said + 1].speaker == "pooh" \
                    and utterances[said + 1].scene <= last:
                answer = utterances[said + 1]
                after = (answer, 0, len(answer.sentences) - 1)

            def card_for() -> CardPlan:
                picks = [pick for pick in (before, reply, after) if pick]
                card = CardPlan(first, last, picture, "\n".join(
                    dialogue_line(u, start, end) for u, start, end in picks))
                return with_item(card, scenes)

            def longest(first: int, last: int, fits_upto) -> int | None:
                """The last sentence index in first..last that still fits,
                found by halving (a few layouts, not one per sentence)."""
                best = None
                while first <= last:
                    middle = (first + last) // 2
                    if fits_upto(middle):
                        best, first = middle, middle + 1
                    else:
                        last = middle - 1
                return best

            card = card_for()
            if fits(card):
                return card
            if after is not None:
                answer, start, end = after

                def answer_fits(upto: int) -> bool:
                    nonlocal after
                    after = (answer, start, upto)
                    return fits(card_for())

                kept = longest(start, end, answer_fits)
                after = (answer, start, kept) if kept is not None else None
                card = card_for()
                if fits(card):
                    return card
            words, start, end = reply

            def words_fit(upto: int) -> bool:
                nonlocal reply
                reply = (words, start, upto)
                return fits(card_for())

            # The first sentence is always kept, fitting or not.
            reply = (words, start, longest(start, end - 1, words_fit) or start)
            return card_for()

        # Where the participant said the most, unless even cut down it does
        # not fit (one very long sentence): then the next, and so on.
        ordered = sorted(candidates, key=lambda i: -len(_normalize("".join(utterances[i].sentences))))
        cards_tried = (card_from(said) for said in ordered)
        plan.append(next((card for card in cards_tried if fits(card)), None) or card_from(ordered[0]))
    return plan


# ---------------------------------------------------------------------------
# The PDF


def _illustration_jpeg(name: str) -> tuple[bytes, tuple[int, int]] | None:
    from storybook_render import place_illustration

    path = find_illustration(name)
    if path is None:
        return None
    # The same cropping and flattening onto white as the book PDF.
    image, _ = place_illustration(path, (0, 0, 1, 1))
    encoded = io.BytesIO()
    image.save(encoded, "JPEG", quality=92)
    return encoded.getvalue(), image.size


def _wobbly_frame(pdf: Any, inset_mm: float, width_pt: float, seed: int,
                  page: tuple[float, float] = (CARD_W_MM, CARD_H_MM)) -> None:
    """A hand-drawn-looking rectangle, like the book cover's frame
    (storybook.frame_image), drawn as vectors so it prints sharp."""
    rnd = random.Random(seed)
    w, h = page[0] * MM, page[1] * MM
    x0, y0, x1, y1 = inset_mm * MM, inset_mm * MM, w - inset_mm * MM, h - inset_mm * MM
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    path = pdf.beginPath()
    first = True
    for (ax, ay), (bx, by) in zip(corners, corners[1:]):
        steps = max(2, int(max(abs(bx - ax), abs(by - ay)) / 14))
        for i in range(steps):
            t = i / steps
            point = (ax + (bx - ax) * t + rnd.uniform(-0.45, 0.45),
                     ay + (by - ay) * t + rnd.uniform(-0.45, 0.45))
            if first:
                path.moveTo(*point)
                first = False
            else:
                path.lineTo(*point)
    path.close()
    pdf.setLineWidth(width_pt)
    pdf.setLineJoin(1)
    pdf.setLineCap(1)
    pdf.drawPath(path, stroke=1, fill=0)


def _draw_picture(pdf: Any, scenes: list[Scene], scene: int | None, box: Box,
                  page: tuple[float, float] = (CARD_W_MM, CARD_H_MM)) -> None:
    if scene is None or not 0 <= scene < len(scenes) or not scenes[scene].illustration:
        return
    _draw_image(pdf, scenes[scene].illustration, box, page)


def _draw_image(pdf: Any, name: str, box: Box,
                page: tuple[float, float] = (CARD_W_MM, CARD_H_MM)) -> None:
    """An illustration fitted into ``box`` (mm from the top-left), centered."""
    from reportlab.lib.utils import ImageReader

    picture = _illustration_jpeg(name)
    if picture is None:
        return
    data, (w, h) = picture
    px, py, pw, ph = box
    scale = min(pw * MM / w, ph * MM / h)
    dw, dh = w * scale, h * scale
    pdf.drawImage(ImageReader(io.BytesIO(data)), px * MM + (pw * MM - dw) / 2,
                  page[1] * MM - py * MM - (ph * MM + dh) / 2, dw, dh)


def _draw_frame(pdf: Any, page: tuple[float, float] = (CARD_W_MM, CARD_H_MM),
                card: CardPlan | None = None) -> None:
    if card is not None and card.frame_style == "ribbon":
        _draw_ribbon(pdf, tuple(card.frame_color or (0.62, 0.76, 0.90)), page)
        return
    pdf.setStrokeColorRGB(*(card.frame_color if card is not None and card.frame_color
                            else HONEY))
    _wobbly_frame(pdf, 4.5, 1.6, seed=7, page=page)
    _wobbly_frame(pdf, 5.9, 0.6, seed=11, page=page)


RIBBON_INK = (0.18, 0.18, 0.22)  # the bow's own ink outline
RIBBON_INSET_MM = 5.6  # the ribbon's middle line, from the card's edge
RIBBON_WIDTH_MM = 2.2
RIBBON_WAVE_MM, RIBBON_WAVELENGTH_MM = 1.1, 46.0  # how it meanders
RIBBON_TWIST_MM = 38.0  # how far it runs between twists


def _ribbon_loop(page: tuple[float, float], step_mm: float = 0.5) -> list[tuple[float, float]]:
    """Points (mm, from the top-left) every step_mm round a rounded
    rectangle RIBBON_INSET_MM inside the card, clockwise, starting at its
    top-left corner (where the bow sits, hiding where the ribbon meets)."""
    import math

    inset, radius = RIBBON_INSET_MM, 5.0
    w, h = page
    x0, y0, x1, y1 = inset, inset, w - inset, h - inset
    outline: list[tuple[float, float]] = []

    def arc(cx: float, cy: float, start: float) -> None:
        for i in range(12):
            angle = math.radians(start + 90 * i / 12)
            outline.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))

    arc(x0 + radius, y0 + radius, 180)  # top-left (y grows downward)
    outline.append((x1 - radius, y0))
    arc(x1 - radius, y0 + radius, 270)
    outline.append((x1, y1 - radius))
    arc(x1 - radius, y1 - radius, 0)
    outline.append((x0 + radius, y1))
    arc(x0 + radius, y1 - radius, 90)
    outline.append(outline[0])
    points, carry = [], 0.0
    for (ax, ay), (bx, by) in zip(outline, outline[1:]):
        length = math.hypot(bx - ax, by - ay)
        t = carry
        while t < length:
            points.append((ax + (bx - ax) * t / length, ay + (by - ay) * t / length))
            t += step_mm
        carry = t - length
    points.append(outline[-1])
    return points


def _draw_ribbon(pdf: Any, color: tuple[float, float, float], page: tuple[float, float]) -> None:
    """A thin satin ribbon running round the card from the bow and back to
    it, one strand: it meanders a little and twists now and then, narrowing
    to an edge and showing its (darker) back until the next twist. Drawn as
    vectors, a band of short pieces between two hand-inked edges."""
    import math

    loop = _ribbon_loop(page)
    total = len(loop) - 1
    length = total * 0.5
    # Whole waves and twists round the loop, so it meets itself smoothly.
    waves = max(1, round(length / RIBBON_WAVELENGTH_MM))
    twists = max(2, round(length / RIBBON_TWIST_MM))
    left, right, faces = [], [], []
    for index, (x, y) in enumerate(loop):
        ahead = loop[min(index + 1, total)]
        behind = loop[max(index - 1, 0)]
        dx, dy = ahead[0] - behind[0], ahead[1] - behind[1]
        norm = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / norm, dx / norm  # pointing out of the card on the top edge
        s = index / total
        wave = RIBBON_WAVE_MM * math.sin(2 * math.pi * waves * s)
        phase = math.pi * twists * s
        half = RIBBON_WIDTH_MM / 2 * max(0.12, abs(math.cos(phase)))
        cx, cy = x + nx * wave, y + ny * wave
        left.append((cx + nx * half, cy + ny * half))
        right.append((cx - nx * half, cy - ny * half))
        faces.append(int((phase + math.pi / 2) // math.pi) % 2)
    back = tuple(c * 0.78 for c in color)
    to_pdf = lambda point: (point[0] * MM, (page[1] - point[1]) * MM)
    for index in range(total):
        piece = pdf.beginPath()
        piece.moveTo(*to_pdf(left[index]))
        piece.lineTo(*to_pdf(left[index + 1]))
        piece.lineTo(*to_pdf(right[index + 1]))
        piece.lineTo(*to_pdf(right[index]))
        piece.close()
        shade = back if faces[index] else color
        pdf.setFillColorRGB(*shade)
        pdf.setStrokeColorRGB(*shade)  # hide the seams between pieces
        pdf.setLineWidth(0.25)
        pdf.drawPath(piece, stroke=1, fill=1)
    pdf.setStrokeColorRGB(*RIBBON_INK)
    pdf.setLineWidth(0.4)
    pdf.setLineJoin(1)
    pdf.setLineCap(1)
    for edge in (left, right):
        line = pdf.beginPath()
        line.moveTo(*to_pdf(edge[0]))
        for point in edge[1:]:
            line.lineTo(*to_pdf(point))
        pdf.drawPath(line, stroke=1, fill=0)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setStrokeColorRGB(0, 0, 0)


def _draw_dots(pdf: Any, y_mm: float, x_mm: float = CARD_W_MM / 2,
               page: tuple[float, float] = (CARD_W_MM, CARD_H_MM)) -> None:
    pdf.setFillColorRGB(*HONEY)
    for dx in (-3.2, 0.0, 3.2):
        pdf.circle((x_mm + dx) * MM, page[1] * MM - y_mm * MM, 0.45 * MM, stroke=0, fill=1)


def _draw_chat(pdf: Any, rows: list[tuple[str, list[str]]], x: float, width: float, y: float,
               size: float, line_pt: float, height: float) -> float:
    """Pooh's lines from the left edge, the participant's to the right edge,
    in its own warmer ink; returns where the text ended (from the top)."""
    pdf.setFont("Yomogi", size)
    for row, (speaker, lines) in enumerate(rows):
        if row:
            y += CHAT_GAP * line_pt
        mine = speaker == "participant"
        pdf.setFillColorRGB(*(PARTICIPANT_INK if mine else INK_BROWN))
        for text in lines:
            baseline = height - y - size - (line_pt - size) / 2
            if mine:
                pdf.drawRightString(x + width, baseline, text)
            else:
                pdf.drawString(x, baseline, text)
            y += line_pt
    return y


def draw_dialogue_card(pdf: Any, card: CardPlan, scenes: list[Scene], index: int, cards: int,
                       title: str) -> None:
    """The picture as large as the exchange under it allows, and the
    exchange under it (see _draw_chat)."""
    _register_font()
    height = CARD_H_MM * MM
    _draw_frame(pdf, card=card)
    layout = dialogue_layout(card)
    if layout is None:  # check_dialogue_plan reported it; draw what fits
        layout = DialogueLayout(DIALOGUE_SIZES[-1], DIALOGUE_PICTURE_MIN, DIALOGUE_TOP, [])
    side = layout.picture_mm
    _draw_picture(pdf, scenes, card.picture_scene,
                  ((CARD_W_MM - side) / 2, layout.top_mm, side, side))
    _draw_dots(pdf, layout.top_mm + side + DIALOGUE_DIVIDER / 2)
    y = (layout.top_mm + side + DIALOGUE_DIVIDER) * MM  # from the top, in points
    size = layout.size
    line_pt = size * DIALOGUE_LEADING
    _draw_chat(pdf, layout.rows, DIALOGUE_TEXT_X * MM, DIALOGUE_TEXT_W * MM, y, size, line_pt,
               height)
    bow = _bow_box(card, (CARD_W_MM, CARD_H_MM))
    if bow is not None:
        _draw_rgba(pdf, card.frame_bow, bow, (CARD_W_MM, CARD_H_MM))
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setStrokeColorRGB(0, 0, 0)


# Landscape dialogue card (148 x 100 mm): the picture large on the left, and
# the exchange flowing through the white space around what is drawn in it,
# line by line, the way the text of an old picture book runs around its
# drawings. A line takes the widest free stretch at its height and skips
# heights where nothing wide enough is free, so it hops over the drawing.
FLOW_AREA: Box = (9.5, 9.5, 129.0, 81.0)  # where text may go: inside the frame
FLOW_PICTURE: Box = (7.5, 7.0, 88.0, 86.0)
FLOW_MARGIN_MM = 2.4  # text keeps this far from anything drawn
# The narrowest free stretch a line may use: narrower gaps (between a bush
# and the footprints, say) made lines of three or four characters.
FLOW_MIN_RUN_MM = 28.0
FLOW_SIZES = (9.5, 9.0, 8.5, 8.0)
# What gives way when the text does not fit: first a little text size, then
# the picture (to 90 %, 80 %), and only then the smallest text.
FLOW_FITS = ((1.0, 9.5), (1.0, 9.0), (0.9, 9.5), (0.9, 9.0), (0.8, 9.5), (0.8, 9.0),
             (0.8, 8.5), (0.8, 8.0))
# Text stays on the right part of the card (it may tuck into the picture's
# empty corners there, but never runs across above or below the drawing).
FLOW_TEXT_LEFT_MM = 58.0
FLOW_LEADING = 1.6
FLOW_GAP_LINES = 0.8  # between Pooh's line and the participant's
FLOW_GRID_MM = 0.5
FLOW_ORPHAN = 2  # a line never ends an utterance with this few characters alone
TRAIL_STRIP_MM = 15.0  # a footprint trail's strip down the right edge; no text in it


@dataclass
class FlowLine:
    speaker: str
    text: str
    x_mm: float
    width_mm: float
    top_mm: float


@dataclass
class FlowLayout:
    size: float
    picture: Box | None  # where the cropped picture is drawn
    item: Box | None
    lines: list[FlowLine]
    # Footprints: (picture, box, rotation in degrees).
    trail: list[tuple[str, Box, float]] = field(default_factory=list)
    bow: Box | None = None  # the ribbon frame's bow


_RGBA_CACHE: dict[str, Any] = {}


def _illustration_rgba(name: str) -> Any:
    """The picture cropped to what is drawn in it, keeping its transparency."""
    from PIL import Image

    if name not in _RGBA_CACHE:
        path = find_illustration(name)
        image = None
        if path is not None:
            image = Image.open(path).convert("RGBA")
            bbox = image.split()[-1].getbbox()
            if bbox:
                image = image.crop(bbox)
        _RGBA_CACHE[name] = image
    return _RGBA_CACHE[name]


def _fit_box(image: Any, box: Box, anchor: str = "center") -> Box:
    x, y, w, h = box
    scale = min(w / image.width, h / image.height)
    dw, dh = image.width * scale, image.height * scale
    left = x if anchor == "left" else x + w - dw if anchor == "right" else x + (w - dw) / 2
    return (left, y + (h - dh) / 2, dw, dh)


def _occupied(layers: list[tuple[Any, Box]]) -> Any:
    """Grid cells (FLOW_GRID_MM) covered by what is drawn, widened by
    FLOW_MARGIN_MM, over the whole card."""
    import numpy as np
    from PIL import Image, ImageFilter

    cols = round(DIALOGUE.page[0] / FLOW_GRID_MM)
    rows = round(DIALOGUE.page[1] / FLOW_GRID_MM)
    grid = Image.new("L", (cols, rows), 0)
    for image, (x, y, w, h) in layers:
        size = (max(1, round(w / FLOW_GRID_MM)), max(1, round(h / FLOW_GRID_MM)))
        alpha = image.split()[-1].resize(size, Image.BILINEAR).point(lambda a: 255 if a > 40 else 0)
        grid.paste(255, (round(x / FLOW_GRID_MM), round(y / FLOW_GRID_MM)), alpha)
    reach = 2 * round(FLOW_MARGIN_MM / FLOW_GRID_MM) + 1
    return np.asarray(grid.filter(ImageFilter.MaxFilter(reach))) > 0


def _free_runs(occupied: Any, top_mm: float, bottom_mm: float,
               area: Box = FLOW_AREA) -> list[tuple[float, float]]:
    """Free (start, width) stretches in mm across the text area at this height."""
    ax, _, aw, _ = area
    r0, r1 = int(top_mm / FLOW_GRID_MM), int(bottom_mm / FLOW_GRID_MM) + 1
    c0, c1 = round(ax / FLOW_GRID_MM), round((ax + aw) / FLOW_GRID_MM)
    blocked = occupied[r0:r1, c0:c1].any(axis=0)
    runs, start = [], None
    for index, cell in enumerate(list(blocked) + [True]):
        if not cell and start is None:
            start = index
        elif cell and start is not None:
            runs.append(((c0 + start) * FLOW_GRID_MM, (index - start) * FLOW_GRID_MM))
            start = None
    return [run for run in runs if run[1] >= FLOW_MIN_RUN_MM]


def _overlap(run: tuple[float, float], other: tuple[float, float]) -> float:
    return max(0.0, min(run[0] + run[1], other[0] + other[1]) - max(run[0], other[0]))


def _flow(rows: list[tuple[str, str]], occupied: Any, size: float, start_mm: float,
          measure: Measure, area: Box = FLOW_AREA) -> list[FlowLine] | None:
    """The utterances flowed down from start_mm, or None if they run out of
    room. Each utterance is one block: if it would have to jump over the
    drawing partway through, it starts again below it."""
    line_mm = size * FLOW_LEADING / MM
    bottom = area[1] + area[3]
    right = area[0] + area[2]
    y, lines = start_mm, []
    for row, (speaker, text) in enumerate(rows):
        if row:
            y += FLOW_GAP_LINES * line_mm
        begun = len(lines)
        rest, previous = text, None
        while rest:
            if y + line_mm > bottom:
                return None
            # Only stretches beside the picture, reaching the right edge:
            # never a gap on the far side of the drawing.
            runs = [run for run in _free_runs(occupied, y, y + line_mm, area)
                    if run[0] + run[1] >= right - FLOW_GRID_MM * 2]
            if runs:
                x, width = runs[0]
                if previous is not None:
                    # Keep the block's edge (the left one for Pooh, the
                    # right one for the participant) where there is room.
                    if speaker == "participant":
                        edge = previous[0] + previous[1]
                        if x + FLOW_MIN_RUN_MM <= edge < x + width:
                            width = edge - x
                    elif x < previous[0] <= x + width - FLOW_MIN_RUN_MM:
                        width, x = x + width - previous[0], previous[0]
            head = phrases(rest)[0]
            if not runs or (measure(head, size) > width * MM
                            and width < area[2] - FLOW_GRID_MM):
                # No room for the next phrase here: below the drawing, the
                # whole utterance again, so it is never cut in two.
                del lines[begun:]
                rest, previous = text, None
                y += line_mm / 2
                continue
            first = wrap_phrases(rest, size, width * MM, measure)[:1]
            after = storybook.text_after(rest, first)
            if 0 < len(after.strip()) <= FLOW_ORPHAN:
                # Not a lone 「う？」 on the next line: carry the last phrase
                # down with it.
                parts = phrases(first[0])
                if len(parts) > 1:
                    first = ["".join(parts[:-1])]
                    after = storybook.text_after(rest, first)
            lines.append(FlowLine(speaker, first[0], x, width, y))
            rest, previous = after, (x, width)
            y += line_mm
    return lines


def flow_layout(card: CardPlan, picture: str | None,
                measure: Measure = _pdf_measure) -> FlowLayout | None:
    """The picture as large and the text as large as fit together (in the
    order of FLOW_FITS), the text placed as near the middle of the card's
    height as it can."""
    trail = _trail(card)
    ax, ay, aw, ah = FLOW_AREA
    right = ax + aw - (TRAIL_STRIP_MM if trail else 0.0)
    area = (FLOW_TEXT_LEFT_MM, ay, right - FLOW_TEXT_LEFT_MM, ah)
    item_layer, item_box = [], None
    item = _illustration_rgba(card.item) if card.item and not trail else None
    if item is not None:
        side = card.item_size_mm
        left = card.item_anchor.endswith("left")
        top = card.item_anchor.startswith("top")
        item_box = _fit_box(item, (ax if left else ax + aw - side, ay if top else ay + ah - side,
                                   side, side), "left" if left else "right")
        item_layer = [(item, item_box)]
    bow_box = _bow_box(card)
    if bow_box is not None:
        item_layer.append((_illustration_rgba(card.frame_bow), bow_box))
    image = _illustration_rgba(picture) if picture else None
    rows = chat_rows(card.text)
    middle = ay + ah / 2
    for scale, size in FLOW_FITS:
        picture_box = None
        layers = list(item_layer)
        if image is not None:
            px, py, pw, ph = FLOW_PICTURE
            picture_box = _fit_box(image, (px, py + ph * (1 - scale) / 2, pw * scale, ph * scale),
                                   "left")
            layers.append((image, picture_box))
        occupied = _occupied(layers)
        best = None
        start = ay
        while start < ay + ah:
            lines = _flow(rows, occupied, size, start, measure, area)
            if lines is not None:
                end = lines[-1].top_mm + size * FLOW_LEADING / MM if lines else start
                offset = abs((lines[0].top_mm + end) / 2 - middle) if lines else 0.0
                if best is None or offset < best[0]:
                    best = (offset, lines)
            start += 1.0
        if best is not None:
            return FlowLayout(size, picture_box, item_box, best[1], trail, bow_box)
    return None


def _bow_box(card: CardPlan, page: tuple[float, float] = (CARD_H_MM, CARD_W_MM)) -> Box | None:
    """Where the ribbon frame's bow goes: over the ribbon at its corner."""
    if card.frame_style != "ribbon" or not card.frame_bow:
        return None
    image = _illustration_rgba(card.frame_bow)
    if image is None:
        return None
    side = card.frame_bow_size_mm
    left = card.frame_bow_anchor.endswith("left")
    top = card.frame_bow_anchor.startswith("top")
    x = 1.5 if left else page[0] - 1.5 - side
    y = 1.0 if top else page[1] - 1.0 - side
    return _fit_box(image, (x, y, side, side), "left" if left else "right")


def _trail(card: CardPlan) -> list[tuple[str, Box, float]]:
    """Footprints walking down the strip at the right edge, winding a little
    and stepping left and right in turn, the trail's pictures taking turns."""
    import math

    prints = [name for name in card.item_trail if _illustration_rgba(name) is not None]
    if not prints or card.item_count <= 0:
        return []
    ax, ay, aw, ah = FLOW_AREA
    center = ax + aw - TRAIL_STRIP_MM / 2
    step = ah / card.item_count
    size = card.item_size_mm
    steps = []
    for k in range(card.item_count):
        name = prints[k % len(prints)]
        side = -1 if k % 2 == 0 else 1
        x = center + card.item_wiggle_mm * math.sin(k * 1.1) + side * size * 0.3
        # Inside the strip, turned prints included (no text there).
        reach = size * 0.6
        x = min(max(x, ax + aw - TRAIL_STRIP_MM + reach), ax + aw - reach)
        y = ay + step * (k + 0.5)
        box = _fit_box(_illustration_rgba(name), (x - size / 2, y - size / 2, size, size))
        steps.append((name, box, side * 9.0 + 4.0 * math.sin(k * 2.3)))
    return steps


def _scene_picture(scenes: list[Scene], card: CardPlan) -> str | None:
    if card.picture_scene is None or not 0 <= card.picture_scene < len(scenes):
        return None
    return scenes[card.picture_scene].illustration


def dialogue_fits(card: CardPlan, style: Style, scenes: list[Scene],
                  measure: Measure = _pdf_measure) -> bool:
    if style.page[0] > style.page[1]:
        return flow_layout(card, _scene_picture(scenes, card), measure) is not None
    return dialogue_layout(card, measure) is not None


def _draw_rgba(pdf: Any, name: str, box: Box, page: tuple[float, float],
               angle: float = 0.0) -> None:
    """The picture in ``box`` (mm from the top-left), keeping its
    transparency, turned ``angle`` degrees about its center."""
    from reportlab.lib.utils import ImageReader

    image = _illustration_rgba(name)
    if image is None:
        return
    encoded = io.BytesIO()
    image.save(encoded, "PNG")
    encoded.seek(0)
    x, y, w, h = box
    pdf.saveState()
    pdf.translate((x + w / 2) * MM, (page[1] - y - h / 2) * MM)
    pdf.rotate(angle)
    pdf.drawImage(ImageReader(encoded), -w / 2 * MM, -h / 2 * MM, w * MM, h * MM, mask="auto")
    pdf.restoreState()


def draw_landscape_card(pdf: Any, card: CardPlan, scenes: list[Scene], index: int, cards: int,
                        title: str, page: tuple[float, float]) -> None:
    _register_font()
    height = page[1] * MM
    _draw_frame(pdf, page, card)
    picture = _scene_picture(scenes, card)
    layout = flow_layout(card, picture)
    if layout is None:  # check_dialogue_plan reported it; still show the picture
        image = _illustration_rgba(picture) if picture else None
        layout = FlowLayout(FLOW_SIZES[-1], _fit_box(image, FLOW_PICTURE, "left") if image else None,
                            None, [], _trail(card))
    if picture and layout.picture:
        _draw_rgba(pdf, picture, layout.picture, page)
    if card.item and layout.item:
        _draw_rgba(pdf, card.item, layout.item, page)
    for name, box, angle in layout.trail:
        _draw_rgba(pdf, name, box, page, angle)
    bow = layout.bow or _bow_box(card, page)
    if bow is not None:
        _draw_rgba(pdf, card.frame_bow, bow, page)
    size = layout.size
    line_pt = size * FLOW_LEADING
    pdf.setFont("Yomogi", size)
    for line in layout.lines:
        mine = line.speaker == "participant"
        pdf.setFillColorRGB(*(PARTICIPANT_INK if mine else INK_BROWN))
        baseline = height - line.top_mm * MM - size - (line_pt - size) / 2
        if mine:
            pdf.drawRightString((line.x_mm + line.width_mm) * MM, baseline, line.text)
        else:
            pdf.drawString(line.x_mm * MM, baseline, line.text)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setStrokeColorRGB(0, 0, 0)


def draw_card(pdf: Any, card: CardPlan, scenes: list[Scene], index: int, cards: int,
              title: str, style: Style = POEM) -> None:
    """One card at the PDF origin; boxes are in mm from its top-left."""
    from reportlab.lib.utils import ImageReader

    _register_font()
    width, height = CARD_W_MM * MM, CARD_H_MM * MM
    poem = style is POEM or style.name == "poem"
    ink = INK_BROWN if poem else (0, 0, 0)
    footer = FOOTER_BROWN if poem else (0.55, 0.55, 0.55)
    if poem:
        pdf.setStrokeColorRGB(*HONEY)
        _wobbly_frame(pdf, 4.5, 1.6, seed=7)
        _wobbly_frame(pdf, 5.9, 0.6, seed=11)
    px, py, pw, ph = style.picture
    if poem:
        # Three small honey dots between the picture and the poem.
        pdf.setFillColorRGB(*HONEY)
        for dx in (-3.2, 0.0, 3.2):
            pdf.circle(width / 2 + dx * MM, height - 96.0 * MM, 0.45 * MM, stroke=0, fill=1)
    if style.title_on_first and index == 0 and title:
        pdf.setFillColorRGB(*ink)
        pdf.setFont("Yomogi", 13)
        pdf.drawCentredString(width / 2, height - py * MM - 13, title)
        py += 7.3
        ph -= 0
    picture = None
    if card.picture_scene is not None and 0 <= card.picture_scene < len(scenes):
        name = scenes[card.picture_scene].illustration
        picture = _illustration_jpeg(name) if name else None
    if picture is not None:
        data, (w, h) = picture
        scale = min(pw * MM / w, ph * MM / h)
        dw, dh = w * scale, h * scale
        pdf.drawImage(ImageReader(io.BytesIO(data)), px * MM + (pw * MM - dw) / 2,
                      height - py * MM - (ph * MM + dh) / 2, dw, dh)
    is_last = index == cards - 1
    size = fit_size(card.text, index, is_last, style) or style.body_pt_min
    tx, ty, tw, th = _text_box(style, index)
    text = _with_closing(card.text, style, is_last)
    paragraphs = wrap_card_text(text, size, tw * MM)
    line = size * style.leading
    used = _text_height(paragraphs, size, style)
    # A poem sits in the middle of its space; prose starts at the top.
    top = ty * MM + ((th * MM - used) / 2 if style.centered else 0.0)
    # reportlab places a line by its baseline; leave half the leading above.
    baseline = height - top - size - (line - size) / 2
    pdf.setFillColorRGB(*ink)
    pdf.setFont("Yomogi", size)
    lines_left = sum(len(p) for p in paragraphs)
    for paragraph_index, paragraph in enumerate(paragraphs):
        for text_line in paragraph:
            lines_left -= 1
            centered = style.centered or (style.closing and is_last and lines_left == 0
                                          and paragraph_index == len(paragraphs) - 1)
            if centered:
                pdf.drawCentredString((tx + tw / 2) * MM, baseline, text_line)
            else:
                pdf.drawString(tx * MM, baseline, text_line)
            baseline -= line
        baseline -= style.paragraph_gap * line
    pdf.setFillColorRGB(*footer)
    pdf.setFont("Yomogi", 6.5)
    bottom = (8.2 if poem else 3.6) * MM
    if poem and title:
        pdf.drawString(9.0 * MM, bottom, title)
    pdf.drawRightString(width - (9.0 if poem else 6.0) * MM, bottom, f"{index + 1} / {cards}")
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setStrokeColorRGB(0, 0, 0)


def paper_page(style: Style, paper: str = DEFAULT_PAPER) -> tuple[float, float]:
    """The sheet (width, height in mm), turned the way the style's cards are."""
    short, long = PAPER_SIZES[paper]
    return (long, short) if style.page[0] > style.page[1] else (short, long)


def write_pdf(plan: list[CardPlan], scenes: list[Scene], title: str, path: Path,
              style: Style = POEM, paper: str = DEFAULT_PAPER) -> Path:
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    sheet = paper_page(style, paper)
    margin = PAPER_SAFE_MARGIN_MM.get(paper, 0.0)
    scale = min((sheet[0] - 2 * margin) / style.page[0], (sheet[1] - 2 * margin) / style.page[1])
    pdf = canvas.Canvas(str(path), pagesize=(sheet[0] * MM, sheet[1] * MM))
    pdf.setTitle(f"{title}（ポストカード）" if title else "ポストカード")
    for index, card in enumerate(plan):
        pdf.saveState()
        # The card as laid out (postcard size), scaled to the sheet, centered.
        pdf.translate((sheet[0] - style.page[0] * scale) / 2 * MM,
                      (sheet[1] - style.page[1] * scale) / 2 * MM)
        pdf.scale(scale, scale)
        if style.dialogue and style.page[0] > style.page[1]:
            draw_landscape_card(pdf, card, scenes, index, len(plan), title, style.page)
        elif style.dialogue:
            draw_dialogue_card(pdf, card, scenes, index, len(plan), title)
        else:
            draw_card(pdf, card, scenes, index, len(plan), title, style)
        pdf.restoreState()
        pdf.showPage()
    pdf.save()
    return path


# ---------------------------------------------------------------------------


def default_pdf_path(log: Path) -> Path:
    """Next to the books: ``postcards/`` in the log directory."""
    log_dir = Path(os.getenv("POOH_LOG_DIR", str(storybook.PROJECT_ROOT / "logs")))
    return log_dir / "postcards" / f"{log.stem}.pdf"


def plan_to_json(plan: list[CardPlan], style: Style, source: str, errors: list[str]) -> str:
    return json.dumps({"style": style.name, "source": source, "remaining_errors": errors,
                       "cards": [asdict(card) for card in plan]},
                      ensure_ascii=False, indent=2)


def plan_from_json(text: str) -> tuple[list[CardPlan], Style | None]:
    data = json.loads(text)
    known = set(CardPlan.__dataclass_fields__)
    cards = [CardPlan(**{k: v for k, v in card.items() if k in known}) for card in data["cards"]]
    return cards, STYLES.get(data.get("style", ""))


def _check(plan: list[CardPlan], scenes: list[Scene], cards: int, style: Style) -> list[str]:
    if style.dialogue:
        return check_dialogue_plan(plan, scenes, cards, style)
    return check_plan(plan, scenes, cards, style)


def _extractive(scenes: list[Scene], cards: int, style: Style) -> list[CardPlan]:
    if style.dialogue:
        return extractive_dialogue_plan(scenes, cards, style)
    return extractive_plan(scenes, cards, style)


def main() -> int:
    from storybook_render import build_from_log

    parser = argparse.ArgumentParser(description="セッションの絵本をポストカードにまとめる")
    parser.add_argument("log", type=Path)
    parser.add_argument("--cards", type=int, default=DEFAULT_CARDS, help="カードの枚数（既定3）")
    parser.add_argument("--style", choices=sorted(STYLES), default=None,
                        help=f"dialogue: 横向き、絵の横に小物と実際のやりとり1往復／"
                             f"dialogue_portrait: その縦向き／poem: 短い詩／"
                             f"prose: 短い文章でまとめる"
                             f"（既定 {DEFAULT_STYLE}。--plan ではプランの値）")
    parser.add_argument("--paper", choices=sorted(PAPER_SIZES), default=DEFAULT_PAPER,
                        help="印刷する紙: hagaki（はがき 100×148mm、既定）／l（L判 89×119mm、SELPHY の L）。"
                             "同じカードを紙に合わせて縮小する")
    parser.add_argument("--out", type=Path, default=None,
                        help="PDFの出力先（既定: <ログディレクトリ>/postcards/<ログ名>.pdf）")
    parser.add_argument("--plan", type=Path, default=None,
                        help="保存済み（手で直した）プランJSONから描く。LMは呼ばない")
    parser.add_argument("--no-llm", action="store_true",
                        help="LMを使わず、絵本の文だけで作る")
    parser.add_argument("--png", type=Path, default=None, metavar="DIR",
                        help="確認用に、カードを300dpiのPNGでもDIRに書き出す（pdftoppmが必要）")
    args = parser.parse_args()

    builder = build_from_log(args.log, "portrait")
    title = builder.config.title
    scenes = scenes_from_book(builder)
    out = args.out or default_pdf_path(args.log)
    style = STYLES[args.style or DEFAULT_STYLE]
    if args.plan is not None:
        plan, saved_style = plan_from_json(args.plan.read_text(encoding="utf-8"))
        style = STYLES[args.style] if args.style else (saved_style or style)
        source = "plan"
        errors = _check(plan, scenes, len(plan), style)
    elif args.no_llm:
        plan, source = _extractive(scenes, args.cards, style), "extractive"
        errors = _check(plan, scenes, args.cards, style)
    else:
        plan, errors = (lm_dialogue_plan(scenes, title, args.cards, style) if style.dialogue
                        else lm_plan(scenes, title, args.cards, style))
        source = "lm"
        if errors:
            print("[ポストカード] LMの案が条件を満たしませんでした。LMを使わずに作ります:\n  "
                  + "\n  ".join(errors), file=sys.stderr)
            plan, source = _extractive(scenes, args.cards, style), "extractive"
            errors = _check(plan, scenes, args.cards, style)
    for error in errors:
        print(f"[ポストカード] 注意: {error}", file=sys.stderr)
    write_pdf(plan, scenes, title, out, style, args.paper)
    if args.plan is None:
        out.with_suffix(".json").write_text(plan_to_json(plan, style, source, errors),
                                            encoding="utf-8")
    print(f"{len(plan)} 枚のポストカード（{style.name}・{PAPER_LABELS[args.paper]}・{source}）を書き出しました: {out}")
    if args.png is not None:
        import subprocess

        args.png.mkdir(parents=True, exist_ok=True)
        subprocess.run(["pdftoppm", "-r", "300", "-png", str(out), str(args.png / "card")],
                       check=True)
        print(f"PNG: {args.png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
