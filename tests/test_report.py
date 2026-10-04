"""Tests for the shared report lines used by the terminal and the app window."""

import unittest

from report import build_report


def result(player, hero, team, status="found", note="", url=None, top=()):
    return {"player": player, "hero": hero, "team": team, "status": status, "note": note,
            "profile_url": url, "top_heroes": list(top)}


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

        self.assertEqual(texts[0], "YOUR TEAM")
        self.assertIn(("Party of 2: A + B", "party"), lines)
        self.assertIn(("    https://steam/1", "link"), lines)
        self.assertIn("Viscous", next(t for t, s in lines if s == "hero"))
        # The party line belongs to the enemy section, right after its title
        enemy_title = texts.index("ENEMY TEAM")
        self.assertEqual(styles[enemy_title + 1], "party")

    def test_player_without_account_has_no_link_or_heroes(self):
        lines = build_report([result("Bot", "Haze", "enemy", status="skipped", note="likely a bot")], [])
        self.assertNotIn("link", [s for _, s in lines])
        self.assertNotIn("hero", [s for _, s in lines])


if __name__ == "__main__":
    unittest.main()
