"""Tests for waiting on a finished match's data without wasting the 3-an-hour Steam fetches."""

import unittest
from unittest.mock import patch

import match_review
from match_review import MatchUnavailable
from postgame import GIVE_UP_AFTER_S, PostGame, is_our_match


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(match_review, "_steam_fetches", [])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []  # (match_id, allow_steam) of each check

    def not_ready(self, match_id, steam):
        self.calls.append((match_id, steam))
        raise MatchUnavailable("This match isn't available yet.")

    def test_stored_copy_every_check_and_steam_only_at_3_10_and_25_minutes(self):
        game = PostGame(42, ended_at=0)
        for minute in range(0, 40):
            game.attempt(self.not_ready, now=minute * 60)
        steam_minutes = [minute for minute, (_, steam) in enumerate(self.calls) if steam]
        self.assertEqual(steam_minutes, [3, 10, 25])
        self.assertEqual(game.checks, 40)
        self.assertFalse(game.done)
        self.assertEqual(game.last_error, "This match isn't available yet.")

    def test_no_steam_fetch_when_this_hours_are_used_up(self):
        match_review._steam_fetches.extend([100.0, 110.0, 120.0])  # e.g. match reviews opened by hand
        game = PostGame(42, ended_at=0)
        game.attempt(self.not_ready, now=200)
        self.assertEqual(self.calls, [(42, False)])
        self.assertEqual(game.steam_tries, 0)  # the try is kept for later

    def test_ready(self):
        game = PostGame(42, ended_at=0)
        summary = game.attempt(lambda match_id, steam: {"match_id": match_id}, now=30)
        self.assertEqual(summary, {"match_id": 42})
        self.assertTrue(game.done and game.ready)

    def test_gives_up_after_an_hour(self):
        game = PostGame(42, ended_at=0)
        self.assertFalse(game.expired(GIVE_UP_AFTER_S - 1))
        self.assertTrue(game.expired(GIVE_UP_AFTER_S + 1))


class IsOurMatchTests(unittest.TestCase):
    SUMMARY = {"players": [{"account_id": 1, "hero": "Haze"}, {"account_id": 2, "hero": "Rem"},
                           {"account_id": 3, "hero": "Paige"}, {"account_id": 4, "hero": "Lash"}]}

    def test_by_account(self):
        self.assertTrue(is_our_match(self.SUMMARY, {"account_id": 3}))
        self.assertFalse(is_our_match(self.SUMMARY, {"account_id": 9}))

    def test_by_the_lobbys_heroes_when_no_account_is_set(self):
        self.assertTrue(is_our_match(self.SUMMARY, None, ["Haze", "Rem", "Vyper", "Kelvin"]))       # half
        self.assertFalse(is_our_match(self.SUMMARY, None, ["Haze", "Vyper", "Kelvin", "Bebop"]))    # a quarter
        self.assertFalse(is_our_match(self.SUMMARY, None, []))                                       # nothing to go on


if __name__ == "__main__":
    unittest.main()
