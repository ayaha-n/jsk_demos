#!/usr/bin/env python3
"""Generate the picture book's illustrations, one per story beat.

    python scripts/storybook_illustrations.py --scenario eeyore_birthday
    python scripts/storybook_illustrations.py --scenario eeyore_birthday --only pooh_ate_honey --force

Illustrations are drawn ahead of time, not during a session: an image takes
far longer than a turn, and every participant should see the same pages.
Each beat's ``prompt`` and the shared ``illustration_style`` come from
``config/storybook.json``.  ``assets/storybook/bear-reference.png`` (the
plush bear, drawn the way the illustrations show him) is the reference
image, so Pooh looks the same on every page.
Output goes to ``assets/storybook/<beat illustration path>``.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import storybook

REFERENCE_IMAGE = storybook.ILLUSTRATION_ROOT / "bear-reference.png"
MODEL = "gpt-image-1"


def generate(prompt: str, output: Path, reference: Path = REFERENCE_IMAGE) -> None:
    from openai import OpenAI

    client = OpenAI()
    with reference.open("rb") as image:
        result = client.images.edit(
            model=MODEL,
            image=image,
            prompt=prompt,
            size="1024x1024",
            quality="medium",
            background="transparent",
            output_format="png",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(base64.b64decode(result.data[0].b64_json))


def main() -> int:
    parser = argparse.ArgumentParser(description="絵本のさしえを生成する")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--only", action="append", default=[], metavar="BEAT")
    parser.add_argument("--force", action="store_true", help="既存のさしえも描き直す")
    args = parser.parse_args()
    config = storybook.load_story_config(args.scenario)
    if not config.beats:
        print(f"エラー: {args.scenario} の絵本設定がありません", file=sys.stderr)
        return 1
    failures = 0
    for beat in config.beats.values():
        if args.only and beat.beat_id not in args.only:
            continue
        if not beat.illustration or not beat.prompt:
            continue
        output = storybook.ILLUSTRATION_ROOT / beat.illustration
        if output.exists() and not args.force:
            print(f"スキップ（既存）: {output}")
            continue
        print(f"生成中: {beat.beat_id} -> {output}")
        try:
            generate(" ".join(filter(None, (
                beat.prompt, config.participant_presence, config.illustration_style,
            ))), output)
        except Exception as exc:
            # The image API sometimes rejects a request at random; rerun to retry.
            print(f"  失敗: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
    if failures:
        print(f"{failures} 枚のさしえを生成できませんでした", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
