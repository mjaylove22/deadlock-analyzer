"""Tests for waiting on a finished match's data without wasting the 3-an-hour Steam fetches."""

import os
import tempfile
import unittest
from unittest.mock import patch

import match_review
import postgame
from match_review import MatchUnavailable
from postgame import GIVE_UP_AFTER_S, PostGame, is_our_match, last_session, recent_matches
from profiles import describe_match


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


def match(match_id, start, won=True, minutes=30, kills=6, deaths=3, assists=9, net_worth=30000):
    return {"match_id": match_id, "start_time": start, "won": won, "minutes": minutes, "kills": kills,
            "deaths": deaths, "assists": assists, "net_worth": net_worth, "hero": "Haze", "mode": "Normal"}


class SessionTests(unittest.TestCase):
    """Home's recent matches and session summary, from the API's history plus kept end screens."""

    def test_end_screens_fill_in_what_the_api_doesnt_have_yet(self):
        api = [match(2, 10_000), match(1, 5_000)]
        screens = [match(1, 99_999, kills=0), match(3, 20_000)]  # 1: an old match opened in game; the API's row wins
        self.assertEqual([(m["match_id"], m["start_time"]) for m in recent_matches(api, screens)],
                         [(3, 20_000), (2, 10_000), (1, 5_000)])

    def test_session_ends_at_a_long_break(self):
        hour = 3600
        matches = [match(4, 20 * hour, won=None), match(3, 19 * hour, won=False, kills=10, net_worth=60000),
                   match(2, 17 * hour, minutes=40), match(1, 10 * hour)]  # 1 ended 6.5 h before 2 started
        s = last_session(matches)
        self.assertEqual((s["games"], s["wins"], s["losses"]), (3, 1, 1))  # 4's result wasn't read
        self.assertAlmostEqual(s["kills"], (6 + 10 + 6) / 3)
        self.assertAlmostEqual(s["souls_per_min"], 120000 / 100)
        self.assertEqual(s["ended"], 20 * hour + 30 * 60)
        self.assertIsNone(last_session([]))

    def test_rank_change_sums_ranked_matches_but_not_placements(self):
        def api_row(start, match_mode, delta, calibration=0):  # fields as in a real /match-history response
            return {"match_id": start, "start_time": start, "hero_id": 1, "game_mode": 1, "match_mode": match_mode,
                    "player_team": 0, "match_result": 0, "player_kills": 6, "player_deaths": 3, "player_assists": 9,
                    "net_worth": 30000, "match_duration_s": 1800, "ranked_delta": delta, "ranked_calibration_match": calibration}
        api = [describe_match(m, {1: "Haze"}) for m in
               (api_row(9000, 4, -300), api_row(6000, 4, 370), api_row(3000, 1, None))]  # ranked, ranked, unranked
        session = last_session(recent_matches(api, [match(1, 12_000)]))  # + an end screen the API doesn't have yet
        self.assertEqual((session["games"], session["rank_change"], session["ranked_games"]), (4, 70, 2))
        placements = [describe_match(api_row(3000, 4, 0, calibration=2), {1: "Haze"})]
        self.assertIsNone(last_session(placements)["rank_change"])

    def test_remember_end_screen_keeps_your_row_once(self):
        path = os.path.join(tempfile.mkdtemp(), "end_screens.json")
        me = {"hero": "Haze", "won": False, "kills": 4, "deaths": 2, "assists": 7, "net_worth": 25000}
        screen = {"me": me, "minutes": 32.5, "mode": "Normal", "winning_team": None, "players": [me]}
        with patch.object(postgame, "END_SCREENS_FILE", path):
            postgame.remember_end_screen(dict(screen, me=None), 111203456)  # you weren't found on it
            self.assertEqual(postgame.load_end_screens(), [])
            postgame.remember_end_screen(screen, 111203456, now=10_000)
            postgame.remember_end_screen(screen, 111203456, now=10_000)  # the same end screen seen again
            rows = postgame.load_end_screens()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["won"], rows[0]["minutes"], rows[0]["start_time"]), (None, 32, 10_000 - 1950))


if __name__ == "__main__":
    unittest.main()
