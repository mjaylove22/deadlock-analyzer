"""Tests for player-page and tier-list data (pure functions, no network)."""

import calendar
from datetime import datetime
import unittest
from unittest.mock import patch

import deadlock_api
import profiles
import report
from profiles import RANK_BANDS, describe_match, filter_matches, hero_rows, match_type, mode_breakdown, tier_rows, top_mates, when

NAMES = {1: "Haze", 2: "Rem", 3: "Newbie"}


def match(game_mode=1, won=True, hero_id=1, start=1000, match_mode=1):
    # A win is "the player's team is the winning team"; player_match_outcome is usually 0 (invalid)
    return {"match_id": start, "hero_id": hero_id, "game_mode": game_mode, "match_mode": match_mode, "player_team": 0,
            "match_result": 0 if won else 1, "player_match_outcome": 0, "player_kills": 5, "player_deaths": 2,
            "player_assists": 9, "net_worth": 30000, "match_duration_s": 1830, "start_time": start}


class ProfilesTests(unittest.TestCase):
    def test_win_comes_from_the_winning_team_not_the_outcome_field(self):
        self.assertTrue(describe_match(match(won=True), NAMES)["won"])
        self.assertFalse(describe_match(match(won=False), NAMES)["won"])

    def test_describe_match(self):
        m = describe_match(match(game_mode=4), NAMES)
        self.assertEqual((m["hero"], m["mode"], m["minutes"]), ("Haze", "Street Brawl", 30))

    def test_mode_breakdown_splits_ranked_unranked_and_street_brawl(self):
        matches = [match(1, True), match(1, False), match(1, True), match(4, True), match(1, True, match_mode=4)]
        rows = mode_breakdown(matches)
        self.assertEqual([(r["mode"], r["games"]) for r in rows], [("Unranked", 3), ("Street Brawl", 1), ("Ranked", 1)])
        self.assertAlmostEqual(rows[0]["win_rate"], 2 / 3)

    def test_match_type(self):
        self.assertEqual(match_type(match(1, match_mode=4)), "Ranked")
        self.assertEqual(match_type(match(1, match_mode=1)), "Unranked")
        self.assertEqual(match_type(match(4, match_mode=1)), "Street Brawl")  # always unranked
        self.assertEqual(describe_match(match(1, match_mode=4), NAMES)["type"], "Ranked")

    def test_hero_rows_skip_unplayed_and_sort_by_games(self):
        entries = [{"hero_id": 1, "matches_played": 5, "wins": 4, "kills": 10, "deaths": 5, "assists": 10},
                   {"hero_id": 2, "matches_played": 9, "wins": 3, "kills": 9, "deaths": 0, "assists": 0},
                   {"hero_id": 3, "matches_played": 0, "wins": 0}]
        rows = hero_rows(entries, NAMES)
        self.assertEqual([r["hero"] for r in rows], ["Rem", "Haze"])
        self.assertAlmostEqual(rows[1]["kda"], 4.0)
        self.assertEqual(rows[0]["kda"], 9.0)  # no deaths: divide by 1, not 0

    def test_tier_rows_win_rate_pick_rate_and_minimum_games(self):
        stats = [{"hero_id": 1, "matches": 6000, "losses": 2400}, {"hero_id": 2, "matches": 6000, "losses": 3600},
                 {"hero_id": 3, "matches": 100, "losses": 10}]  # too few games for the tier list
        rows = tier_rows(stats, NAMES)
        self.assertEqual([r["hero"] for r in rows], ["Haze", "Rem"])
        self.assertAlmostEqual(rows[0]["win_rate"], 0.6)
        # Pick rate = hero's games / number of matches, where matches = all hero-games / 12.
        # (These made-up numbers give a rate above 100%, which real data can't; it only checks the formula.)
        self.assertAlmostEqual(rows[0]["pick_rate"], 6000 / (12100 / 12))

    def test_when(self):
        now = 1_000_000
        self.assertEqual(when(now - 120, now), "2m ago")
        self.assertEqual(when(now - 3 * 3600, now), "3h ago")
        self.assertEqual(when(now - 2 * 86400, now), "2d ago")
        self.assertEqual(when(None, now), "")
        august = datetime(2026, 8, 25, 12).timestamp()
        self.assertEqual(when(august, datetime(2026, 10, 7, 12).timestamp()), "Aug 25")  # this year: no year
        self.assertEqual(when(datetime(2025, 10, 14, 12).timestamp(), august), "Oct 14 '25")


    def test_ban_share_is_each_heros_part_of_all_bans(self):
        stats = [{"hero_id": 1, "matches": 6000, "losses": 3000}, {"hero_id": 2, "matches": 6000, "losses": 3000}]
        rows = tier_rows(stats, NAMES, bans=[{"hero_id": 1, "bans": 300}, {"hero_id": 2, "bans": 100}])
        self.assertEqual({r["hero"]: r["ban_share"] for r in rows}, {"Haze": 0.75, "Rem": 0.25})
        self.assertIsNone(tier_rows(stats, NAMES)[0]["ban_share"])  # no ban data (e.g. Street Brawl)
        only_haze = tier_rows(stats, NAMES, bans=[{"hero_id": 1, "bans": 300}])
        self.assertIsNone(next(r for r in only_haze if r["hero"] == "Rem")["ban_share"])  # missing hero: unknown, not 0%

    def test_top_mates_most_games_first(self):
        mates = [{"mate_id": 1, "matches_played": 20, "wins": 10}, {"mate_id": 2, "matches_played": 700, "wins": 350},
                 {"mate_id": 3, "matches_played": 60, "wins": 45}]
        top = top_mates(mates, count=2)
        self.assertEqual([m["account_id"] for m in top], [2, 3])
        self.assertAlmostEqual(top[1]["win_rate"], 0.75)

    def test_rank_bands_cover_every_rank_once(self):
        tiers = [t for _, band in RANK_BANDS if band for t in range(band[0], band[1] + 1)]
        self.assertEqual(tiers, list(range(1, 12)))  # Initiate (1) to Eternus (11), no gaps or overlaps


