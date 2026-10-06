"""Tests for hero stats and badges (pure logic, no network)."""

import unittest

from insights import compute_badges, hero_summary

HERO = 10


def entry(hero_id, matches, wins=0, kills=0, deaths=0, assists=0, dpm=0.0):
    return {"hero_id": hero_id, "matches_played": matches, "wins": wins, "kills": kills,
            "deaths": deaths, "assists": assists, "damage_per_min": dpm}


def labels(entries, hero_id=HERO):
    return [label for label, _ in compute_badges(entries, hero_id)]


def labels_at(entries, now, hero_id=HERO):
    return [label for label, _ in compute_badges(entries, hero_id, now)]


class HeroSummaryTests(unittest.TestCase):
    def test_stats_on_current_hero(self):
        s = hero_summary([entry(HERO, 20, wins=12, kills=60, deaths=30, assists=90, dpm=800)], HERO)
        self.assertEqual(s["games"], 20)
        self.assertAlmostEqual(s["win_rate"], 0.6)
        self.assertAlmostEqual(s["kda"], 5.0)  # (60 + 90) / 30
        self.assertEqual(s["damage_per_min"], 800)

    def test_no_games_on_hero(self):
        self.assertIsNone(hero_summary([entry(99, 50)], HERO))

    def test_zero_deaths_does_not_divide_by_zero(self):
        self.assertEqual(hero_summary([entry(HERO, 1, kills=3, deaths=0, assists=1)], HERO)["kda"], 4.0)


class ComputeBadgesTests(unittest.TestCase):
    def test_one_trick(self):
        # 300 of 400 games on the current hero
        self.assertIn("ONE-TRICK", labels([entry(HERO, 300, wins=150), entry(2, 100)]))

    def test_main_without_enough_share_to_be_a_one_trick(self):
        found = labels([entry(HERO, 60, wins=30), entry(2, 55), entry(3, 50)])
        self.assertIn("ON MAIN", found)
        self.assertNotIn("ONE-TRICK", found)

    def test_comfort_pick_is_in_top_three(self):
        self.assertIn("COMFORT PICK", labels([entry(1, 100), entry(HERO, 40, wins=20), entry(3, 30)]))

    def test_first_game_and_new_on_hero(self):
        self.assertIn("FIRST GAME ON HERO", labels([entry(1, 100)]))
        self.assertIn("NEW ON HERO", labels([entry(1, 100), entry(HERO, 3)]))

    def test_old_data_never_claims_a_first_game(self):
        # The API's newest game for many players is weeks old, e.g. before a new hero came out
        now = 1_800_000_000
        old = [dict(entry(1, 100), last_played=now - 60 * 86400)]
        found = labels_at(old, now)
        self.assertIn("NO RECENT DATA", found)
        self.assertNotIn("FIRST GAME ON HERO", found)
        self.assertNotIn("NEW ON HERO", labels_at(old + [dict(entry(HERO, 3), last_played=now - 60 * 86400)], now))

    def test_current_data_still_says_first_game(self):
        now = 1_800_000_000
        found = labels_at([dict(entry(1, 100), last_played=now - 86400)], now)  # a day behind is normal
        self.assertIn("FIRST GAME ON HERO", found)
        self.assertNotIn("NO RECENT DATA", found)

    def test_win_rate_badges_need_enough_games(self):
        self.assertIn("HIGH WR", labels([entry(1, 100), entry(HERO, 30, wins=20)]))
        self.assertIn("LOW WR", labels([entry(1, 100), entry(HERO, 30, wins=10)]))
        # 4 wins out of 5 is 80%, but too few games to mean anything
        self.assertNotIn("HIGH WR", labels([entry(1, 100), entry(HERO, 5, wins=4)]))

    def test_veteran(self):
        self.assertIn("VETERAN", labels([entry(1, 900), entry(HERO, 200, wins=100)]))

    def test_too_little_history_says_so_and_nothing_else(self):
        self.assertEqual(labels([entry(HERO, 5, wins=5)]), ["FEW RECORDED GAMES"])


if __name__ == "__main__":
    unittest.main()
