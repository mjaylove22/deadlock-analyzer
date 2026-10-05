"""Tests for the performance ratings (pure functions; percentiles shaped like the API's answer)."""

import unittest
from unittest.mock import patch

import match_review
from performance import (STATS, band_for_badge, compared_text, grade, length_window, percentile_of, rate_player,
                         verdict)
from profiles import RANK_BANDS


def metric(p1, p5, p10, p25, p50, p75, p90, p95, p99):
    return dict(zip((f"percentile{p}" for p in (1, 5, 10, 25, 50, 75, 90, 95, 99)),
                    (p1, p5, p10, p25, p50, p75, p90, p95, p99)), avg=p50, std=1)


# Every stat a typical player on some hero gets; the median is the 50th percentile
METRICS = {
    "net_worth_per_min": metric(600, 800, 900, 1000, 1100, 1200, 1300, 1400, 1600),
    "player_damage_per_min": metric(200, 400, 450, 600, 770, 950, 1250, 1400, 1800),
    "kda": metric(0.5, 1.0, 1.2, 2.0, 3.9, 7.0, 13.9, 18.0, 30.0),
    "deaths": metric(0, 1, 2, 3, 5, 7, 9, 10, 13),
    "boss_damage_per_min": metric(0, 10, 24, 60, 128, 220, 334, 400, 520),
    "last_hits": metric(40, 80, 95, 120, 153, 190, 228, 250, 300),
    "player_healing_per_min": metric(0, 0, 0, 0, 0, 0, 0, 0, 0),  # a hero nobody heals with
    "accuracy": metric(0.35, 0.45, 0.47, 0.52, 0.565, 0.61, 0.65, 0.67, 0.72),
    "crit_shot_rate": metric(0.02, 0.06, 0.085, 0.11, 0.137, 0.16, 0.19, 0.21, 0.26),
    "player_damage_taken_per_min": metric(500, 650, 730, 900, 1100, 1300, 1500, 1600, 1900),
}

PLAYER = {"net_worth": 36000, "damage": 19500, "kills": 4, "deaths": 1, "assists": 21, "boss_damage": 9000,
          "last_hits": 141, "healing": 0, "shots_hit": 600, "shots_missed": 400, "crits": 14, "hero_hits": 86,
          "damage_taken": 26000}


class PercentileTests(unittest.TestCase):
    M = metric(0, 1, 2, 3, 5, 7, 9, 10, 13)

    def test_straight_lines_between_the_api_percentiles(self):
        self.assertEqual(percentile_of(5, self.M), 50)
        self.assertEqual(percentile_of(6, self.M), 62.5)    # halfway from the 50th (5) to the 75th (7)
        self.assertEqual(percentile_of(8, self.M), 82.5)

    def test_outside_the_range(self):
        self.assertEqual(percentile_of(20, self.M), 99.5)
        self.assertEqual(percentile_of(-1, self.M), 0.5)

    def test_ties_count_as_the_middle_of_the_tied_range(self):
        healing = metric(0, 0, 0, 0, 0, 10, 20, 30, 40)    # half the players heal nothing
        self.assertEqual(percentile_of(0, healing), 25.5)  # (1 + 50) / 2


