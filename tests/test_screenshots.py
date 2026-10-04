"""Regression tests: real OCR on real screenshots, compared with hand-checked answers.

For every screenshots/<name>.png that has a screenshots/<name>.expected.json next to it,
the full OCR pipeline must produce exactly the expected records. Screenshots contain other
players' names, so they stay in the gitignored screenshots/ folder; these tests are skipped
on machines that don't have any.

To add a case: save a screenshot, check `python scoreboard_ocr.py <file>` by eye, and write
<name>.expected.json as a list of {"player", "hero", "team"} records.
"""

import glob
import json
import os
import unittest

from player_lookup import looks_like_misread
from scoreboard_ocr import FALLBACK_HERO_NAMES, read_scoreboard

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")
CASES = sorted(glob.glob(os.path.join(SCREENSHOT_DIR, "*.expected.json")))


@unittest.skipUnless(CASES, "no screenshots/*.expected.json files on this machine")
class ScreenshotRegressionTests(unittest.TestCase):
    def test_each_screenshot_matches_expected_records(self):
        for expected_path in CASES:
            screenshot_path = expected_path.replace(".expected.json", ".png")
            with self.subTest(screenshot=os.path.basename(screenshot_path)):
                with open(expected_path, encoding="utf-8") as f:
                    expected = json.load(f)
                actual = read_scoreboard(screenshot_path, FALLBACK_HERO_NAMES)
                # Heroes and teams must be exact. A name may have one misread character, because
                # the lookup corrects those (e.g. "Or. Night Owl" -> "Dr. Night Owl").
                self.assertEqual([(r["hero"], r["team"]) for r in actual], [(r["hero"], r["team"]) for r in expected])
                for got, want in zip(actual, expected):
                    self.assertTrue(got["player"] == want["player"] or looks_like_misread(got["player"], want["player"]),
                                    f"OCR read {got['player']!r}, screen says {want['player']!r}")


if __name__ == "__main__":
    unittest.main()
