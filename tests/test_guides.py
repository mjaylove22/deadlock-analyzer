"""Tests for the hero guide: plain game text, what a kit does, and the playstyle summary."""

import unittest
from unittest.mock import patch

import deadlock_api
import guides


def ability(text, cooldown=30.0):
    return {"name": "An ability", "image": None, "text": text, "cooldown": cooldown, "charges": None}


GUIDES = {
    "Booker": {"type": "mystic", "tags": ["Helpful"], "complexity": 1, "gun": "Projectile", "health": 680, "speed": 6.9,
               "abilities": [ability("Grant an ally a barrier. While it holds, they gain bonus weapon damage."),
                             ability("Target an area, applying slow, then dealing spirit damage.")]},
    "Tank": {"type": "brawler", "tags": [], "complexity": 2, "gun": "Shotgun", "health": 800, "speed": 6.9, "abilities": []},
    "Mid": {"type": "marksman", "tags": [], "complexity": 3, "gun": "Pistol", "health": 760, "speed": 6.9, "abilities": []},
}


class PlainTextTests(unittest.TestCase):
    def test_markup_icons_and_entities_are_removed_without_stray_spaces(self):
        markup = 'Deals <svg width="1"><path d="M0"/></svg><span class="x">spirit damage</span> &amp; applies <b>slow</b> .'
        self.assertEqual(deadlock_api.plain_text(markup), "Deals spirit damage & applies slow.")


class KitTests(unittest.TestCase):
    def test_what_the_abilities_do(self):
        self.assertEqual(guides.kit(GUIDES["Booker"]["abilities"]), ["weapon buffs", "spirit damage", "barriers", "slows"])

    def test_a_buff_for_allies_isnt_also_counted_as_damage(self):
        self.assertNotIn("weapon damage", guides.kit([ability("Allies gain bonus weapon damage.")]))


class GuideTests(unittest.TestCase):
    def guide(self, hero, bought=None):
        with patch.object(deadlock_api, "fetch_hero_guides", return_value=GUIDES):
            return guides.hero_guide(hero, bought)

    def test_summary_is_built_from_the_facts(self):
        bought = [{"slot": "spirit", "matches": 70}, {"slot": "weapon", "matches": 30}]
        summary = self.guide("Booker", bought)["summary"]
        self.assertTrue(summary.startswith("An easy Mystic with a projectile gun. Low health."), summary)
        self.assertIn("Players mostly build Booker with spirit items (70%", summary)

    def test_weapons_that_are_already_nouns_dont_get_gun_added(self):
        self.assertIn("with a shotgun.", self.guide("Tank")["summary"])

    def test_unknown_hero_has_no_guide(self):
        self.assertIsNone(self.guide("Nobody"))

    def test_build_split_is_weighted_by_purchases(self):
        split = guides.build_split([{"slot": "spirit", "matches": 300}, {"slot": "weapon", "matches": 100}])
        self.assertEqual(split, {"spirit": 0.75, "weapon": 0.25})


if __name__ == "__main__":
    unittest.main()
