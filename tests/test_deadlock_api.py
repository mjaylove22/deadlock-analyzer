"""Tests for the request parameters sent to the Deadlock API (no network: get_json is mocked)."""

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


if __name__ == "__main__":
    unittest.main()
