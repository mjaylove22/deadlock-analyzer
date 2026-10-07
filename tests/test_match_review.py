"""Tests for the post-game review (pure functions; disk saving uses a throwaway folder)."""

import os
import tempfile
import unittest
from unittest.mock import patch

import match_review
from match_review import compare_to_usual, lobby_place, networth_lead, summarize

NAMES = {1: "Haze", 2: "Rem"}
ITEMS = {10: {"name": "Opening Rounds", "slot": "weapon"}, 11: {"name": "Extra Health", "slot": "vitality"}}


def player(account_id, team, hero_id, net_worth, stats, items=()):
    return {"account_id": account_id, "team": team, "hero_id": hero_id, "kills": 5, "deaths": 2, "assists": 7,
            "net_worth": net_worth, "last_hits": 100, "denies": 3, "level": 30, "stats": stats, "items": list(items)}


def snap(time_s, net_worth, damage=0, healing=0):
    return {"time_stamp_s": time_s, "net_worth": net_worth, "player_damage": damage, "player_healing": healing,
            "player_damage_taken": 0}


METADATA = {"match_info": {
    "match_id": 123, "start_time": 1000, "duration_s": 1800, "game_mode": 1, "match_mode": 1, "winning_team": 1,
    "average_badge_team0": 0, "average_badge_team1": 0,
    "players": [
        player(1, 0, 1, 30000, [snap(600, 8000), snap(1800, 30000, damage=20000, healing=500)],
               items=[{"item_id": 11, "game_time_s": 500, "sold_time_s": 0},
                      {"item_id": 10, "game_time_s": 60, "sold_time_s": 0},
                      {"item_id": 10, "game_time_s": 30, "sold_time_s": 400},     # sold: not in the final build
                      {"item_id": 99, "game_time_s": 10, "sold_time_s": 0}]),     # not a shop item
        player(2, 1, 2, 40000, [snap(600, 9000), snap(1800, 40000, damage=30000)]),
    ]}}


class MatchReviewTests(unittest.TestCase):
    def test_summary_keeps_what_the_page_shows(self):
        s = summarize(METADATA, NAMES, ITEMS)
        haze = s["players"][0]
        self.assertEqual((s["minutes"], s["mode"], haze["hero"], haze["won"]), (30, "Normal", "Haze", False))
        self.assertEqual((haze["damage"], haze["healing"]), (20000, 500))
        self.assertEqual([i["name"] for i in haze["items"]], ["Opening Rounds", "Extra Health"])  # bought order

    def test_networth_lead_is_team0_minus_team1(self):
        lead = networth_lead(METADATA["match_info"]["players"])
        self.assertEqual(lead, [[10.0, -1000.0], [30.0, -10000.0]])

    def test_lobby_place(self):
        players = [{"damage": 5}, {"damage": 9}, {"damage": 7}]
        self.assertEqual([lobby_place(players, p, "damage") for p in players], [3, 1, 2])

    def test_compare_to_usual(self):
        me = {"net_worth": 33000, "damage": 15000, "healing": 100, "last_hits": 90}
        usual = {"networth_per_min": 1000, "damage_per_min": 600, "last_hits_per_min": 3}
        vs = compare_to_usual(me, 30, usual)
        self.assertAlmostEqual(vs["net_worth"], 0.10)   # 1,100 per minute vs 1,000
        self.assertAlmostEqual(vs["damage"], -1 / 6)    # 500 vs 600
        self.assertIsNone(vs["healing"])                # no healing average to compare with
        self.assertEqual(compare_to_usual(me, 30, None)["damage"], None)

    def test_only_the_newest_summaries_are_kept_on_disk(self):
        folder = tempfile.mkdtemp()
        with patch.object(match_review, "CACHE_DIR", folder), patch.object(match_review, "MAX_SAVED_MATCHES", 2):
            for match_id in (1, 2, 3):
                match_review.save({"match_id": match_id})
                path = os.path.join(folder, f"{match_id}.json")
                os.utime(path, (match_id, match_id))  # make the save order unambiguous
            match_review.save({"match_id": 4})
            self.assertEqual(sorted(os.listdir(folder)), ["3.json", "4.json"])
            self.assertEqual(match_review.load_saved(4), {"match_id": 4})


    def test_a_failed_match_is_not_requested_again_right_away(self):
        import urllib.error
        error = urllib.error.HTTPError("url", 429, "Too Many Requests", None, None)
        with patch.object(match_review, "load_saved", return_value=None), \
             patch.object(match_review.deadlock_api, "get_json", side_effect=error) as get_json, \
             patch.dict(match_review._failed, clear=True):
            for _ in range(3):
                with self.assertRaises(match_review.MatchUnavailable) as caught:
                    match_review.match_review(555, NAMES)
        self.assertEqual(get_json.call_count, 1)
        self.assertIn("3 an hour", str(caught.exception))


