#!/usr/bin/env python3
"""Print the postcards on the Canon SELPHY CP1500, borderless.

    python scripts/print_postcards.py                 # the newest postcards: preview, then ask
    python scripts/print_postcards.py <cards.pdf> --cards 1
    python scripts/print_postcards.py --preview-only

Sends straight to the printer over IPP, not through a CUPS queue: CUPS's
driverless queue for this printer converts to Apple raster (URF) with a
filter that fails on every page ("サポートされていないラスターデータです"),
and a JPEG through the queue is refused by the printer. Here each card is
drawn at the printer's resolution to exactly the loaded paper, turned to
the paper's direction, written as PWG raster (what the printer lists:
300 dpi, rgb_8), and sent with ``ipptool`` naming the borderless paper.
Works from any PC on the same network with ``pdftoppm`` (poppler-utils)
and ``ipptool`` (cups-ipp-utils); see docs/printing.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "printer.json"
# This PC's own record of the printer's address (not committed).
LOCAL_PATH = PROJECT_ROOT / ".tools" / "printer.json"
DPI = 300
MM_PER_INCH = 25.4
PT_PER_MM = 72 / MM_PER_INCH


def load_printer(path: Path = CONFIG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_printer(name: str) -> str | None:
    """The printer's IPP address on the local network (mDNS), by name."""
    try:
        found = subprocess.run(["ippfind", "-T", "10"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return next((uri for uri in found.split() if name.lower() in uri.lower()), None)


def printer_uri(printer: dict, given: str | None = None, local: Path = LOCAL_PATH,
                find=find_printer) -> str | None:
    """--printer, else the address this PC remembered, else found and remembered."""
    if given:
        return given
    try:
        remembered = json.loads(local.read_text(encoding="utf-8")).get("uri")
    except (OSError, ValueError, AttributeError):
        remembered = None
    if remembered:
        return remembered
    uri = find(printer["find_name"])
    if uri:
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(json.dumps({"uri": uri}, indent=2) + "\n", encoding="utf-8")
    return uri


def paper_for(page_mm: tuple[float, float], papers: dict) -> str | None:
    """The paper whose size matches the PDF page (either way round)."""
    short, long = sorted(page_mm)
    for name, paper in papers.items():
        if abs(paper["width_mm"] - short) < 1.0 and abs(paper["height_mm"] - long) < 1.0:
            return name
    return None


def pixels(paper: dict) -> tuple[int, int]:
    """The paper in printer pixels (portrait: width < height)."""
    return (round(paper["width_mm"] / MM_PER_INCH * DPI),
            round(paper["height_mm"] / MM_PER_INCH * DPI))


# --- PWG raster (PWG 5102.4) -------------------------------------------------

def pwg_header(width: int, height: int, paper: dict, pages: int = 1) -> bytes:
    """A page header for 8-bit sRGB at DPI, as the printer lists it."""
    header = bytearray(1796)

    def put(offset: int, value: int) -> None:
        header[offset:offset + 4] = struct.pack(">I", value)

    def text(offset: int, value: str) -> None:
        data = value.encode("ascii")[:63]
        header[offset:offset + len(data)] = data

    text(0, "PwgRaster")
    put(276, DPI)
    put(280, DPI)  # HWResolution
    put(340, 1)  # NumCopies
    put(352, round(paper["width_mm"] * PT_PER_MM))
    put(356, round(paper["height_mm"] * PT_PER_MM))  # PageSize, points
    put(372, width)
    put(376, height)
    put(384, 8)  # BitsPerColor
    put(388, 24)  # BitsPerPixel
    put(392, width * 3)  # BytesPerLine
    put(400, 19)  # ColorSpace: sRGB
    put(420, 3)  # NumColors
    put(452, 0)  # TotalPageCount: unknown, as CUPS writes it (what printed)
    put(456, 1)
    put(460, 1)  # Cross/FeedTransform
    put(480, 0xFFFFFF)  # AlternatePrimary (white)
    text(1732, paper["pwg_size_name"])
    return bytes(header)


def _encode_line(line: bytes) -> bytes:
    """One line of 3-byte pixels: runs (0-127: next pixel n+1 times) and
    literals (129-255: 257-n pixels follow)."""
    out = bytearray()
    px = [line[i:i + 3] for i in range(0, len(line), 3)]
    i, n = 0, len(px)
    while i < n:
        run = 1
        while i + run < n and run < 128 and px[i + run] == px[i]:
            run += 1
        if run > 1:
            out.append(run - 1)
            out += px[i]
            i += run
            continue
        start = i
        while i < n and i - start < 128 and not (i + 1 < n and px[i + 1] == px[i]):
            i += 1
        count = i - start
        if count == 1:
            out.append(0)
        else:
            out.append(257 - count)
        out += b"".join(px[start:i])
    return bytes(out)


def encode_page(rgb: bytes, width: int, height: int) -> bytes:
    """The page's bitmap, compressed line by line; repeated lines once."""
    out = bytearray()
    stride = width * 3
    y = 0
    while y < height:
        line = rgb[y * stride:(y + 1) * stride]
        repeat = 0
        while y + repeat + 1 < height and repeat < 255 and \
                rgb[(y + repeat + 1) * stride:(y + repeat + 2) * stride] == line:
            repeat += 1
        out.append(repeat)
        out += _encode_line(line)
        y += repeat + 1
    return bytes(out)


def decode_page(data: bytes, width: int, height: int) -> bytes:
    """The inverse of encode_page (for tests)."""
    out = bytearray()
    pos = 0
    while len(out) < width * height * 3:
        repeat = data[pos]
        pos += 1
        line = bytearray()
        while len(line) < width * 3:
            control = data[pos]
            pos += 1
            if control == 128:
                line += b"\xff" * (width * 3 - len(line))
            elif control < 128:
                line += data[pos:pos + 3] * (control + 1)
                pos += 3
            else:
                count = 257 - control
                line += data[pos:pos + 3 * count]
                pos += 3 * count
        out += bytes(line) * (repeat + 1)
    return bytes(out)


def pwg_raster(images: list, paper: dict) -> bytes:
    """PIL images (already the paper's pixel size) as one PWG raster stream."""
    out = bytearray(b"RaS2")
    for image in images:
        width, height = image.size
        out += pwg_header(width, height, paper, len(images))
        out += encode_page(image.convert("RGB").tobytes(), width, height)
    return bytes(out)


# --- the cards ---------------------------------------------------------------

def pdf_pages_mm(pdf: Path) -> tuple[int, tuple[float, float]]:
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True).stdout
    pages = int(next(line.split()[-1] for line in info.splitlines() if line.startswith("Pages:")))
    size = next(line for line in info.splitlines() if line.startswith("Page size:")).split()
    return pages, (float(size[2]) / PT_PER_MM, float(size[4]) / PT_PER_MM)


def render_cards(pdf: Path, cards: list[int], paper: dict) -> list:
    """Each card at the printer's pixels, turned to the paper (portrait)."""
    from PIL import Image

    width, height = pixels(paper)
    images = []
    with tempfile.TemporaryDirectory() as tmp:
        for card in cards:
            stem = Path(tmp) / f"card{card}"
            subprocess.run(["pdftoppm", "-r", str(DPI), "-f", str(card), "-l", str(card),
                            "-png", "-singlefile", str(pdf), str(stem)], check=True)
            image = Image.open(f"{stem}.png").convert("RGB")
            if image.width > image.height:
                image = image.rotate(90, expand=True)
            images.append(image.resize((width, height), Image.LANCZOS))
    return images


def preview(pdf: Path, pages: int, out: Path) -> Path:
    """All cards side by side in one picture, numbered, to look at first."""
    from PIL import Image, ImageDraw

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "60", "-png", str(pdf), str(Path(tmp) / "p")], check=True)
        shots = [Image.open(path).convert("RGB") for path in sorted(Path(tmp).glob("p*.png"))]
    gap = 16
    sheet = Image.new("RGB", (sum(s.width for s in shots) + gap * (len(shots) + 1),
                              max(s.height for s in shots) + gap * 2 + 20), "#d8d8d8")
    draw = ImageDraw.Draw(sheet)
    x = gap
    for number, shot in enumerate(shots, start=1):
        sheet.paste(shot, (x, gap))
        draw.text((x + shot.width // 2 - 4, gap + shot.height + 4), str(number), fill="black")
        x += shot.width + gap
    sheet.save(out)
    return out


def newest_postcards() -> Path | None:
    log_dir = Path(os.getenv("POOH_LOG_DIR", str(PROJECT_ROOT / "logs")))
    found = sorted((log_dir / "postcards").glob("*.pdf"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


# --- IPP ---------------------------------------------------------------------

PRINT_TEST = """{{
  NAME "Print postcards"
  OPERATION Print-Job
  GROUP operation-attributes-tag
  ATTR charset attributes-charset utf-8
  ATTR naturalLanguage attributes-natural-language ja
  ATTR uri printer-uri $uri
  ATTR name requesting-user-name $user
  ATTR name job-name "{job}"
  ATTR mimeMediaType document-format image/pwg-raster
  GROUP job-attributes-tag
  ATTR collection media-col {{
    MEMBER collection media-size {{
      MEMBER integer x-dimension {x}
      MEMBER integer y-dimension {y}
    }}
    MEMBER integer media-bottom-margin 0
    MEMBER integer media-left-margin 0
    MEMBER integer media-right-margin 0
    MEMBER integer media-top-margin 0
    MEMBER keyword media-type photographic
    MEMBER keyword media-source photo
  }}
  FILE $filename
  STATUS successful-ok
}}
"""


def loaded_paper(uri: str) -> str | None:
    """The printer's media-ready keyword (which paper is in it), if it answers."""
    with tempfile.NamedTemporaryFile("w", suffix=".test", delete=False) as test:
        test.write('{ OPERATION Get-Printer-Attributes\n GROUP operation-attributes-tag\n'
                   ' ATTR charset attributes-charset utf-8\n'
                   ' ATTR naturalLanguage attributes-natural-language en\n'
                   ' ATTR uri printer-uri $uri\n ATTR keyword requested-attributes media-ready\n'
                   ' STATUS successful-ok }\n')
    try:
        result = subprocess.run(["ipptool", "-tv", uri, test.name], capture_output=True,
                                text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        os.unlink(test.name)
    for line in result.stdout.splitlines():
        if line.strip().startswith("media-ready (") and "=" in line:
            return line.split("=", 1)[1].strip()
    return None


def send(raster: bytes, uri: str, paper: dict, job: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "cards.pwg"
        data.write_bytes(raster)
        test = Path(tmp) / "print.test"
        test.write_text(PRINT_TEST.format(job=job, x=round(paper["width_mm"] * 100),
                                          y=round(paper["height_mm"] * 100)), encoding="utf-8")
        return subprocess.run(["ipptool", "-tv", "-f", str(data), uri, str(test)],
                              capture_output=True, text=True, timeout=120)


def parse_cards(text: str, pages: int) -> list[int]:
    """'1', '1,3', '1-3' or 'all' (empty) -> card numbers."""
    text = text.strip().lower()
    if text in ("", "all", "a"):
        return list(range(1, pages + 1))
    cards: list[int] = []
    for part in text.replace(" ", "").split(","):
        first, _, last = part.partition("-")
        cards += range(int(first), int(last or first) + 1)
    if not cards or any(not 1 <= card <= pages for card in cards):
        raise ValueError(f"1〜{pages} の番号で指定してください。")
    return cards


def main() -> int:
    parser = argparse.ArgumentParser(description="ポストカードを SELPHY CP1500 にふちなしで印刷する")
    parser.add_argument("pdf", nargs="?", type=Path, help="ポストカードのPDF（既定: いちばん新しいもの）")
    parser.add_argument("--cards", help="印刷するカード（1 / 1,3 / 1-3 / all）。省略すると、見本を見てから聞く")
    parser.add_argument("--printer", help="プリンターのURI（既定: .tools/printer.json、なければネットワークで探す）")
    parser.add_argument("--preview-only", action="store_true", help="見本を出すだけで印刷しない")
    parser.add_argument("--yes", action="store_true", help="確認せずに印刷する")
    args = parser.parse_args()

    printer = load_printer()
    for tool in ("pdftoppm", "pdfinfo", "ipptool"):
        if shutil.which(tool) is None:
            print(f"{tool} が見つかりません（docs/printing.md の準備を見てください）。", file=sys.stderr)
            return 1
    pdf = args.pdf or newest_postcards()
    if pdf is None or not pdf.is_file():
        print("ポストカードのPDFが見つかりません。", file=sys.stderr)
        return 1
    pages, page_mm = pdf_pages_mm(pdf)
    name = paper_for(page_mm, printer["papers"])
    if name is None:
        print(f"このPDF（{page_mm[0]:.0f}×{page_mm[1]:.0f} mm）はプリンターの用紙に合いません。"
              "postcard.py の --paper で作り直してください。", file=sys.stderr)
        return 1
    paper = printer["papers"][name]
    print(f"ポストカード: {pdf}（{pages}枚、{paper['label']}）")

    shot = preview(pdf, pages, Path(tempfile.gettempdir()) / f"{pdf.stem}_preview.png")
    print(f"見本: {shot}")
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        subprocess.Popen(["xdg-open", str(shot)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if args.preview_only:
        return 0

    uri = printer_uri(printer, args.printer)
    if uri is None:
        print(f"{printer['name']} がネットワークに見つかりません。電源とネットワークを確かめるか、"
              "--printer ipp://<アドレス>:631/ipp/print で指定してください。", file=sys.stderr)
        return 1
    loaded = loaded_paper(uri)
    if loaded is None:
        print(f"注意: プリンター（{uri}）が答えません。電源・ネットワークを確かめてください。")
    elif loaded != paper["media_ready"]:
        print(f"注意: プリンターに入っている用紙は {loaded} です（このPDFは {paper['label']}）。")
    try:
        cards = parse_cards(args.cards if args.cards is not None else
                            input(f"印刷するカード（1〜{pages}、例 1 / 1,3 / 1-3、Enter で全部）: "), pages)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not args.yes and input(f"{', '.join(map(str, cards))} 枚目を {paper['label']} に印刷しますか？ [y/N]: "
                              ).strip().lower() not in ("y", "yes"):
        print("印刷しませんでした。")
        return 0
    raster = pwg_raster(render_cards(pdf, cards, paper), paper)
    result = send(raster, uri, paper, pdf.stem)
    if result.returncode != 0:
        print("印刷できませんでした:\n" + (result.stdout + result.stderr)[-1500:], file=sys.stderr)
        return 1
    print(f"{len(cards)} 枚を送りました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
