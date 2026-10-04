"""Tests for lobby lookup. The Deadlock API is mocked, so these run offline.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import unittest
from unittest.mock import patch

import player_lookup
from player_lookup import lookup_lobby

PARADOX = 10
GRAVES = 76
HERO_IDS = {"Paradox": PARADOX, "Graves": GRAVES, "Haze": 13}
HERO_NAMES = {v: k for k, v in HERO_IDS.items()}


def profile(account_id, name, friends=()):
    return {"account_id": account_id, "personaname": name, "profileurl": f"https://steam/{account_id}",
            "friends": [{"account_id": f} for f in friends]}


def stat(account_id, hero_id, matches, wins=0):
    """Shaped like a real hero-stats entry (only the fields this project reads)."""
    return {"account_id": account_id, "hero_id": hero_id, "matches_played": matches, "wins": wins,
            "kills": matches * 5, "deaths": matches * 4, "assists": matches * 8, "damage_per_min": 900.0}


RANK_TIERS = {0: {"name": "Obscurus", "color": "#333333"}, 7: {"name": "Emissary", "color": "#B47FEB"}}


def rank(account_id, tier, subrank):
    return {"account_id": account_id, "rank": tier, "subrank": subrank}


def record(player, hero, team="friendly"):
    return {"player": player, "hero": hero, "team": team}


def fake_search(profiles_by_name):
    return lambda name: profiles_by_name.get(name, [])


class LookupLobbyTests(unittest.TestCase):
    def setUp(self):
        # Safety net: any API call a test forgot to mock fails loudly instead of using the network
        guard = patch.object(player_lookup.deadlock_api, "get_json",
                             side_effect=AssertionError("unit test tried to call the real API"))
        guard.start()
        self.addCleanup(guard.stop)

    def run_lookup(self, records, profiles_by_name, stats=()):
        api = player_lookup.deadlock_api
        with patch.object(api, "search_steam_profiles", side_effect=fake_search(profiles_by_name)), \
             patch.object(api, "get_hero_stats", return_value=list(stats)) as get_stats, \
             patch.object(api, "fetch_rank_tiers", return_value=RANK_TIERS), \
             patch.object(api, "get_player_ranks", side_effect=lambda ids: [rank(a, 7, 2) for a in ids]):
            results, parties = lookup_lobby(records, HERO_IDS, HERO_NAMES)
        return results, parties, get_stats

    def test_bot_named_after_its_hero_is_skipped_without_api_calls(self):
        with patch.object(player_lookup.deadlock_api, "search_steam_profiles") as search:
            results, _ = lookup_lobby([record("Haze", "Haze")], HERO_IDS, HERO_NAMES)
        self.assertEqual(results[0]["status"], "skipped")
        search.assert_not_called()

    def test_fuzzy_matches_are_not_trusted(self):
        results, _, _ = self.run_lookup([record("Grey Mirage", "Paradox")],
                                        {"Grey Mirage": [profile(1, "Nine Viscious")]})
        self.assertEqual(results[0]["status"], "not found")
        self.assertIn("Nine Viscious", results[0]["note"])

    def test_found_player_gets_top_heroes_sorted_by_matches(self):
        stats = [stat(2, PARADOX, 8, wins=4), stat(2, GRAVES, 20, wins=14)]
        results, _, _ = self.run_lookup([record("Grey Mirage", "Paradox")],
                                        {"Grey Mirage": [profile(2, "Grey Mirage")]}, stats)
        self.assertEqual(results[0]["status"], "found")
        self.assertEqual([h["hero"] for h in results[0]["top_heroes"]], ["Graves", "Paradox"])
        self.assertAlmostEqual(results[0]["top_heroes"][0]["win_rate"], 0.7)

    def test_whole_lobby_uses_one_stats_request(self):
        profiles = {"A": [profile(1, "A")], "B": [profile(2, "B"), profile(3, "B")]}
        _, _, get_stats = self.run_lookup([record("A", "Paradox"), record("B", "Graves")], profiles)
        get_stats.assert_called_once()
        self.assertEqual(sorted(get_stats.call_args.args[0]), [1, 2, 3])

    def test_friend_link_picks_account_and_party_is_reported(self):
        profiles = {
            "PlayerA": [profile(1, "PlayerA")],
            "PlayerB": [profile(20, "PlayerB"), profile(21, "PlayerB", friends={1})],
        }
        stats = [stat(20, PARADOX, 25)]  # the stranger has more games on the current hero
        results, parties, _ = self.run_lookup(
            [record("PlayerA", "Graves", "enemy"), record("PlayerB", "Paradox", "enemy")], profiles, stats)
        self.assertEqual(results[1]["account_id"], 21)
        self.assertEqual(results[1]["note"], "friends with PlayerA in this lobby")
        self.assertEqual(parties, [[0, 1]])

    def test_found_player_gets_rank_badges_and_confidence(self):
        stats = [stat(2, PARADOX, 25, wins=20), stat(2, GRAVES, 5)]
        results, _, _ = self.run_lookup([record("Solo", "Paradox")], {"Solo": [profile(2, "Solo")]}, stats)
        r = results[0]
        self.assertEqual(r["rank"], {"name": "Emissary 2", "color": "#B47FEB"})
        self.assertEqual(r["hero_stats"]["games"], 25)
        self.assertIn(("HIGH WR", "good"), r["badges"])
        self.assertTrue(r["confident"])

    def test_close_hero_history_call_is_marked_unsure(self):
        profiles = {"Twin": [profile(1, "Twin"), profile(2, "Twin")]}
        stats = [stat(1, PARADOX, 8), stat(2, PARADOX, 5)]  # the real "8 vs 5" case
        results, _, _ = self.run_lookup([record("Twin", "Paradox")], profiles, stats)
        self.assertFalse(results[0]["confident"])

    def test_network_error_is_reported_not_raised(self):
        with patch.object(player_lookup.deadlock_api, "search_steam_profiles", side_effect=OSError("timed out")):
            results, _ = lookup_lobby([record("Someone", "Paradox")], HERO_IDS, HERO_NAMES)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("timed out", results[0]["note"])


if __name__ == "__main__":
    unittest.main()