class ProgressTests(unittest.TestCase):
    def test_only_changes_bigger_than_chance_are_called_out(self):
        # 40 Haze games, newest first: farming clearly up (~36k vs ~30k souls in 30.5 min), wins unchanged at 50%
        haze = [dict(match(won=n % 2 == 0, start=1000 - n), net_worth=(36000 if n < 20 else 30000) + 500 * (n % 3))
                for n in range(40)]
        other = [match(hero_id=2, start=500 - n) for n in range(10)]           # too few games on Rem to compare
        brawl = [dict(match(game_mode=4, start=2000 - n), net_worth=90000) for n in range(5)]  # not comparable
        p = profiles.progress(brawl + haze + other, NAMES)
        self.assertEqual(list(p["heroes"]), ["Haze"])
        souls, wins = p["overall"]["souls_per_min"], p["overall"]["win_rate"]
        self.assertTrue(souls["clear"])
        self.assertFalse(wins["clear"])
        summary, changes = report.progress_texts(p)
        self.assertIn("win rate 50% → 50%", summary)
        # averages 30,500 -> 36,475 souls a game (+20%); the Rem games are older, so overall = these 40 Haze games
        self.assertEqual(changes, [("Overall souls/min up 20%", True), ("Haze souls/min up 20%", True)])
        self.assertIsNone(profiles.progress(haze[:39], NAMES)["overall"])  # needs 40 games
        # The chart gets the same 40 normal matches, oldest first (the brawls and older Rem games left out)
        series = p["series"]
        self.assertEqual([s["match_id"] for s in series], [haze[n]["match_id"] for n in range(39, -1, -1)])
        self.assertEqual((series[-1]["hero"], series[-1]["won"], round(series[-1]["souls_per_min"])), ("Haze", True, 1180))


class RankProgressTests(unittest.TestCase):
    def test_changes_from_placement_peak_and_direction(self):
        day = 86400
        # (start, badge): two placements without a rank yet, the last placement gives 22, up to 23, back to 22
        ranked = [dict(match(match_mode=4, start=n * day), ranked_display_badge=b)
                  for n, b in enumerate([0, 0, 22, 23, 23, 22])]
        rp = profiles.rank_progress(ranked + [match(start=9 * day)])  # an unranked match is ignored
        self.assertEqual(rp["steps"], [(2 * day, 22), (3 * day, 23), (5 * day, 22)])
        self.assertEqual((rp["peak"], rp["games"]), (23, 4))
        shown = profiles.rank_progress_text(rp, lambda b: f"Seeker {b % 10}")
        self.assertIn("Placed Seeker 2", shown["text"])
        self.assertIn("→ Seeker 2 after 4 ranked matches · peak Seeker 3", shown["text"])
        self.assertEqual(shown["change"], 0)
        self.assertIsNone(profiles.rank_progress([match()]))


