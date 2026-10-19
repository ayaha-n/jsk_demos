#!/usr/bin/env python3
"""Render a session log as a picture book: a PDF, and page PNGs on request.

    python scripts/storybook_render.py logs/saved_examples/<log>.jsonl
    python scripts/storybook_render.py <log>.jsonl --layout portrait --out /tmp/book.pdf --png /tmp/pages

The pages use the geometry and font of the live e-ink book, so they show
what the participant saw on the tablet (typeset rather than handwritten).
In the PDF the text is real text in the same font, so it stays sharp and
can be searched; only the illustrations are images.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

import storybook
from storybook import LAYOUTS, PageLayout, StoryPage, find_illustration, line_height

BG = (255, 255, 255)
INK = (0, 0, 0)
PLACEHOLDER = (200, 200, 200)


def fit_illustration(path: Path, box: storybook.Box) -> tuple[Image.Image, tuple[int, int]]:
    """The illustration scaled into ``box``, flattened onto the page white,
    and where its top-left goes (centered in the box)."""
    image, (x, y, w, h) = place_illustration(path, box)
    return image.resize((w, h), Image.LANCZOS), (x, y)


def place_illustration(path: Path, box: storybook.Box) -> tuple[Image.Image, storybook.Box]:
    """The illustration cropped to its subject and flattened onto the page
    white, at its own resolution, and the rectangle it fills, centered in
    ``box`` the way the e-ink viewer centers it."""
    x, y, w, h = box
    image = Image.open(path)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        bbox = image.split()[-1].getbbox()
        if bbox:
            image = image.crop(bbox)
        canvas = Image.new("RGB", image.size, BG)
        canvas.paste(image, mask=image.split()[-1])
        image = canvas
    else:
        image = image.convert("RGB")
    scale = min(w / image.width, h / image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image, (x + (w - size[0]) // 2, y + (h - size[1]) // 2, *size)


def render_page(page: StoryPage, layout: PageLayout, *, page_number: bool = True) -> Image.Image:
    canvas = Image.new("RGB", layout.size, BG)
    draw = ImageDraw.Draw(canvas)
    if page.illustration:
        path = find_illustration(page.illustration)
        if path is not None:
            image, origin = fit_illustration(path, layout.picture_box)
            canvas.paste(image, origin)
        else:
            x, y, w, h = layout.picture_box
            draw.rectangle((x, y, x + w, y + h), outline=PLACEHOLDER, width=4)
            draw.text((x + 30, y + 30), f"(さしえ: {page.illustration})",
                      font=storybook._font(40), fill=PLACEHOLDER)
    for item in page.placed:
        font = storybook._font(item.px)
        for index, text in enumerate(item.lines):
            dx = 0
            if item.align == "center":
                dx = max(0, int((item.w - font.getlength(text)) / 2))
            draw.text((item.x + dx, item.y + index * line_height(item.px)), text,
                      font=font, fill=INK)
    if page_number:
        width, height = layout.size
        label = str(page.index + 1)
        font = storybook._font(36)
        draw.text(((width - font.getlength(label)) / 2, height - 60), label,
                  font=font, fill=PLACEHOLDER)
    return canvas


def read_log(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def build_from_log(path: Path, layout: str = "portrait") -> storybook.StoryBuilder:
    records = read_log(path)
    if not records:
        raise ValueError(f"空のログです: {path}")
    from scenarios import get_scenario

    scenario = get_scenario(records[0]["scenario"])
    return storybook.build_book(
        records, scenario=scenario.key, opening_line=scenario.opening_line, layout=layout
    )


def write_pdf(builder: storybook.StoryBuilder, path: Path) -> Path:
    """The book as a PDF, one page per tablet page, 1 pt per panel pixel."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    if "Yomogi" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Yomogi", str(storybook.FONT_PATH)))
    width, height = builder.layout.size
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=(width, height))
    pdf.setTitle(builder.config.title or "絵本")
    for page in builder.pages:
        illustration = find_illustration(page.illustration) if page.illustration else None
        if illustration is not None:
            image, (x, y, w, h) = place_illustration(illustration, builder.layout.picture_box)
            # At the panel's own resolution, as JPEG: the PDF stays small.
            encoded = io.BytesIO()
            image.resize((w, h), Image.LANCZOS).save(encoded, "JPEG", quality=90)
            encoded.seek(0)
            pdf.drawImage(ImageReader(encoded), x, height - y - h, w, h)
        for item in page.placed:
            font = storybook._font(item.px)
            em = font.size
            ascent = font.getmetrics()[0]
            pdf.setFont("Yomogi", em)
            for index, text in enumerate(item.lines):
                # PIL (and the viewer) place a line by its top; PDF by its
                # baseline, from the bottom of the page.
                baseline = height - (item.y + index * line_height(item.px) + ascent)
                if item.align == "center":
                    pdf.drawCentredString(item.x + item.w / 2, baseline, text)
                else:
                    pdf.drawString(item.x, baseline, text)
        pdf.setFont("Yomogi", 26)
        pdf.setFillGray(0.75)
        pdf.drawCentredString(width / 2, 30, str(page.index + 1))
        pdf.setFillGray(0)
        pdf.showPage()
    pdf.save()
    return path


def write_pngs(builder: storybook.StoryBuilder, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for page in builder.pages:
        path = out_dir / f"page_{page.index + 1:02d}.png"
        render_page(page, builder.layout).save(path)
        paths.append(path)
    return paths


def default_pdf_path(log: Path) -> Path:
    """Where a session's book is kept: ``storybooks/`` in the log directory
    (``POOH_LOG_DIR``), named after the log."""
    log_dir = Path(os.getenv("POOH_LOG_DIR", str(storybook.PROJECT_ROOT / "logs")))
    return log_dir / "storybooks" / f"{log.stem}.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(description="セッションログを絵本にする")
    parser.add_argument("log", type=Path)
    parser.add_argument("--layout", choices=sorted(LAYOUTS), default="landscape",
                        help="タブレットの向き（デフォルト: landscape）")
    parser.add_argument("--out", type=Path, default=None,
                        help="PDFの出力先（デフォルト: <ログディレクトリ>/storybooks/<ログ名>.pdf）")
    parser.add_argument("--png", type=Path, default=None, metavar="DIR",
                        help="ページごとのPNGもDIRに書き出す")
    parser.add_argument("--generate-illustrations", action="store_true",
                        help="会話から挿絵を生成してから絵本を書き出す（生成済みは再利用）")
    args = parser.parse_args()
    if args.generate_illustrations:
        from storybook_illustrations import IllustrationGenerationError, generate_from_log

        try:
            generate_from_log(args.log)
        except IllustrationGenerationError as exc:
            print(f"[絵本] {exc}。保存済みの挿絵・既存絵でPDF作成を続けます。", file=sys.stderr)
    builder = build_from_log(args.log, args.layout)
    pdf = write_pdf(builder, args.out or default_pdf_path(args.log))
    print(f"{len(builder.pages)} ページの絵本を書き出しました: {pdf}")
    if args.png is not None:
        write_pngs(builder, args.png)
        print(f"PNG: {args.png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
