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


if __name__ == "__main__":
    unittest.main()
