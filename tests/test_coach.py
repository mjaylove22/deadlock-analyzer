"""Tests for the Coach tab's facts (match_review.coach_facts) and analysis (coach.coach_report), on made-up data."""

import unittest

from coach import coach_report
from match_review import coach_facts

NAMES = {7: "Haze", 8: "Vyper"}


def path(slot, x_at, length=130):
    """A path on a 10,000-unit square (x from 0, y from -5,000): x_at(i) gives the x in units at second i; y is 0."""
    return {"player_slot": slot, "x_min": 0, "x_max": 10000, "y_min": -5000, "y_max": 5000,
            "x_pos": [x_at(i) // 100 for i in range(length)], "y_pos": [50] * length}


class CoachFactsTests(unittest.TestCase):
    def test_death_is_matched_to_the_path_and_dead_teammates_are_skipped(self):
        me = {"player_slot": 1, "team": 0, "hero_id": 7,
              "death_details": [{"game_time_s": 100, "death_pos": {"x": 1000, "y": 0}, "killer_player_slot": 3,
                                 "time_to_kill_s": 4.04, "death_duration_s": 10}],
              "stats": [{"time_stamp_s": 540, "creep_kills": 30, "possible_creeps": 40, "denies": 2, "net_worth": 7000,
                         "gold_player": 900, "gold_player_orbs": 100, "gold_lane_creep": 3000, "gold_death_loss": 450}]}
        mate = {"player_slot": 2, "team": 0, "hero_id": 8, "death_details": []}
        dead_mate = {"player_slot": 4, "team": 0, "hero_id": 8,
                     "death_details": [{"game_time_s": 95, "death_pos": {"x": 1000, "y": 0}, "death_duration_s": 20}]}
        enemy = {"player_slot": 3, "team": 1, "hero_id": 8, "death_details": []}
        info = {"players": [me, mate, dead_mate, enemy], "match_paths": {
            "interval_s": 1, "x_resolution": 100, "y_resolution": 100, "paths": [
                # The path clock runs 5 s ahead: you're at the death spot at sample 105, not 100
                path(1, lambda i: 1000 if i == 105 else 0),
                # Your teammate was beside the death spot at sample 100 but 4,000 units away at 105
                path(2, lambda i: 1000 if i < 105 else 5000),
                path(4, lambda i: 1000),  # right there, but dead: doesn't count
                path(3, lambda i: 1000)]}}
        facts = coach_facts(info, me, NAMES)
        self.assertEqual(facts["death_list"], [{"t": 100, "x": 1000, "y": 0, "fight_s": 4.0, "killer": "Vyper", "mate": 4000}])
        self.assertEqual(facts["lane"], {"minute": 9, "last_hits": 30, "possible": 40, "denies": 2, "net_worth": 7000})
        self.assertEqual(facts["sources"]["kills"], 1000)  # souls from heroes, picked up as orbs or not
        self.assertEqual(facts["souls_lost"], 450)

    def test_null_fields_dont_break_the_match(self):
        # Seen in the log: a null time_to_kill_s made the whole match review fail to load
        me = {"player_slot": 1, "team": 0, "hero_id": 7, "death_details": [
            {"game_time_s": 100, "death_pos": {"x": 1000, "y": 0}, "time_to_kill_s": None, "death_duration_s": None},
            {"game_time_s": 200, "death_pos": None}]}  # nowhere to put it: skipped
        facts = coach_facts({"players": [me]}, me, NAMES)
        self.assertEqual(facts["death_list"], [{"t": 100, "x": 1000, "y": 0, "fight_s": 0, "killer": None, "mate": None}])


def player(account_id, mates, killer="Haze"):
    """A player who died once per entry in mates (the nearest teammate's distance at each death)."""
    return {"account_id": account_id, "hero": "Haze", "deaths": len(mates), "net_worth": 30000,
            "death_list": [{"t": 1600, "x": 0, "y": 0, "fight_s": 10.0, "killer": killer, "mate": m} for m in mates],
            "lane": {"minute": 9, "last_hits": 30, "possible": 40, "denies": 1, "net_worth": 7000},
            "sources": {"kills": 20, "lane": 50, "jungle": 15, "objectives": 8, "other": 7}, "souls_lost": 500}


def matches(my_mates, games=10):
    """games matches in which you died once per entry in my_mates, Vyper killing you every time; everyone else
    dies beside a teammate, and Vyper is one of 6 enemies (so 1 in 6 of your deaths would be "fair")."""
    found = []
    for n in range(games):
        me = player(1, my_mates, killer="Vyper")
        me["team"] = 0
        others = [dict(player(n, [500] * 6), team=0 if n < 7 else 1, hero="Vyper" if n == 7 else "Haze") for n in range(2, 13)]
        found.append({"summary": {"match_id": 111203450 + n, "minutes": 30, "players": [me] + others}, "me": me, "rating": None})
    return found


class CoachReportTests(unittest.TestCase):
    def test_dying_alone_far_more_than_the_lobby_is_a_finding(self):
        report = coach_report(matches([5000, 5000, 5000, 5000, 500, 500]))
        alone = next(f for f in report["findings"] if f["title"] == "Dying away from your team")
        self.assertTrue(alone["text"].startswith("40 of your 60 deaths came with no teammate within 3,000 units"))
        self.assertEqual(alone["level"], "likely")  # 10 matches: never "consistent", however clear
        self.assertTrue(any(f["title"] == "Vyper kills you a lot" for f in report["findings"]))

    def test_a_finding_and_each_death_link_to_their_match(self):
        games = matches([5000, 5000, 5000, 500, 500, 500])
        for d in games[3]["me"]["death_list"]:
            d["mate"] = 5000  # all six deaths away from the team in this one
        report = coach_report(games)
        alone = next(f for f in report["findings"] if f["title"] == "Dying away from your team")
        self.assertEqual(alone["example"]["match_id"], 111203453)  # the match where it showed most
        self.assertEqual({d["match"]["match_id"] for d in report["deaths"]["points"]}, {111203450 + n for n in range(10)})

    def test_dying_alone_as_often_as_the_lobby_is_not(self):
        report = coach_report(matches([500] * 6))
        self.assertFalse(any(f["title"] == "Dying away from your team" for f in report["findings"]))

    def test_a_few_matches_are_never_a_finding(self):
        report = coach_report(matches([5000] * 6, games=3))
        self.assertEqual([f for f in report["findings"] if f["title"] == "Dying away from your team"], [])

    def test_hero_filter_with_no_matches_gives_nothing(self):
        self.assertIsNone(coach_report(matches([500]), hero="Vyper"))


if __name__ == "__main__":
    unittest.main()
