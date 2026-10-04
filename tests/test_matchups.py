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
        items = popular_items(stats, {10: {"name": "Ten", "image": "ten.png"}, 11: {"name": "Eleven"}})
        self.assertEqual([i["name"] for i in items], ["Eleven", "Ten"])
        self.assertAlmostEqual(items[0]["win_rate"], 0.9)
        self.assertEqual(items[1]["image"], "ten.png")  # for the icon


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
        self.assertEqual(items.call_args.args[:2], (ME, [A]))

    def test_four_a_side_is_street_brawl_and_six_is_normal(self):
        def lobby(per_team):
            return [{"team": team} for team in ("friendly", "enemy") for _ in range(per_team)]
        self.assertEqual(matchups.lobby_mode(lobby(4)), "street_brawl")
        self.assertEqual(matchups.lobby_mode(lobby(6)), "normal")

    def test_none_when_the_user_is_not_in_the_lobby(self):
        matchup, _ = self.build([{"hero": "A", "team": "enemy"}])
        self.assertIsNone(matchup)


def item_stat(item_id, wins, matches):
    return {"item_id": item_id, "wins": wins, "matches": matches}


class ItemLiftTests(unittest.TestCase):
    ITEMS = {1: {"name": "Late Big Item"}, 2: {"name": "Counter Item"}, 3: {"name": "Rare Item"}}

    def test_items_are_compared_with_themselves_and_the_matchup(self):
        usual = [item_stat(1, 6000, 10000), item_stat(2, 5000, 10000), item_stat(3, 50, 100)]
        # A hard matchup (-2 points for the hero): the late item keeps its usual edge over that, the
        # counter item gains 4 points on its usual, and the rare item has too few games to judge
        here = [item_stat(1, 580, 1000), item_stat(2, 520, 1000), item_stat(3, 90, 100)]
        lifted = matchups.item_lift(here, usual, self.ITEMS, matchup_shift=-0.02, games=4000)
        self.assertEqual([i["name"] for i in lifted], ["Counter Item"])
        self.assertAlmostEqual(lifted[0]["lift"], 0.04)
        self.assertAlmostEqual(lifted[0]["bought_share"], 0.25)

    def test_nothing_to_compare_gives_nothing(self):
        self.assertEqual(matchups.item_lift(None, [], self.ITEMS, 0.0), [])

    def test_expected_win_rate_adds_the_average_shift(self):
        self.assertAlmostEqual(matchups.expected_win_rate(0.55, [{"vs_average": -0.02}, {"vs_average": 0.04}]), 0.56)
        self.assertEqual(matchups.expected_win_rate(0.55, []), 0.55)


class HeroBreakdownTests(unittest.TestCase):
    NAMES = {ME: "Me", A: "A", B: "B", C: "C"}
    COUNTERS = [pair(ME, A, 400, 1000), pair(ME, B, 600, 1000), pair(ME, C, 500, 1000)]

    def breakdown(self, counters):
        with patch.object(matchups.deadlock_api, "fetch_counter_stats", side_effect=counters), \
                patch.object(matchups.deadlock_api, "get_item_stats", return_value=[{"item_id": 7, "wins": 6, "matches": 10}]), \
                patch.object(matchups.deadlock_api, "fetch_items", return_value={7: {"name": "Seven", "slot": "spirit"}}):
            return hero_breakdown(ME, self.NAMES, shown=1)

    def test_best_and_toughest_against_the_heros_average(self):
        b = self.breakdown(lambda *args: self.COUNTERS)
        self.assertEqual(b["toughest"][0]["enemy_hero"], "A")
        self.assertEqual(b["best"][0]["enemy_hero"], "B")
        self.assertAlmostEqual(b["average_win_rate"], 0.5)
        self.assertEqual(b["items"][0]["slot"], "spirit")

    def test_items_still_come_back_when_matchups_time_out(self):
        # Regression: a slow matchup request at one rank range made the whole hero page fail
        b = self.breakdown(TimeoutError("The read operation timed out"))
        self.assertIsNone(b["toughest"])
        self.assertIsNone(b["best"])
        self.assertEqual(b["items"][0]["name"], "Seven")


if __name__ == "__main__":
    unittest.main()
