"""Tests for player-page and tier-list data (pure functions, no network)."""

import unittest

from profiles import describe_match, hero_rows, mode_breakdown, tier_rows, when

NAMES = {1: "Haze", 2: "Rem", 3: "Newbie"}


def match(game_mode=1, won=True, hero_id=1, start=1000):
    # A win is "the player's team is the winning team"; player_match_outcome is usually 0 (invalid)
    return {"match_id": start, "hero_id": hero_id, "game_mode": game_mode, "match_mode": 1, "player_team": 0,
            "match_result": 0 if won else 1, "player_match_outcome": 0, "player_kills": 5, "player_deaths": 2,
            "player_assists": 9, "net_worth": 30000, "match_duration_s": 1830, "start_time": start}


class ProfilesTests(unittest.TestCase):
    def test_win_comes_from_the_winning_team_not_the_outcome_field(self):
        self.assertTrue(describe_match(match(won=True), NAMES)["won"])
        self.assertFalse(describe_match(match(won=False), NAMES)["won"])

    def test_describe_match(self):
        m = describe_match(match(game_mode=4), NAMES)
        self.assertEqual((m["hero"], m["mode"], m["minutes"]), ("Haze", "Street Brawl", 30))

    def test_mode_breakdown_most_played_first(self):
        matches = [match(1, True), match(1, False), match(1, True), match(4, True)]
        rows = mode_breakdown(matches)
        self.assertEqual([(r["mode"], r["games"]) for r in rows], [("Normal", 3), ("Street Brawl", 1)])
        self.assertAlmostEqual(rows[0]["win_rate"], 2 / 3)

    def test_hero_rows_skip_unplayed_and_sort_by_games(self):
        entries = [{"hero_id": 1, "matches_played": 5, "wins": 4, "kills": 10, "deaths": 5, "assists": 10},
                   {"hero_id": 2, "matches_played": 9, "wins": 3, "kills": 9, "deaths": 0, "assists": 0},
                   {"hero_id": 3, "matches_played": 0, "wins": 0}]
        rows = hero_rows(entries, NAMES)
        self.assertEqual([r["hero"] for r in rows], ["Rem", "Haze"])
        self.assertAlmostEqual(rows[1]["kda"], 4.0)
        self.assertEqual(rows[0]["kda"], 9.0)  # no deaths: divide by 1, not 0

    def test_tier_rows_win_rate_pick_rate_and_minimum_games(self):
        stats = [{"hero_id": 1, "matches": 6000, "losses": 2400}, {"hero_id": 2, "matches": 6000, "losses": 3600},
                 {"hero_id": 3, "matches": 100, "losses": 10}]  # too few games for the tier list
        rows = tier_rows(stats, NAMES)
        self.assertEqual([r["hero"] for r in rows], ["Haze", "Rem"])
        self.assertAlmostEqual(rows[0]["win_rate"], 0.6)
        # Pick rate = hero's games / number of matches, where matches = all hero-games / 12.
        # (These made-up numbers give a rate above 100%, which real data can't; it only checks the formula.)
        self.assertAlmostEqual(rows[0]["pick_rate"], 6000 / (12100 / 12))

    def test_when(self):
        now = 1_000_000
        self.assertEqual(when(now - 120, now), "2m ago")
        self.assertEqual(when(now - 3 * 3600, now), "3h ago")
        self.assertEqual(when(now - 2 * 86400, now), "2d ago")
        self.assertEqual(when(None, now), "")


if __name__ == "__main__":
    unittest.main()
