"""Tests for turning Steam's patch-note BBCode into lines (patches.py), on made-up notes in the real format."""

import unittest

from patches import lines_about, notes_lines

NOTES = ("[p][b]\\[ General ][/b][/p][p][/p][p]- Guardian bounty increased by 10%[/p]"
         "[p][img]{STEAM_CLAN_IMAGE}/x.png[/img][/p][h2]Heroes[/h2]"
         "[list][*]Haze: Bullet Dance damage reduced from 20 to 18[*]Kelvin: Frost Grenade cooldown reduced[/list]"
         "[p]Thanks for playing &amp; see you [url=https://example.com]soon[/url]![/p]")


class NotesTests(unittest.TestCase):
    def test_bbcode_becomes_headings_items_and_text(self):
        self.assertEqual(notes_lines(NOTES), [
            ("heading", "General"),
            ("item", "Guardian bounty increased by 10%"),
            ("heading", "Heroes"),
            ("item", "Haze: Bullet Dance damage reduced from 20 to 18"),
            ("item", "Kelvin: Frost Grenade cooldown reduced"),
            ("text", "Thanks for playing & see you soon!"),
        ])

    def test_lines_about_a_hero_keep_their_heading(self):
        self.assertEqual(lines_about(notes_lines(NOTES), "kelvin"),
                         [("heading", "Heroes"), ("item", "Kelvin: Frost Grenade cooldown reduced")])
        self.assertEqual(lines_about(notes_lines(NOTES), "Haze Bot"), [])  # whole words only
        self.assertEqual(len(lines_about(notes_lines(NOTES), None)), 6)


if __name__ == "__main__":
    unittest.main()
