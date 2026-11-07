#!/usr/bin/env python3
"""The small item pictures the postcards show beside their text.

    python scripts/postcard_items.py --list                         # which item each scene shows
    python scripts/postcard_items.py --scenario footprints          # make the missing ones
    python scripts/postcard_items.py --scenario eeyore_birthday --force honey_pot

Each scenario has its own file, ``config/postcard/<scenario>.json`` (a new
scenario only needs a new file):

- ``items``: name -> one of
  - ``{"file": ...}``: an existing picture, used as it is (nothing to make);
  - ``{"combine": [{"file": ..., "half": "left" | "right"}, ...]}``: those
    halves of existing pictures side by side, cut and pasted, not drawn;
  - ``{"prompt": ...}``: drawn once per scenario (not per session) in the
    style of the scenario's ``style_reference`` picture, on a transparent
    background;
  - ``{"trail": [item, ...]}``: those items (each one of the above) as
    footprints walking down the card's right edge in a winding line, taking
    turns; ``count`` prints, ``wiggle_mm`` from side to side.
  Any item may also set ``anchor`` and ``size_mm`` (see below; for a trail,
  ``size_mm`` is one print's).
- ``beats``: scene (beat) -> item name; ``null`` for none on a card whose
  picture is that beat's (e.g. a picture already full of footprints).
- ``placement``: the default ``anchor`` (top_right / top_left / bottom_right
  / bottom_left, inside the card's frame) and ``size_mm`` of the items.
- ``frame`` (optional): the card's frame. ``{"style": "lines", "color":
  [r, g, b]}`` is a hand-drawn double line of that color (honey, by
  default, without a ``frame``); ``{"style": "ribbon", "color":
  [r, g, b], "bow": item, "bow_anchor": ..., "bow_size_mm": ...}`` is a
  ribbon of that color all round, with that item (a bow) on one corner.

Made items are saved as 256-color PNGs like the book's other bundled
pictures: ``assets/storybook/<scenario>/postcard_items/<item>.png``.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import storybook

CONFIG_DIR = storybook.PROJECT_ROOT / "config" / "postcard"
ANCHORS = ("top_right", "top_left", "bottom_right", "bottom_left")
DEFAULT_PLACEMENT = {"anchor": "top_right", "size_mm": 16.0}
ITEM_STYLE = (
    "A single small object drawn as a spot illustration for a children's picture book, in "
    "exactly the style of the reference image: irregular black pen-and-ink contours and "
    "watercolor grain inside the object, the same warm palette. The object alone, centered, "
    "filling most of the frame, on a genuinely transparent background. No bear, no people, "
    "no hands, no ground patch, no shadow on a floor, no frame, no text or lettering."
)


@dataclass(frozen=True)
class Item:
    name: str
    picture: str  # for storybook.find_illustration(); "" for a trail
    anchor: str
    size_mm: float
    # A trail: its prints' pictures (taking turns), how many, how winding.
    trail: tuple[str, ...] = ()
    count: int = 0
    wiggle_mm: float = 0.0


@dataclass(frozen=True)
class Frame:
    style: str = "lines"  # "lines" | "ribbon"
    color: tuple[float, float, float] = (0.80, 0.58, 0.25)
    bow: str = ""  # picture for storybook.find_illustration(); "" for none
    bow_anchor: str = "top_left"
    bow_size_mm: float = 24.0


def load_frame(scenario: str) -> Frame:
    """The scenario's card frame (config ``frame``), the honey one by default."""
    config = load_scenario(scenario) or {}
    spec = config.get("frame") or {}
    if spec.get("style") != "ribbon":
        return Frame("lines", tuple(spec.get("color", Frame.color)))
    bow = ""
    name = spec.get("bow")
    item = config.get("items", {}).get(name) if name else None
    if isinstance(item, dict):
        picture = item_picture(scenario, name, item)
        bow = picture if storybook.find_illustration(picture) else ""
    anchor = spec.get("bow_anchor", "top_left")
    return Frame("ribbon", tuple(spec.get("color", (0.62, 0.76, 0.90))), bow,
                 anchor if anchor in ANCHORS else "top_left", float(spec.get("bow_size_mm", 24.0)))


def scenarios() -> list[str]:
    return sorted(path.stem for path in CONFIG_DIR.glob("*.json"))


def load_scenario(scenario: str) -> dict | None:
    path = CONFIG_DIR / f"{scenario}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def item_picture(scenario: str, item: str, spec: dict) -> str:
    """The item's path for storybook.find_illustration()."""
    if isinstance(spec.get("file"), str):
        return spec["file"]
    return f"{scenario}/postcard_items/{item}.png"


