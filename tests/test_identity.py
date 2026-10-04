"""Tests for identity resolution and party detection (pure logic, no network)."""

import unittest

from identity import find_parties, pick_by_hero_history, resolve_lobby


def account(account_id, friends=(), hero_matches=0):
    return {"account_id": account_id, "friends": set(friends), "current_hero_matches": hero_matches}


class PickByHeroHistoryTests(unittest.TestCase):
    def test_prefers_most_matches_and_reports_runner_up(self):
        chosen, note = pick_by_hero_history([account(1, hero_matches=3), account(2, hero_matches=8)])
        self.assertEqual(chosen["account_id"], 2)
        self.assertIn("8 matches", note)
        self.assertIn("next best: 3", note)

    def test_no_history_keeps_api_order_and_says_it_is_a_guess(self):
        chosen, note = pick_by_hero_history([account(1), account(2)])
        self.assertEqual(chosen["account_id"], 1)
        self.assertIn("guess", note)


class ResolveLobbyTests(unittest.TestCase):
    def test_unique_name_is_settled(self):
        resolved = resolve_lobby({0: [account(10)]}, {0: "Solo"})
        self.assertEqual(resolved[0][0]["account_id"], 10)
        self.assertEqual(resolved[0][1], "unique name")

    def test_friend_link_beats_hero_history(self):
        # Mirrors a real lobby: the right "PlayerB" had 0 games on his hero, a stranger had 25
        candidates = {
            0: [account(1)],                                       # PlayerA: unique
            1: [account(20, hero_matches=25), account(21, friends={1})],  # PlayerB
        }
        resolved = resolve_lobby(candidates, {0: "PlayerA", 1: "PlayerB"})
        self.assertEqual(resolved[1][0]["account_id"], 21)
        self.assertEqual(resolved[1][1], "friends with PlayerA in this lobby")

    def test_links_can_be_listed_by_either_side(self):
        # Player 1's candidate has a private friend list; the settled player lists them instead
        candidates = {0: [account(1, friends={21})], 1: [account(20), account(21)]}
        resolved = resolve_lobby(candidates, {0: "A", 1: "B"})
        self.assertEqual(resolved[1][0]["account_id"], 21)

    def test_newly_settled_players_unlock_others(self):
        # A is unique; B is settled through A; C is only linked to B
        candidates = {
            0: [account(1)],
            1: [account(20), account(21, friends={1})],
            2: [account(30), account(31, friends={21})],
        }
        resolved = resolve_lobby(candidates, {0: "A", 1: "B", 2: "C"})
        self.assertEqual(resolved[2][0]["account_id"], 31)

    def test_tied_friend_links_fall_back_to_hero_history(self):
        candidates = {0: [account(1)], 1: [account(20, friends={1}), account(21, friends={1}, hero_matches=5)]}
        resolved = resolve_lobby(candidates, {0: "A", 1: "B"})
        self.assertEqual(resolved[1][0]["account_id"], 21)
        self.assertIn("matches on this hero", resolved[1][1])

    def test_links_to_unsettled_players_are_not_evidence(self):
        # Both players are ambiguous; a link between two guesses proves nothing
        candidates = {0: [account(1), account(2)], 1: [account(20, friends={1}), account(21, hero_matches=9)]}
        resolved = resolve_lobby(candidates, {0: "A", 1: "B"})
        self.assertEqual(resolved[1][0]["account_id"], 21)


class FindPartiesTests(unittest.TestCase):
    def resolved(self, *accounts):
        return {i: (a, "") for i, a in enumerate(accounts)}

    def test_three_friends_on_one_team_form_one_party(self):
        resolved = self.resolved(account(1, {2}), account(2, {3}), account(3), account(4))
        teams = {0: "enemy", 1: "enemy", 2: "enemy", 3: "enemy"}
        self.assertEqual(find_parties(resolved, teams), [[0, 1, 2]])

    def test_friends_on_opposite_teams_are_not_a_party(self):
        resolved = self.resolved(account(1, {2}), account(2))
        self.assertEqual(find_parties(resolved, {0: "friendly", 1: "enemy"}), [])

    def test_separate_parties_stay_separate(self):
        resolved = self.resolved(account(1, {2}), account(2), account(3, {4}), account(4))
        teams = {0: "friendly", 1: "friendly", 2: "friendly", 3: "friendly"}
        self.assertEqual(find_parties(resolved, teams), [[0, 1], [2, 3]])


if __name__ == "__main__":
    unittest.main()
