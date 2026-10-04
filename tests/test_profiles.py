"""Tests for player-page and tier-list data (pure functions, no network)."""

import unittest

from profiles import RANK_BANDS, describe_match, hero_rows, match_type, mode_breakdown, tier_rows, top_mates, when

NAMES = {1: "Haze", 2: "Rem", 3: "Newbie"}


def match(game_mode=1, won=True, hero_id=1, start=1000, match_mode=1):
    # A win is "the player's team is the winning team"; player_match_outcome is usually 0 (invalid)
    return {"match_id": start, "hero_id": hero_id, "game_mode": game_mode, "match_mode": match_mode, "player_team": 0,
            "match_result": 0 if won else 1, "player_match_outcome": 0, "player_kills": 5, "player_deaths": 2,
            "player_assists": 9, "net_worth": 30000, "match_duration_s": 1830, "start_time": start}


class ProfilesTests(unittest.TestCase):
    def test_win_comes_from_the_winning_team_not_the_outcome_field(self):
        self.assertTrue(describe_match(match(won=True), NAMES)["won"])
        self.assertFalse(describe_match(match(won=False), NAMES)["won"])

    def test_describe_match(self):
        m = describe_match(match(game_mode=4), NAMES)
        self.assertEqual((m["hero"], m["mode"], m["minutes"]), ("Haze", "Street Brawl", 30))

    def test_mode_breakdown_splits_ranked_unranked_and_street_brawl(self):
        matches = [match(1, True), match(1, False), match(1, True), match(4, True), match(1, True, match_mode=4)]
        rows = mode_breakdown(matches)
        self.assertEqual([(r["mode"], r["games"]) for r in rows], [("Unranked", 3), ("Street Brawl", 1), ("Ranked", 1)])
        self.assertAlmostEqual(rows[0]["win_rate"], 2 / 3)

    def test_match_type(self):
        self.assertEqual(match_type(match(1, match_mode=4)), "Ranked")
        self.assertEqual(match_type(match(1, match_mode=1)), "Unranked")
        self.assertEqual(match_type(match(4, match_mode=1)), "Street Brawl")  # always unranked
        self.assertEqual(describe_match(match(1, match_mode=4), NAMES)["type"], "Ranked")

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


    def test_ban_share_is_each_heros_part_of_all_bans(self):
        stats = [{"hero_id": 1, "matches": 6000, "losses": 3000}, {"hero_id": 2, "matches": 6000, "losses": 3000}]
        rows = tier_rows(stats, NAMES, bans=[{"hero_id": 1, "bans": 300}, {"hero_id": 2, "bans": 100}])
        self.assertEqual({r["hero"]: r["ban_share"] for r in rows}, {"Haze": 0.75, "Rem": 0.25})
        self.assertIsNone(tier_rows(stats, NAMES)[0]["ban_share"])  # no ban data (e.g. Street Brawl)
        only_haze = tier_rows(stats, NAMES, bans=[{"hero_id": 1, "bans": 300}])
        self.assertIsNone(next(r for r in only_haze if r["hero"] == "Rem")["ban_share"])  # missing hero: unknown, not 0%

    def test_top_mates_most_games_first(self):
        mates = [{"mate_id": 1, "matches_played": 20, "wins": 10}, {"mate_id": 2, "matches_played": 700, "wins": 350},
                 {"mate_id": 3, "matches_played": 60, "wins": 45}]
        top = top_mates(mates, count=2)
        self.assertEqual([m["account_id"] for m in top], [2, 3])
        self.assertAlmostEqual(top[1]["win_rate"], 0.75)

    def test_rank_bands_cover_every_rank_once(self):
        tiers = [t for _, band in RANK_BANDS if band for t in range(band[0], band[1] + 1)]
        self.assertEqual(tiers, list(range(1, 12)))  # Initiate (1) to Eternus (11), no gaps or overlaps


if __name__ == "__main__":
    unittest.main()