def item_for_beat(scenario: str, beat_id: str | None) -> Item | None:
    """The beat's item and where it goes, if one is configured and exists."""
    config = load_scenario(scenario)
    if not config or not beat_id:
        return None
    name = config.get("beats", {}).get(beat_id)
    items = config.get("items", {})
    spec = items.get(name) if name else None
    if not isinstance(spec, dict):
        return None
    placement = {**DEFAULT_PLACEMENT, **config.get("placement", {}), **{
        key: spec[key] for key in ("anchor", "size_mm") if key in spec}}
    anchor = placement["anchor"] if placement["anchor"] in ANCHORS else "top_right"
    if "trail" in spec:
        prints = tuple(item_picture(scenario, part, items.get(part, {}))
                       for part in spec["trail"])
        if not prints or any(storybook.find_illustration(p) is None for p in prints):
            return None
        return Item(name, "", anchor, float(spec.get("size_mm", 7.0)), prints,
                    int(spec.get("count", 6)), float(spec.get("wiggle_mm", 2.5)))
    picture = item_picture(scenario, name, spec)
    if storybook.find_illustration(picture) is None:
        return None
    return Item(name, picture, anchor, float(placement["size_mm"]))


def beat_hides_item(scenario: str, beat_id: str | None) -> bool:
    """Whether the scenario lists this beat with no item (``null``): a card
    showing its picture then gets none, not another scene's."""
    beats = (load_scenario(scenario) or {}).get("beats", {})
    return bool(beat_id) and beat_id in beats and not beats[beat_id]


def combine_halves(parts: list[dict], output: Path) -> None:
    """The given halves of existing pictures side by side, each cropped to
    what is drawn in it, on a transparent background."""
    from PIL import Image

    pieces = []
    for part in parts:
        path = storybook.find_illustration(part["file"])
        if path is None:
            raise FileNotFoundError(part["file"])
        image = Image.open(path).convert("RGBA")
        half = image.width // 2
        image = image.crop((0, 0, half, image.height) if part.get("half") == "left"
                           else (half, 0, image.width, image.height))
        bbox = image.split()[-1].getbbox()
        pieces.append(image.crop(bbox) if bbox else image)
    gap = max(piece.width for piece in pieces) // 8
    height = max(piece.height for piece in pieces)
    canvas = Image.new("RGBA", (sum(p.width for p in pieces) + gap * (len(pieces) - 1), height),
                       (255, 255, 255, 0))
    x = 0
    for piece in pieces:
        canvas.paste(piece, (x, (height - piece.height) // 2), piece)
        x += piece.width + gap
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.quantize(colors=256).save(output)


def draw_item(prompt: str, style_reference: Path, output: Path) -> None:
    from openai import OpenAI
    from PIL import Image

    from storybook_illustrations import MODEL

    with OpenAI(timeout=180.0, max_retries=1) as client, contextlib.ExitStack() as stack:
        result = client.images.edit(
            model=os.getenv("STORYBOOK_IMAGE_MODEL", MODEL),
            image=[stack.enter_context(style_reference.open("rb"))],
            prompt=prompt,
            size="1024x1024",
            quality="medium",
            background="transparent",
            output_format="png",
        )
    image = Image.open(io.BytesIO(base64.b64decode(result.data[0].b64_json))).convert("RGBA")
    bbox = image.split()[-1].getbbox()
    if bbox:
        image = image.crop(bbox)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Same size class as the bundled pictures (see docs/storybook.md).
    image.quantize(colors=256).save(output)


def list_items() -> None:
    for scenario in scenarios():
        config = load_scenario(scenario) or {}
        print(f"■ {scenario}（config/postcard/{scenario}.json）")
        for beat, name in config.get("beats", {}).items():
            if not name:
                print(f"  {beat:22} （なし：この場面の絵のカードには小物を出さない）")
                continue
            item = item_for_beat(scenario, beat)
            spec = config.get("items", {}).get(name, {})
            kind = next((key for key in ("file", "combine", "prompt", "trail") if key in spec), "?")
            if item is None:
                where = "（まだない）"
            elif item.trail:
                where = f"右端に{item.count}個 {item.size_mm:g}mm  " + " / ".join(item.trail)
            else:
                where = f"{item.anchor} {item.size_mm:g}mm  {item.picture}"
            print(f"  {beat:22} {name:16} {kind:8} {where}")


def main() -> int:
    parser = argparse.ArgumentParser(description="ポストカードの小物イラスト")
    parser.add_argument("--list", action="store_true", help="各シナリオの場面ごとの小物を表示する")
    parser.add_argument("--scenario", choices=scenarios())
    parser.add_argument("--force", nargs="*", default=None, metavar="ITEM",
                        help="作り直す小物（名前なしなら全部）")
    args = parser.parse_args()
    if args.list or not args.scenario:
        list_items()
        return 0
    config = load_scenario(args.scenario) or {}
    for item, spec in config.get("items", {}).items():
        if "file" in spec or "trail" in spec:
            continue  # used as it is / made of other items
        output = storybook.ILLUSTRATION_ROOT / item_picture(args.scenario, item, spec)
        forced = args.force is not None and (not args.force or item in args.force)
        if output.exists() and not forced:
            continue
        print(f"[小物] {item} を作っています…", flush=True)
        try:
            if "combine" in spec:
                combine_halves(spec["combine"], output)
            else:
                reference = storybook.find_illustration(config.get("style_reference", ""))
                if reference is None:
                    raise FileNotFoundError(f"style_reference: {config.get('style_reference')}")
                draw_item(f"{spec['prompt']}\n{config.get('item_style', ITEM_STYLE)}",
                          reference, output)
        except Exception as exc:  # one refusal must not stop the rest
            print(f"[小物] {item} を作れませんでした: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        print(f"[小物] {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