class RatePlayerTests(unittest.TestCase):
    def setUp(self):
        self.rating = rate_player(PLAYER, 30, METRICS)
        self.rows = {r["key"]: r for r in self.rating["rows"]}

    def test_formulas_match_the_api(self):
        self.assertAlmostEqual(self.rows["souls"]["value"], 1200)          # per minute
        self.assertAlmostEqual(self.rows["kda"]["value"], 25)              # (4 + 21) / 1
        self.assertAlmostEqual(self.rows["accuracy"]["value"], 0.6)        # 600 / (600 + 400)
        self.assertAlmostEqual(self.rows["crits"]["value"], 14 / 100)      # crits / (crits + other hits)

    def test_lower_is_better_for_deaths(self):
        self.assertEqual(self.rows["deaths"]["percentile"], 5)   # 1 death: only 5% died less...
        self.assertEqual(self.rows["deaths"]["good"], 95)        # ...so better than 95%

    def test_a_stat_nobody_on_the_hero_has_is_left_out(self):
        self.assertNotIn("healing", self.rows)

    def test_damage_taken_is_shown_but_not_judged(self):
        self.assertIsNone(self.rows["damage_taken"]["good"])
        self.assertNotIn("damage_taken", [r["key"] for r in self.rating["strengths"] + self.rating["weaknesses"]])

    def test_score_averages_the_core_stats(self):
        core = [r["good"] for r in self.rating["rows"] if r["in_score"]]
        self.assertEqual(len(core), 5)
        self.assertEqual(self.rating["score"], round(sum(core) / 5))

    def test_strengths_and_weaknesses(self):
        self.assertEqual([r["key"] for r in self.rating["strengths"]], ["kda", "deaths", "objectives"])
        self.assertEqual(self.rating["weaknesses"], [])
        weak = rate_player(dict(PLAYER, deaths=12, kills=0, assists=2), 30, METRICS)
        self.assertEqual([r["key"] for r in weak["weaknesses"]], ["kda", "deaths"])

    def test_old_saved_reviews_without_the_new_stats(self):
        old = {k: v for k, v in PLAYER.items() if k not in ("boss_damage", "shots_hit", "shots_missed", "crits", "hero_hits")}
        keys = [r["key"] for r in rate_player(old, 30, METRICS)["rows"]]
        self.assertNotIn("accuracy", keys)
        self.assertIn("souls", keys)

    def test_every_stat_is_read_from_a_real_api_field(self):
        api_fields = {"net_worth_per_min", "player_damage_per_min", "kda", "deaths", "boss_damage_per_min",
                      "last_hits", "player_healing_per_min", "accuracy", "crit_shot_rate",
                      "player_damage_taken_per_min"}  # names from a real /v1/analytics/player-stats/metrics answer
        self.assertEqual({s.metric for s in STATS}, api_fields)


class WordingTests(unittest.TestCase):
    def test_grades_and_verdicts(self):
        self.assertEqual([grade(g) for g in (95, 75, 50, 20, 5)], ["excellent", "good", "typical", "below par", "poor"])
        self.assertEqual([verdict(s) for s in (80, 65, 50, 30, 10)],
                         ["Great game", "Good game", "Average game", "Tough game", "Rough game"])

    def test_compared_text(self):
        self.assertEqual(compared_text({"good": 82.4, "percentile": 82.4}), "better than 82%")
        self.assertEqual(compared_text({"good": 30, "percentile": 70}), "worse than 70%")
        self.assertEqual(compared_text({"good": 99.5, "percentile": 99.5}), "better than 99%")
        self.assertEqual(compared_text({"good": None, "percentile": 64}), "more than 64%")


class ComparisonTests(unittest.TestCase):
    def test_match_length_window_in_whole_minutes(self):
        self.assertEqual(length_window(35.4), (29, 41))
        self.assertEqual(length_window(4.0), (0, 10))

    def test_rank_band(self):
        self.assertEqual(band_for_badge(82.5, RANK_BANDS)[0], "Emissary - Phantom")   # tier 8
        self.assertEqual(band_for_badge(None, RANK_BANDS)[0], "All ranks")

    def test_rate_match_uses_the_match_rank_or_else_the_players_ranks(self):
        review = {"mode": "Normal", "minutes": 30, "team_badges": [0, 0],
                  "players": [dict(PLAYER, account_id=1, hero="Haze"), dict(PLAYER, account_id=2, hero="Rem")]}
        ranks = {1: {"badge": 75}, 2: {"badge": 3}}  # the second player is unranked (tier 0)
        with patch.object(match_review, "fetch_ranks", return_value=ranks), \
             patch.object(match_review.deadlock_api, "get_player_metrics", return_value=METRICS) as get_metrics:
            rated = match_review.rate_match(review, {7: "Haze", 8: "Rem"})
        self.assertEqual(rated["band"], "Emissary - Phantom")  # from the one ranked player
        self.assertEqual(rated["window"], (24, 36))
        self.assertEqual(get_metrics.call_count, 2)            # one request per hero
        get_metrics.assert_any_call(7, "normal", (7, 9), 24, 36)
        self.assertEqual(len(rated["ratings"]), 2)

    def test_street_brawl_has_no_rank_filter(self):
        review = {"mode": "Street Brawl", "minutes": 14, "team_badges": [0, 0],
                  "players": [dict(PLAYER, account_id=1, hero="Haze")]}
        with patch.object(match_review, "fetch_ranks") as fetch_ranks, \
             patch.object(match_review.deadlock_api, "get_player_metrics", return_value=METRICS) as get_metrics:
            match_review.rate_match(review, {7: "Haze"})
        fetch_ranks.assert_not_called()
        get_metrics.assert_called_once_with(7, "street_brawl", None, 8, 20)


if __name__ == "__main__":
    unittest.main()