class FetchMetadataTests(unittest.TestCase):
    """The stored copy is free; Steam allows 3 an hour, so it's only asked when needed and counted."""

    def setUp(self):
        patcher = patch.object(match_review, "_steam_fetches", [])
        self.steam_fetches = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def not_stored(path, params=None, max_age=0):
        import urllib.error
        if params and params.get("disable_steam") == "true":
            raise urllib.error.HTTPError(path, 404, "Not Found", None, None)
        return {"from": "steam"}

    def test_the_stored_copy_is_tried_first(self):
        with patch.object(match_review.deadlock_api, "get_json", return_value={"from": "store"}) as get_json:
            self.assertEqual(match_review.fetch_metadata(7), {"from": "store"})
        get_json.assert_called_once_with("/v1/matches/7/metadata", {"disable_steam": "true"}, max_age=0)
        self.assertEqual(match_review.steam_fetches_left(), 3)

    def test_steam_only_when_not_stored_and_each_fetch_is_counted(self):
        with patch.object(match_review.deadlock_api, "get_json", side_effect=self.not_stored):
            self.assertEqual(match_review.fetch_metadata(7), {"from": "steam"})
        self.assertEqual(match_review.steam_fetches_left(), 2)

    def test_no_steam_fetch_once_this_hours_are_used_up(self):
        self.steam_fetches.extend([1000.0, 1100.0, 1200.0])
        with patch.object(match_review.time, "time", return_value=1300.0), \
             patch.object(match_review.deadlock_api, "get_json", side_effect=self.not_stored) as get_json:
            with self.assertRaises(match_review.MatchUnavailable):
                match_review.fetch_metadata(7)
        self.assertEqual(get_json.call_count, 1)  # only the stored copy was asked
        self.assertEqual(match_review.steam_fetches_left(now=1000.0 + 3600), 1)  # the oldest has expired

    def test_stored_copy_only(self):
        with patch.object(match_review, "load_saved", return_value=None), \
             patch.object(match_review.deadlock_api, "get_json", side_effect=self.not_stored) as get_json:
            with self.assertRaises(match_review.MatchUnavailable):
                match_review.get_summary(7, NAMES, allow_steam=False)
        self.assertEqual(get_json.call_count, 1)
        self.assertEqual(match_review.steam_fetches_left(), 3)


class StoryTests(unittest.TestCase):
    # team 0's lead at each snapshot, from a real 50-minute match: team 1 fell 33k behind, came back, then won
    LEAD = [[3, 466], [6, -1719], [9, -7472], [30, -32973], [35, 767], [40, -375], [45, -11875]]

    def test_told_from_your_side(self):
        me = {"team": 0, "won": False, "hero": "Haze"}
        review = {"networth_lead": self.LEAD, "winning_team": 1, "me": me, "places": {"damage": 1, "net_worth": 5},
                  "vs_usual": {"net_worth": -0.18, "damage": 0.04, "healing": None, "last_hits": 0.12}}
        self.assertEqual(match_review.story(review), [
            "You lost, 12k souls behind at the end.",
            "The enemy took the lead for good at minute 40. "
            "The biggest swing was 34k souls toward your team, between minutes 30 and 35.",
            "Against your usual Haze: souls 18% lower, last hits 12% higher.",  # damage +4% is ordinary
            "Most damage in the lobby.",
        ])

    def test_a_match_you_werent_in_follows_the_winners(self):
        lead = [[3, 1000], [6, 5000], [9, 40000]]
        self.assertEqual(match_review.story({"networth_lead": lead, "winning_team": 0}),
                         ["The winners finished 40k souls ahead.", "The winners led from start to finish."])


class SaveTests(unittest.TestCase):
    def test_saving_many_at_once_never_fails(self):
        # The Coach saves ~30 summaries at once; pruning used to remove a file another thread was sorting by
        import threading
        errors = []

        def save(n):
            try:
                match_review.save({"match_id": n, "players": []})
            except OSError as e:
                errors.append(e)
        with tempfile.TemporaryDirectory() as folder, patch.object(match_review, "CACHE_DIR", folder),                 patch.object(match_review, "MAX_SAVED_MATCHES", 5):
            threads = [threading.Thread(target=save, args=(n,)) for n in range(40)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(os.listdir(folder)), 5)


if __name__ == "__main__":
    unittest.main()
