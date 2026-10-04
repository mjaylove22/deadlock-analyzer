"""Tests for the items page data: daily stats (whole UTC days, today left out) and the rows."""

import calendar
import unittest
from unittest.mock import patch

import deadlock_api
import item_trends


class DailyItemStatsTests(unittest.TestCase):
    def test_whole_days_with_today_left_out_and_a_weighted_buy_time(self):
        evening = calendar.timegm((2026, 10, 4, 20, 0, 0))
        midnight = calendar.timegm((2026, 10, 4, 0, 0, 0))
        yesterday = midnight - 86400
        items = [{"bucket": yesterday, "item_id": 7, "wins": 60, "matches": 100, "avg_buy_time_s": 600},
                 {"bucket": yesterday - 86400, "item_id": 7, "wins": 30, "matches": 300, "avg_buy_time_s": 1000},
                 {"bucket": midnight, "item_id": 7, "wins": 1, "matches": 2}]  # today: not over yet
        heroes = [{"bucket": yesterday, "matches": 1200}, {"bucket": yesterday - 86400, "matches": 2400}]

        def fake_get_json(path, params, max_age):
            return items if "item" in path else heroes
        with patch.object(deadlock_api.time, "time", return_value=evening), \
                patch.object(deadlock_api, "disk_cached", lambda name, build, max_age=0: build()), \
                patch.object(deadlock_api, "get_json", side_effect=fake_get_json) as get_json:
            daily = deadlock_api.fetch_daily_item_stats(days=2)
        self.assertEqual(get_json.call_args.args[1]["max_unix_timestamp"], midnight - 1)
        self.assertEqual(daily["days"], [yesterday - 86400, yesterday])
        self.assertEqual(daily["items"]["7"], [[30, 300], [60, 100]])
        self.assertEqual(daily["player_games"], [2400, 1200])
        self.assertEqual(daily["buy_time"]["7"], 900)  # (1000 x 300 + 600 x 100) / 400


class ItemTrendsTests(unittest.TestCase):
    def test_rows_most_bought_first_with_share_and_trend(self):
        daily = {"days": list(range(14)), "player_games": [10000] * 14, "buy_time": {"1": 500},
                 "items": {"1": [[300, 600]] * 14,     # bought by 6% of players
                           "2": [[500, 1000]] * 14,    # 10%
                           "3": [[5, 10]] * 14,        # too few purchases to list
                           "99": [[500, 1000]] * 14}}  # not a shop item
        shop = {1: {"name": "One", "slot": "spirit"}, 2: {"name": "Two", "slot": "weapon"},
                3: {"name": "Three", "slot": "weapon"}}
        with patch.object(deadlock_api, "fetch_daily_item_stats", return_value=daily), \
                patch.object(deadlock_api, "fetch_items", return_value=shop):
            rows = item_trends.item_trends()["rows"]
        self.assertEqual([r["name"] for r in rows], ["Two", "One"])
        self.assertAlmostEqual(rows[0]["bought"], 0.1)
        self.assertEqual(rows[1]["buy_time"], 500)
        self.assertTrue(rows[0]["trend"]["steady"])
        self.assertEqual(len(rows[0]["trend"]["weeks"]), 14)


if __name__ == "__main__":
    unittest.main()
