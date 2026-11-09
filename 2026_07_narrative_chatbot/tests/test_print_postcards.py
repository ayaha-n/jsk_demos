"""scripts/print_postcards.py: the PWG raster it writes and how it picks
cards and paper. Nothing is sent to a printer."""

import random
import struct
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from PIL import Image

import print_postcards as pp

PRINTER = pp.load_printer()
L = PRINTER["papers"]["l"]


class PwgRasterTest(unittest.TestCase):
    def test_pages_survive_encoding(self):
        random.seed(1)
        width, height = 37, 9
        rows = [bytes(random.choice([0, 255]) for _ in range(width * 3)) for _ in range(3)]
        flat = b"\xff\xff\xff" * width
        runs = (b"\x10\x20\x30" * 200)[:width * 3]
        # random, repeated, white and run lines, the last ones repeated many times
        lines = rows + [rows[0], flat, flat, runs] + [rows[1]] * 2
        rgb = b"".join(lines)
        self.assertEqual(pp.decode_page(pp.encode_page(rgb, width, len(lines)), width, len(lines)), rgb)
        tall = flat * 600  # more than 256 identical lines
        self.assertEqual(pp.decode_page(pp.encode_page(tall, width, 600), width, 600), tall)

    def test_the_header_says_what_the_printer_takes(self):
        width, height = pp.pixels(L)
        self.assertEqual((width, height), (1051, 1406))  # 89 x 119 mm at 300 dpi
        raster = pp.pwg_raster([Image.new("RGB", (width, height), "white")], L)
        self.assertEqual(raster[:4], b"RaS2")
        header = raster[4:4 + 1796]
        field = lambda offset: struct.unpack(">I", header[offset:offset + 4])[0]
        self.assertEqual(header[:9], b"PwgRaster")
        self.assertEqual((field(276), field(280)), (300, 300))
        self.assertEqual((field(372), field(376)), (1051, 1406))
        self.assertEqual((field(384), field(388), field(392), field(400)), (8, 24, 1051 * 3, 19))  # rgb_8, sRGB


class ChoosingTest(unittest.TestCase):
    def test_paper_comes_from_the_pdf_page(self):
        self.assertEqual(pp.paper_for((119.0, 89.0), PRINTER["papers"]), "l")
        self.assertEqual(pp.paper_for((89.0, 119.0), PRINTER["papers"]), "l")
        self.assertEqual(pp.paper_for((148.0, 100.0), PRINTER["papers"]), "hagaki")
        self.assertIsNone(pp.paper_for((127.0, 89.0), PRINTER["papers"]))  # the photo-lab L

    def test_the_printer_address_is_given_remembered_or_found(self):
        import json
        import tempfile

        self.assertNotIn("uri", PRINTER)  # not in the public repository
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "printer.json"
            asked = []
            find = lambda name: asked.append(name) or "ipp://CP1500abc.local:631/ipp/print"
            self.assertEqual(pp.printer_uri(PRINTER, "ipp://given", local, find), "ipp://given")
            self.assertEqual(asked, [])
            # Found once on the network, then remembered on this PC.
            self.assertEqual(pp.printer_uri(PRINTER, None, local, find), "ipp://CP1500abc.local:631/ipp/print")
            self.assertEqual(asked, ["CP1500"])
            self.assertEqual(json.loads(local.read_text())["uri"], "ipp://CP1500abc.local:631/ipp/print")
            self.assertEqual(pp.printer_uri(PRINTER, None, local, lambda name: None),
                             "ipp://CP1500abc.local:631/ipp/print")
            local.unlink()
            self.assertIsNone(pp.printer_uri(PRINTER, None, local, lambda name: None))

    def test_cards_to_print(self):
        self.assertEqual(pp.parse_cards("", 3), [1, 2, 3])
        self.assertEqual(pp.parse_cards("1,3", 3), [1, 3])
        self.assertEqual(pp.parse_cards("2-3", 3), [2, 3])
        with self.assertRaises(ValueError):
            pp.parse_cards("4", 3)


if __name__ == "__main__":
    unittest.main()
