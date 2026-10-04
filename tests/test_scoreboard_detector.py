"""Tests for scoreboard detection on real screenshots and on things that must not match."""

import glob
import os
import unittest

from PIL import Image, ImageDraw

from scoreboard_detector import PLAYERS_TAB_BOX, is_scoreboard_open, tab_correlation, tab_difference

# Only screenshots confirmed to show the scoreboard (they have hand-checked .expected.json answers);
# any other capture in the folder could be of anything, e.g. a hotkey press outside the game
SCREENSHOTS = [path.replace(".expected.json", ".png") for path in sorted(glob.glob(
    os.path.join(os.path.dirname(__file__), "..", "screenshots", "screenshot_*.expected.json")))]


class DetectorTests(unittest.TestCase):
    @unittest.skipUnless(SCREENSHOTS, "no screenshots on this machine")
    def test_every_real_scoreboard_screenshot_is_detected(self):
        for path in SCREENSHOTS:
            with self.subTest(screenshot=os.path.basename(path)):
                self.assertTrue(is_scoreboard_open(Image.open(path).crop(PLAYERS_TAB_BOX)))

    def test_plain_and_noisy_areas_are_not_detected(self):
        width = PLAYERS_TAB_BOX[2] - PLAYERS_TAB_BOX[0]
        height = PLAYERS_TAB_BOX[3] - PLAYERS_TAB_BOX[1]
        for color in ("black", "white", "#6b6359", "#1f1f1f"):  # including the tab's own average colour
            with self.subTest(color=color):
                self.assertFalse(is_scoreboard_open(Image.new("RGB", (width, height), color)))

    def test_same_pill_with_different_text_is_not_detected(self):
        # A pill of the right colour saying something else, e.g. another menu's button
        width = PLAYERS_TAB_BOX[2] - PLAYERS_TAB_BOX[0]
        height = PLAYERS_TAB_BOX[3] - PLAYERS_TAB_BOX[1]
        fake = Image.new("RGB", (width, height), "#6b6359")
        ImageDraw.Draw(fake).text((20, 4), "SETTINGS  MENU", fill="#e8e0d0")
        self.assertFalse(is_scoreboard_open(fake))
        self.assertLess(tab_correlation(fake), 0.5)

    def test_flat_area_of_the_tabs_own_colour_fails_the_pattern_check(self):
        # Regression: this plain block passes the colour check alone (14.3 < 15)
        flat = Image.new("RGB", (PLAYERS_TAB_BOX[2] - PLAYERS_TAB_BOX[0], PLAYERS_TAB_BOX[3] - PLAYERS_TAB_BOX[1]), "#6b6359")
        self.assertLess(tab_difference(flat), 15)
        self.assertFalse(is_scoreboard_open(flat))


if __name__ == "__main__":
    unittest.main()
