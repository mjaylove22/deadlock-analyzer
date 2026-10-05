"""Tests for spotting the end-of-match screen and reading its match ID."""

import glob
import os
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

import end_screen
import layout

SCREENSHOTS = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "screenshots", "screenshot_*.png")))


def screen_with_headings(dx=0, dy=0, size=(1920, 1080), scale=1.0):
    """A dark screen with the reference headings pasted where the end screen has them (moved a little)."""
    screen = Image.new("RGB", size, (8, 8, 8))
    headings = Image.open(end_screen.REFERENCE_PATH).convert("RGB")
    if scale != 1:
        headings = headings.resize((round(headings.width * scale), round(headings.height * scale)), Image.LANCZOS)
    left, top = end_screen.HEADER_BOX[:2]
    screen.paste(headings, (round(left * scale) + dx, round(top * scale) + dy))
    return screen


class DetectorTests(unittest.TestCase):
    def test_found_at_its_place_and_a_few_pixels_off(self):
        for dx, dy in ((0, 0), (2, -1), (-3, 3)):
            with self.subTest(offset=(dx, dy)):
                self.assertTrue(end_screen.is_end_screen(screen_with_headings(dx, dy).crop, layout.REFERENCE_LAYOUT))

    def test_found_on_a_bigger_screen(self):
        scaled = layout.Layout(1440 / 1080)  # 2560x1440, the UI scaled with the height
        image = screen_with_headings(size=(2560, 1440), scale=scaled.scale)
        self.assertTrue(end_screen.is_end_screen(image.crop, scaled))

    def test_not_found_too_far_away_or_on_plain_screens(self):
        self.assertFalse(end_screen.is_end_screen(screen_with_headings(0, 12).crop, layout.REFERENCE_LAYOUT))
        for color in ("black", "white", (60, 60, 60)):
            with self.subTest(color=color):
                self.assertFalse(end_screen.is_end_screen(Image.new("RGB", (1920, 1080), color).crop,
                                                          layout.REFERENCE_LAYOUT))

    def test_other_text_in_the_same_place_is_not_the_end_screen(self):
        screen = Image.new("RGB", (1920, 1080), (8, 8, 8))
        ImageDraw.Draw(screen).text((565, 110), "HEALING   OBJ DMG   ITEMS   SOULS", fill=(110, 110, 110))
        self.assertFalse(end_screen.is_end_screen(screen.crop, layout.REFERENCE_LAYOUT))

    @unittest.skipUnless(SCREENSHOTS, "no screenshots on this machine")
    def test_no_scoreboard_screenshot_is_taken_for_the_end_screen(self):
        for path in SCREENSHOTS:
            with self.subTest(screenshot=os.path.basename(path)):
                image = Image.open(path)
                if image.size == (1920, 1080):
                    self.assertFalse(end_screen.is_end_screen(image.crop, layout.REFERENCE_LAYOUT))


class EndMatchIdTests(unittest.TestCase):
    """The top-right text is dim, and one setting read a wrong digit at the right length, so two
    settings must agree."""

    def read(self, *ocr_texts):
        import scoreboard_ocr
        with patch.object(scoreboard_ocr.pytesseract, "image_to_string", side_effect=list(ocr_texts)) as ocr:
            found = end_screen.read_end_match_id(Image.new("RGB", (1920, 1080)), layout.REFERENCE_LAYOUT)
        return found, ocr.call_count

    def test_two_settings_must_agree(self):
        self.assertEqual(self.read("MATCH 111203456", "MATCH 111203456"), (111203456, 2))

    def test_a_disagreeing_reading_is_outvoted(self):
        self.assertEqual(self.read("MATCH 111208456", "MATCH 111203456", "MATCH 111203456"), (111203456, 3))

    def test_none_without_agreement(self):
        self.assertEqual(self.read("MATCH 111208456", "MATCH 111203456", "", "MATCH 11120345"), (None, 4))


if __name__ == "__main__":
    unittest.main()
