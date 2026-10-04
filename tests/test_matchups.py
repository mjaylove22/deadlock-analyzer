"""Tests for matchup maths (pure logic; build_matchup's API calls are mocked)."""

import unittest
from unittest.mock import patch

import matchups
from matchups import average_win_rate, build_matchup, hero_breakdown, hero_matchups, popular_items

ME, A, B, C = 1, 2, 3, 4


def pair(hero, enemy, wins, games):
    return {"hero_id": hero, "enemy_hero_id": enemy, "wins": wins, "matches_played": games}


COUNTERS = [pair(ME, A, 450, 1000), pair(ME, B, 650, 1000), pair(ME, C, 10, 20), pair(A, ME, 550, 1000)]


class HeroMatchupsTests(unittest.TestCase):
    def test_average_is_over_all_of_the_heros_matchups(self):
        self.assertAlmostEqual(average_win_rate(COUNTERS, ME), (450 + 650 + 10) / 2020)

    def test_toughest_first_with_difference_from_average(self):
        result = hero_matchups(COUNTERS, ME, [B, A])
        self.assertEqual([m["enemy_hero_id"] for m in result], [A, B])
        average = average_win_rate(COUNTERS, ME)
        self.assertAlmostEqual(result[0]["vs_average"], 0.45 - average)

    def test_too_few_games_and_duplicate_enemies_are_skipped(self):
        result = hero_matchups(COUNTERS, ME, [A, A, C])
        self.assertEqual([m["enemy_hero_id"] for m in result], [A])  # C has only 20 games


class PopularItemsTests(unittest.TestCase):
    def test_most_bought_shop_items_with_win_rate(self):
        stats = [{"item_id": 10, "wins": 30, "matches": 60}, {"item_id": 11, "wins": 90, "matches": 100},
                 {"item_id": 99, "wins": 900, "matches": 1000}]  # 99 isn't a shop item
        items = popular_items(stats, {10: {"name": "Ten"}, 11: {"name": "Eleven"}})
        self.assertEqual([i["name"] for i in items], ["Eleven", "Ten"])
        self.assertAlmostEqual(items[0]["win_rate"], 0.9)


class BuildMatchupTests(unittest.TestCase):
    IDS = {"Me": ME, "A": A, "B": B}
    NAMES = {v: k for k, v in IDS.items()}

    def build(self, results):
        with patch.object(matchups.deadlock_api, "fetch_counter_stats", return_value=COUNTERS), \
             patch.object(matchups.deadlock_api, "get_item_stats", return_value=[]) as items, \
             patch.object(matchups.deadlock_api, "fetch_items", return_value={}):
            return build_matchup(results, self.IDS, self.NAMES), items

    def test_uses_the_other_team_as_enemies(self):
        results = [{"hero": "Me", "team": "friendly", "is_me": True}, {"hero": "A", "team": "enemy"},
                   {"hero": "B", "team": "friendly"}]
        matchup, items = self.build(results)
        self.assertEqual(matchup["hero"], "Me")
        self.assertEqual([m["enemy_hero"] for m in matchup["matchups"]], ["A"])
        items.assert_called_once_with(ME, [A])

    def test_none_when_the_user_is_not_in_the_lobby(self):
        matchup, _ = self.build([{"hero": "A", "team": "enemy"}])
        self.assertIsNone(matchup)


class HeroBreakdownTests(unittest.TestCase):
    def test_best_and_toughest_against_the_heros_average(self):
        counters = [pair(ME, A, 400, 1000), pair(ME, B, 600, 1000), pair(ME, C, 500, 1000)]
        names = {ME: "Me", A: "A", B: "B", C: "C"}
        with patch.object(matchups.deadlock_api, "fetch_counter_stats", return_value=counters),              patch.object(matchups.deadlock_api, "get_item_stats", return_value=[{"item_id": 7, "wins": 6, "matches": 10}]),              patch.object(matchups.deadlock_api, "fetch_items", return_value={7: {"name": "Seven", "slot": "spirit"}}):
            b = hero_breakdown(ME, names, shown=1)
        self.assertEqual(b["toughest"][0]["enemy_hero"], "A")
        self.assertEqual(b["best"][0]["enemy_hero"], "B")
        self.assertAlmostEqual(b["average_win_rate"], 0.5)
        self.assertEqual(b["items"][0]["slot"], "spirit")


if __name__ == "__main__":
    unittest.main()