class PartyGamesTests(unittest.TestCase):
    def test_games_together_from_the_first_members_teammates(self):
        mates = [{"mate_id": 2, "matches_played": 741, "wins": 381}, {"mate_id": 3, "matches_played": 40, "wins": 20},
                 {"mate_id": 9, "matches_played": 900, "wins": 400}]  # not in this party
        with patch.object(deadlock_api, "get_mate_stats", return_value=mates):
            duo, trio, strangers = (profiles.party_games(ids) for ids in ([1, 2], [1, 3, 2], [1, 5]))
        self.assertEqual(report.party_text([0, 1], duo), "party of 2 (741 games together, 51% WR)")
        self.assertEqual(report.party_text([0, 1, 2], trio), "party of 3 (up to 741 games together)")
        self.assertIsNone(strangers)  # under 10 games together, so not in the API's list
        self.assertEqual(report.party_text([0, 1], strangers), "party of 2")


class TrendTests(unittest.TestCase):
    def test_a_clear_rise_is_a_change(self):
        series = [[4800, 10000]] * 4 + [[5000, 10000]] * 4 + [[5200, 10000]] * 4
        t = profiles.trend_change(series)
        self.assertAlmostEqual(t["change"], 0.04)
        self.assertFalse(t["steady"])

    def test_a_small_sample_wobble_is_steady(self):
        # +3 points on 500 games a week is within what chance gives
        series = [[240, 500]] * 4 + [[250, 500]] * 4 + [[255, 500]] * 4
        self.assertTrue(profiles.trend_change(series)["steady"])

    def test_a_real_but_tiny_change_is_steady(self):
        series = [[500000, 1000000]] * 8 + [[503000, 1000000]] * 4  # +0.3 points, real at this size
        self.assertTrue(profiles.trend_change(series)["steady"])

    def test_no_change_for_a_hero_without_games_back_then(self):
        series = [[0, 0]] * 8 + [[600, 1000]] * 4  # released recently
        self.assertIsNone(profiles.trend_change(series)["change"])

    def test_weeks_with_few_games_are_left_off_but_keep_their_place(self):
        weekly = {"weeks": list(range(12)), "heroes": {
            "1": [[0, 0]] * 10 + [[60, 120], [70, 120]],   # new hero: only the last two weeks
            "2": [[600, 1200]] * 12}}
        with patch.object(deadlock_api, "fetch_weekly_hero_stats", return_value=weekly):
            trends = profiles.hero_trends({1: "New", 2: "Old"})
        new = trends["heroes"]["New"]["weeks"]
        self.assertEqual([w["index"] for w in new], [10, 11])
        # Pick rate = the hero's games / matches that week, where matches = all heroes' games / 12
        self.assertAlmostEqual(new[1]["pick_rate"], 120 / ((120 + 1200) / 12))

    def test_weeks_are_whole_api_weeks_and_the_current_one_is_left_out(self):
        wednesday = calendar.timegm((2026, 10, 7, 15, 30, 0))
        sunday = calendar.timegm((2026, 10, 4, 0, 0, 0))  # the API's weeks start Sunday 00:00 UTC
        rows = [{"bucket": sunday - 7 * 86400, "hero_id": 5, "matches": 100, "losses": 40},
                {"bucket": sunday, "hero_id": 5, "matches": 9, "losses": 1}]  # this week: not over yet
        with patch.object(deadlock_api.time, "time", return_value=wednesday),                 patch.object(deadlock_api, "disk_cached", lambda name, build, max_age=0: build()),                 patch.object(deadlock_api, "get_json", return_value=rows) as get_json:
            weekly = deadlock_api.fetch_weekly_hero_stats(weeks=2)
        params = get_json.call_args.args[1]
        self.assertEqual(params["min_unix_timestamp"], sunday - 2 * 7 * 86400)
        self.assertEqual(params["max_unix_timestamp"], sunday - 1)
        self.assertEqual(weekly["weeks"], [sunday - 14 * 86400, sunday - 7 * 86400])
        self.assertEqual(weekly["heroes"]["5"], [[0, 0], [60, 100]])


class FilterTests(unittest.TestCase):
    MATCHES = [{"hero": "Haze", "type": "Ranked", "won": True}, {"hero": "Haze", "type": "Street Brawl", "won": False},
               {"hero": "Kelvin", "type": "Unranked", "won": False}, {"hero": "Kelvin", "type": "Ranked", "won": False}]

    def test_filters_combine(self):
        self.assertEqual(len(filter_matches(self.MATCHES)), 4)  # "All" keeps Street Brawl too
        self.assertEqual(filter_matches(self.MATCHES, "Ranked", "Kelvin", "Losses"), [self.MATCHES[3]])
        self.assertEqual(filter_matches(self.MATCHES, hero="Haze", result="Wins"), [self.MATCHES[0]])
        self.assertEqual(filter_matches(self.MATCHES, "Street Brawl", "Kelvin"), [])


if __name__ == "__main__":
    unittest.main()
