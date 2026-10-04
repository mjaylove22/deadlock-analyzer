"""Other screen sizes, simulated from a real 1080p screenshot (skipped without local screenshots).

These check the app's own handling (finding the scoreboard, scaling it back, reading it), not
how Deadlock itself lays out its UI at each size: that needs real screenshots at those sizes.
Simulated small/huge screens are blurrier than real ones (the image is resized twice), so the
bar is set lower there.
"""

import json
import os
import tempfile
import unittest

from PIL import Image

from player_lookup import looks_like_misread, same_name
from scoreboard_ocr import FALLBACK_HERO_NAMES, find_layout, read_scoreboard

SHOT = os.path.join(os.path.dirname(__file__), "..", "screenshots", "screenshot_20261003_223435.png")
EXPECTED = SHOT.replace(".png", ".expected.json")


def padded(image, width, height, x, y):
    canvas = Image.new("RGB", (width, height), (8, 8, 10))
    canvas.paste(image, (x, y))
    return canvas


@unittest.skipUnless(os.path.exists(SHOT) and os.path.exists(EXPECTED), "test screenshot not on this machine")
class SimulatedResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Image.open(SHOT).convert("RGB")
        with open(EXPECTED, encoding="utf-8") as f:
            cls.expected = json.load(f)
        cls.folder = tempfile.mkdtemp()

    def read(self, image):
        path = os.path.join(self.folder, "case.png")
        image.save(path)
        return read_scoreboard(path, FALLBACK_HERO_NAMES)

    def correct_rows(self, records):
        """Rows whose hero and team are right and whose name the lookup would match."""
        wanted = {(r["hero"], r["team"]): r["player"] for r in self.expected}
        return sum(1 for r in records if (r["hero"], r["team"]) in wanted and
                   (same_name(r["player"], wanted[(r["hero"], r["team"])])
                    or looks_like_misread(r["player"], wanted[(r["hero"], r["team"])])))

    def check(self, image, expected_scale, expected_offset, at_least):
        layout = find_layout(image)
        self.assertAlmostEqual(layout.scale, expected_scale, places=3)
        self.assertEqual((round(layout.offset_x), round(layout.offset_y)), expected_offset)
        self.assertGreaterEqual(self.correct_rows(self.read(image)), at_least)

    def test_1440p(self):
        self.check(self.base.resize((2560, 1440), Image.LANCZOS), 2560 / 1920, (0, 0), 12)

    def test_ultrawide_with_the_ui_on_the_right(self):
        image = padded(self.base.resize((2560, 1440), Image.LANCZOS), 3440, 1440, 880, 0)
        self.check(image, 1440 / 1080, (880, 0), 12)

    def test_ultrawide_with_the_ui_centred(self):
        self.check(padded(self.base, 2560, 1080, 320, 0), 1.0, (320, 0), 12)

    def test_16_10_letterboxed(self):
        self.check(padded(self.base, 1920, 1200, 0, 60), 1.0, (0, 60), 12)

    def test_smaller_and_bigger_screens(self):
        self.check(self.base.resize((1600, 900), Image.LANCZOS), 900 / 1080, (0, 0), 11)
        self.check(self.base.resize((1280, 720), Image.LANCZOS), 720 / 1080, (0, 0), 10)
        self.check(self.base.resize((3840, 2160), Image.LANCZOS), 2.0, (0, 0), 10)


if __name__ == "__main__":
    unittest.main()
