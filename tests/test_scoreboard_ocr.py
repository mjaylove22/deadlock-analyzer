"""Tests for the scoreboard parser, using OCR text actually produced from real screenshots.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import unittest

from scoreboard_ocr import FALLBACK_HERO_NAMES, parse_player_rows

HEROES = FALLBACK_HERO_NAMES

# Friendly-team lines from screenshot_20260930_123906.png with the current crop
CLEAN_FRIENDLY = """Haze
Haze Level-1
Wraith
Wraith Level 1
Infernus
Infernus Level -1
Bebop
Bebop Level 1
Grey Mirage
Paradox Level
Vindicta
Vindicta Level 1""".split("\n")

# The same screenshot read with the original, wider crop (portraits and headers included)
NOISY_FULL_LIST = """FRIENDS
=, MY TEAM
-. Haze
Qe HazeLevel-1
; Wraith
org Wraith Level 1
~ Infernus
i) _ Infernus Level 4
a Bebop
BE Bebop Level -
1) Nine Viscou:
by Paradox Level
a Vindicta
Vindicta Level 1
ENEMY TEAM
8) Vyper
#¢@D Wperlevel-1
Cy Kelvin
Kelvin Level 1
4 Yamato
SB} Yamato Level1
& silver
Silver Level 1""".split("\n")


class ParsePlayerRowsTests(unittest.TestCase):
    def test_clean_rows_pair_in_screen_order(self):
        self.assertEqual(parse_player_rows(CLEAN_FRIENDLY, HEROES), [
            ("Haze", "Haze"),
            ("Wraith", "Wraith"),
            ("Infernus", "Infernus"),
            ("Bebop", "Bebop"),
            ("Grey Mirage", "Paradox"),
            ("Vindicta", "Vindicta"),
        ])

    def test_steam_name_that_is_not_a_hero(self):
        # The original bug: "Grey Mirage" contains no hero name (or a different one), but is still the player
        rows = parse_player_rows(["Grey Mirage", "Paradox Level -1"], HEROES)
        self.assertEqual(rows, [("Grey Mirage", "Paradox")])

    def test_noisy_text_keeps_rows_and_skips_unreadable_hero_line(self):
        rows = parse_player_rows(NOISY_FULL_LIST, HEROES)
        heroes_found = [hero for _, hero in rows]
        # "Wperlevel-1" has no recognisable hero, so Vyper is skipped rather than guessed
        self.assertNotIn("Vyper", heroes_found)
        self.assertEqual(len(rows), 9)
        self.assertIn(("1) Nine Viscou:", "Paradox"), rows)

    def test_header_is_never_a_player_name(self):
        self.assertEqual(parse_player_rows(["ENEMY TEAM", "Kelvin Level 1"], HEROES), [])

    def test_hero_line_without_name_above_is_skipped(self):
        self.assertEqual(parse_player_rows(["Kelvin Level 1"], HEROES), [])

    def test_hero_line_is_not_reused_as_next_players_name(self):
        rows = parse_player_rows(["Rem", "Rem Level 1", "Apollo Level 1"], HEROES)
        self.assertEqual(rows, [("Rem", "Rem")])

    def test_noise_only_lines_are_ignored(self):
        rows = parse_player_rows(["gs Apollo", "=", ",", "Apollo Level-1"], HEROES)
        self.assertEqual(rows, [("gs Apollo", "Apollo")])

    def test_duplicate_player_names_are_both_kept(self):
        rows = parse_player_rows(["Bob", "Haze Level 1", "Bob", "Rem Level 1"], HEROES)
        self.assertEqual(rows, [("Bob", "Haze"), ("Bob", "Rem")])

    def test_multi_word_hero_names(self):
        rows = parse_player_rows(["someone", "Lady Geist Level 1", "other", "Mo & Krill Level 1"], HEROES)
        self.assertEqual(rows, [("someone", "Lady Geist"), ("other", "Mo & Krill")])

    def test_longer_hero_name_wins_over_shorter_one_inside_it(self):
        rows = parse_player_rows(["someone", "Grey Talon Level 1"], ["Talon", "Grey Talon"])
        self.assertEqual(rows, [("someone", "Grey Talon")])


if __name__ == "__main__":
    unittest.main()
