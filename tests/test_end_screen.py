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


class ScoreboardParsingTests(unittest.TestCase):
    def test_the_end_screens_number_format(self):
        self.assertEqual([end_screen.parse_number(t) for t in ("409", "5.0k", "58k", "102k", "0")],
                         [409, 5000, 58000, 102000, 0])
        self.assertEqual([end_screen.parse_number(t) for t in (None, "", "k", "5.k", "1.2.3")], [None] * 5)

    def test_most_common_reading_and_ties_go_to_the_longest(self):
        self.assertEqual(end_screen.most_common(["61k", "6k", "61k"]), "61k")
        self.assertEqual(end_screen.most_common(["6k", "61k", None]), "61k")  # a dropped 1 is the usual misread
        self.assertIsNone(end_screen.most_common([None, ""]))

    def test_darkest_channel_keeps_white_and_grey_text_and_drops_coloured_rows(self):
        pixels = Image.new("RGB", (4, 1))
        for x, color in enumerate([(240, 240, 240), (111, 105, 91), (81, 50, 45), (60, 90, 160)]):
            pixels.putpixel((x, 0), color)  # white text, grey text, orange row, blue star
        values = list(end_screen.darkest_channel(pixels).getdata())
        self.assertTrue(all(v > end_screen.TEXT_CUTOFF for v in values[:2]))
        self.assertTrue(all(v < end_screen.TEXT_CUTOFF for v in values[2:]))


class LinkPlayersTests(unittest.TestCase):
    def screen(self):
        return {"players": [{"name": "Velvet Fox", "hero": "Haze", "account_id": None},
                            {"name": "Grey Mirage", "hero": "Rem", "account_id": None},
                            {"name": "Quiet Owl", "hero": "Lash", "account_id": None}]}

    def test_accounts_from_the_lobby_by_hero_and_you_by_account(self):
        from postgame import link_players
        screen = self.screen()
        lobby = [{"player": "Velvet Fox", "hero": "Haze", "account_id": 11},
                 {"player": "Grey Mirage", "hero": "Rem", "account_id": 22},
                 {"player": "Not Found", "hero": "Lash"}]  # this player wasn't identified
        link_players(screen, {"name": "Grey Mirage", "account_id": 22}, lobby)
        self.assertEqual([p["account_id"] for p in screen["players"]], [11, 22, None])
        self.assertIs(screen["me"], screen["players"][1])

    def test_without_a_lobby_you_are_found_by_name(self):
        from postgame import link_players
        screen = self.screen()
        link_players(screen, {"name": "grey mirage", "account_id": 22})
        self.assertEqual(screen["me"]["hero"], "Rem")
        self.assertEqual(screen["me"]["account_id"], 22)
        link_players(screen := self.screen(), None)
        self.assertIsNone(screen["me"])


if __name__ == "__main__":
    unittest.main()
