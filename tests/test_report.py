"""Tests for the shared report lines used by the terminal and the app window."""

import unittest

from report import badge_labels, build_report, hero_stats_text, team_summary


def result(player, hero, team, status="found", note="unique name", url=None, top=(), stats=None,
           badges=(), confident=True, rank=None):
    return {"player": player, "hero": hero, "team": team, "status": status, "note": note,
            "profile_url": url, "top_heroes": list(top), "hero_stats": stats, "badges": list(badges),
            "confident": confident, "rank": rank}


class BuildReportTests(unittest.TestCase):
    def test_empty_results_say_so(self):
        self.assertEqual(build_report([], []), [("No players found in the screenshot.", "note")])

    def test_teams_parties_and_player_details_in_order(self):
        results = [
            result("Me", "Lash", "friendly", note="unique name", url="https://steam/1",
                   top=[{"hero": "Viscous", "matches": 120, "win_rate": 0.55}]),
            result("A", "Haze", "enemy"),
            result("B", "Rem", "enemy"),
        ]
        lines = build_report(results, parties=[[1, 2]])
        styles = [style for _, style in lines]
        texts = [text for text, _ in lines]

        self.assertTrue(texts[0].startswith("YOUR TEAM"))
        self.assertIn(("Party of 2: A + B", "party"), lines)
        self.assertIn(("    https://steam/1", "link"), lines)
        self.assertIn("Most played: Viscous 120 (55%)", " ".join(t for t, _ in lines))
        # The party line belongs to the enemy section, right after its title
        enemy_title = next(n for n, t in enumerate(texts) if t.startswith("ENEMY TEAM"))
        self.assertEqual(styles[enemy_title + 1], "party")

    def test_player_without_account_has_no_link(self):
        lines = build_report([result("Bot", "Haze", "enemy", status="skipped", note="likely a bot")], [])
        self.assertNotIn("link", [s for _, s in lines])
        self.assertIn(("    Bot", "hero"), lines)


class WordingTests(unittest.TestCase):
    def test_hero_stats_line(self):
        r = result("A", "Lash", "enemy", stats={"games": 1, "win_rate": 1.0, "kda": 2.7, "damage_per_min": 1234.4})
        self.assertEqual(hero_stats_text(r), "1 game · 100% WR · 2.7 KDA · 1,234 dmg/min")

    def test_search_result_describes_all_games(self):
        r = result("A", "", "search")
        r["totals"] = {"games": 1234, "win_rate": 0.5, "recent": 61}
        self.assertEqual(hero_stats_text(r), "1,234 games · 50% WR overall · 61 in the last 30 days")

    def test_no_games_on_hero(self):
        self.assertEqual(hero_stats_text(result("A", "Lash", "enemy")), "No recorded games on Lash")

    def test_identity_badges(self):
        unsure = result("A", "Lash", "enemy", note="7 accounts share this name; ...", confident=False)
        friends = result("B", "Lash", "enemy", note="friends with C in this lobby")
        self.assertIn(("ID UNSURE", "warn"), badge_labels(unsure))
        self.assertIn(("ID VIA FRIENDS", "info"), badge_labels(friends))
        self.assertEqual(badge_labels(result("D", "Lash", "enemy")), [])
        fixed = result("E", "Lash", "enemy", confident=False)
        fixed["corrected_from"] = "Or. E"
        self.assertEqual(badge_labels(fixed), [("NAME FIXED", "info"), ("ID UNSURE", "warn")])



class TeamSummaryTests(unittest.TestCase):
    def test_counts_notable_badges_and_best_rank(self):
        team = [
            result("A", "Haze", "enemy", badges=[("ONE-TRICK", "strong")], rank={"name": "Oracle 4", "badge": 84}),
            result("B", "Rem", "enemy", badges=[("FIRST GAME ON HERO", "warn")], rank={"name": "Emissary 1", "badge": 71}),
            result("C", "Ivy", "enemy", badges=[("NEW ON HERO", "warn")], rank={"name": "Unranked", "badge": 0}),
        ]
        self.assertEqual(team_summary(team, [[0, 1]]),
                         "3 players · party of 2 · 1 one-trick · 2 new on hero · best rank Oracle 4")

    def test_quiet_team(self):
        self.assertEqual(team_summary([result("A", "Haze", "enemy")], []), "1 player")


if __name__ == "__main__":
    unittest.main()
