"""Tests for the request parameters sent to the Deadlock API (no network: get_json is mocked)."""

import io
import json
import unittest
from unittest.mock import patch

import deadlock_api


class SearchSteamProfilesTests(unittest.TestCase):
    def test_does_not_hide_low_activity_accounts(self):
        # Regression: with the API's default (5 recorded matches in 30 days), a player who mostly
        # plays bot matches was filtered out, so a different same-named account was picked.
        with patch.object(deadlock_api, "get_json", return_value=[]) as get_json:
            deadlock_api.search_steam_profiles("Grey Mirage")
        path, params = get_json.call_args.args
        self.assertEqual(path, "/v1/players/steam-search")
        self.assertEqual(params["min_matches_played_last_30d"], 0)
        self.assertEqual(params["search_query"], "Grey Mirage")


class ResponseCacheTests(unittest.TestCase):
    def setUp(self):
        deadlock_api._memory.clear()
        self.addCleanup(deadlock_api._memory.clear)

    def fake_urlopen(self):
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(json.dumps({"n": len(calls)}).encode())
        return calls, urlopen

    def test_repeat_requests_are_answered_from_memory(self):
        calls, urlopen = self.fake_urlopen()
        with patch.object(deadlock_api.urllib.request, "urlopen", side_effect=urlopen):
            first = deadlock_api.get_json("/v1/x", {"a": 1})
            second = deadlock_api.get_json("/v1/x", {"a": 1})
            other = deadlock_api.get_json("/v1/x", {"a": 2})   # different parameters: a different answer
        self.assertEqual(len(calls), 2)
        self.assertIs(first, second)
        self.assertNotEqual(first, other)

    def test_max_age_zero_always_fetches(self):
        calls, urlopen = self.fake_urlopen()
        with patch.object(deadlock_api.urllib.request, "urlopen", side_effect=urlopen):
            deadlock_api.get_json("/v1/x")
            deadlock_api.get_json("/v1/x", max_age=0)
        self.assertEqual(len(calls), 2)

    def test_memory_is_capped(self):
        calls, urlopen = self.fake_urlopen()
        with patch.object(deadlock_api.urllib.request, "urlopen", side_effect=urlopen), \
             patch.object(deadlock_api, "MAX_CACHED", 3):
            for n in range(5):
                deadlock_api.get_json("/v1/x", {"n": n})
        self.assertEqual(len(deadlock_api._memory), 3)

    def test_rank_curves_condense_badges_into_tiers(self):
        rows = [{"hero_id": 7, "bucket": 71, "matches": 100, "losses": 40},   # Emissary 1
                {"hero_id": 7, "bucket": 76, "matches": 50, "losses": 30},    # Emissary 6: same tier
                {"hero_id": 7, "bucket": 0, "matches": 999, "losses": 1}]     # unranked matches: left out
        with patch.object(deadlock_api, "get_json", return_value=rows), \
             patch.object(deadlock_api, "disk_cached", side_effect=lambda name, build, max_age: build()):
            curves = deadlock_api.fetch_rank_curves()
        self.assertEqual(curves, {"7": {"7": [80, 150]}})

    def test_hero_stats_match_mode_is_only_sent_when_asked(self):
        with patch.object(deadlock_api, "get_json", return_value=[]) as get_json:
            deadlock_api.get_hero_stats([1])
            deadlock_api.get_hero_stats([1], match_mode="ranked")
        self.assertNotIn("match_mode", get_json.call_args_list[0].args[1])
        self.assertEqual(get_json.call_args_list[1].args[1]["match_mode"], "ranked")

    def test_badge_range_covers_whole_tiers(self):
        self.assertEqual(deadlock_api.badge_range((7, 9)), {"min_average_badge": 70, "max_average_badge": 99})
        self.assertEqual(deadlock_api.badge_range(None), {})

    def test_parallel_keeps_order(self):
        self.assertEqual(deadlock_api.parallel(lambda: 1, lambda: 2, lambda: 3), [1, 2, 3])

    def test_parallel_can_return_none_for_failed_calls(self):
        def slow():
            raise TimeoutError("timed out")
        with self.assertLogs("deadlock_api", "WARNING"):
            self.assertEqual(deadlock_api.parallel(lambda: 1, slow, allow_failures=True), [1, None])
        with self.assertRaises(TimeoutError):
            deadlock_api.parallel(lambda: 1, slow)

    def test_analytics_get_more_time_than_other_requests(self):
        # The server calculates analytics on request, which can be slow when it's busy
        deadlock_api._memory.clear()
        self.addCleanup(deadlock_api._memory.clear)
        with patch.object(deadlock_api, "_download", return_value=[]) as download:
            deadlock_api.get_json("/v1/analytics/hero-counter-stats", {"game_mode": "normal"})
            deadlock_api.get_json("/v1/players/steam-search", {"search_query": "x"})
        self.assertEqual([c.args[1] for c in download.call_args_list],
                         [deadlock_api.ANALYTICS_TIMEOUT_SECONDS, deadlock_api.TIMEOUT_SECONDS])


if __name__ == "__main__":
    unittest.main()
