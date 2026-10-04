"""Tests for player lookup logic. The Deadlock API is mocked, so these run offline.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import unittest
from unittest.mock import patch

import player_lookup
from player_lookup import lookup_player, pick_account

PARADOX = 10
GRAVES = 76
HERO_IDS = {"Paradox": PARADOX, "Graves": GRAVES, "Haze": 13}
HERO_NAMES = {v: k for k, v in HERO_IDS.items()}


def profile(account_id, name):
    return {"account_id": account_id, "personaname": name, "profileurl": f"https://steam/{account_id}"}


def stat(account_id, hero_id, matches, wins=0):
    return {"account_id": account_id, "hero_id": hero_id, "matches_played": matches, "wins": wins}


class PickAccountTests(unittest.TestCase):
    def test_single_candidate_is_used(self):
        account, note = pick_account([profile(1, "A")], {}, PARADOX)
        self.assertEqual(account["account_id"], 1)
        self.assertEqual(note, "unique name")

    def test_prefers_account_that_plays_current_hero(self):
        candidates = [profile(1, "Grey Mirage"), profile(2, "grey mirage")]
        stats = {1: [stat(1, GRAVES, 50)], 2: [stat(2, PARADOX, 8)]}
        account, note = pick_account(candidates, stats, PARADOX)
        self.assertEqual(account["account_id"], 2)
        self.assertIn("8 matches", note)

    def test_falls_back_to_api_order_and_says_it_is_a_guess(self):
        candidates = [profile(1, "X"), profile(2, "X")]
        account, note = pick_account(candidates, {}, PARADOX)
        self.assertEqual(account["account_id"], 1)
        self.assertIn("guess", note)


class LookupPlayerTests(unittest.TestCase):
    def test_bot_named_after_its_hero_is_skipped_without_api_calls(self):
        with patch.object(player_lookup.deadlock_api, "search_steam_profiles") as search:
            result = lookup_player({"player": "Haze", "hero": "Haze", "team": "enemy"}, HERO_IDS, HERO_NAMES)
        self.assertEqual(result["status"], "skipped")
        search.assert_not_called()

    def test_fuzzy_matches_are_not_trusted(self):
        with patch.object(player_lookup.deadlock_api, "search_steam_profiles", return_value=[profile(1, "Nine Viscious")]):
            result = lookup_player({"player": "Grey Mirage", "hero": "Paradox", "team": "friendly"}, HERO_IDS, HERO_NAMES)
        self.assertEqual(result["status"], "not found")
        self.assertIn("Nine Viscious", result["note"])

    def test_found_player_gets_top_heroes_sorted_by_matches(self):
        stats = [stat(2, PARADOX, 8, wins=4), stat(2, GRAVES, 20, wins=14)]
        with patch.object(player_lookup.deadlock_api, "search_steam_profiles", return_value=[profile(2, "Grey Mirage")]), \
             patch.object(player_lookup.deadlock_api, "get_hero_stats", return_value=stats):
            result = lookup_player({"player": "Grey Mirage", "hero": "Paradox", "team": "friendly"}, HERO_IDS, HERO_NAMES)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["account_id"], 2)
        self.assertEqual([h["hero"] for h in result["top_heroes"]], ["Graves", "Paradox"])
        self.assertAlmostEqual(result["top_heroes"][0]["win_rate"], 0.7)

    def test_network_error_is_reported_not_raised(self):
        with patch.object(player_lookup.deadlock_api, "search_steam_profiles", side_effect=OSError("timed out")):
            result = lookup_player({"player": "Someone", "hero": "Paradox", "team": "enemy"}, HERO_IDS, HERO_NAMES)
        self.assertEqual(result["status"], "error")
        self.assertIn("timed out", result["note"])


if __name__ == "__main__":
    unittest.main()
