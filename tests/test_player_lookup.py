"""Tests for lobby lookup. The Deadlock API is mocked, so these run offline.

Run from the project root:
    python -m unittest discover -s tests -v
"""

import unittest
from unittest.mock import patch

import player_lookup
from player_lookup import looks_like_misread, lookup_lobby, same_name, search_player

PARADOX = 10
GRAVES = 76
HERO_IDS = {"Paradox": PARADOX, "Graves": GRAVES, "Haze": 13}
HERO_NAMES = {v: k for k, v in HERO_IDS.items()}


def profile(account_id, name, friends=()):
    return {"account_id": account_id, "personaname": name, "profileurl": f"https://steam/{account_id}",
            "friends": [{"account_id": f} for f in friends]}


def stat(account_id, hero_id, matches, wins=0):
    """Shaped like a real hero-stats entry (only the fields this project reads)."""
    return {"account_id": account_id, "hero_id": hero_id, "matches_played": matches, "wins": wins,
            "kills": matches * 5, "deaths": matches * 4, "assists": matches * 8, "damage_per_min": 900.0}


RANK_TIERS = {0: {"name": "Obscurus", "color": "#333333"}, 7: {"name": "Emissary", "color": "#B47FEB"}}


RANKED_AT = 1789946847  # when each fake player's last ranked match started


def rank(account_id, tier, subrank):
    return {"account_id": account_id, "rank": tier, "subrank": subrank, "last_match": {"start_time": RANKED_AT}}


def record(player, hero, team="friendly"):
    return {"player": player, "hero": hero, "team": team}


def fake_search(profiles_by_name):
    return lambda name, limit=50: profiles_by_name.get(name, [])


class LookupLobbyTests(unittest.TestCase):
    def setUp(self):
        # Safety net: any API call a test forgot to mock fails loudly instead of using the network
        guard = patch.object(player_lookup.deadlock_api, "get_json",
                             side_effect=AssertionError("unit test tried to call the real API"))
        guard.start()
        self.addCleanup(guard.stop)

    def run_lookup(self, records, profiles_by_name, stats=(), live=(), me=None, live_profiles=(), mates=(),
                   met=None, notes=None):
        api = player_lookup.deadlock_api
        # Never the real notes.json or "met before" cache on this PC
        failure = met if isinstance(met, Exception) else None
        with patch.object(player_lookup.history, "met_before", return_value=met or {}, side_effect=failure), \
             patch.object(player_lookup.history, "load_notes", return_value=notes or {}), \
             patch.object(api, "search_steam_profiles", side_effect=fake_search(profiles_by_name)), \
             patch.object(api, "get_hero_stats", return_value=list(stats)) as get_stats, \
             patch.object(api, "fetch_rank_tiers", return_value=RANK_TIERS), \
             patch.object(api, "get_player_ranks", side_effect=lambda ids: [rank(a, 7, 2) for a in ids]), \
             patch.object(api, "get_active_matches", return_value=list(live)), \
             patch.object(api, "get_profiles", return_value=list(live_profiles)),              patch.object(player_lookup, "frequent_mates", return_value=list(mates)):
            results, parties = lookup_lobby(records, HERO_IDS, HERO_NAMES, me=me)
        return results, parties, get_stats

    def test_bot_named_after_its_hero_is_skipped_without_api_calls(self):
        with patch.object(player_lookup.deadlock_api, "search_steam_profiles") as search:
            results, _ = lookup_lobby([record("Haze", "Haze")], HERO_IDS, HERO_NAMES)
        self.assertEqual(results[0]["status"], "skipped")
        search.assert_not_called()

    def test_clearly_different_names_are_not_trusted(self):
        # "BrightFlame" is the real nearest stranger to "BrightFox": reject it
        results, _, _ = self.run_lookup([record("BrightFox", "Paradox")], {"BrightFox": [profile(1, "BrightFlame")]})
        self.assertEqual(results[0]["status"], "not found")
        self.assertIn("BrightFlame", results[0]["note"])

    def test_added_or_dropped_letters_are_not_treated_as_misreads(self):
        # Real cases: correctly read names that a looser rule turned into strangers
        for ocr, stranger in (("Kovas", "Kovmas"), ("Ravenl", "raven")):
            results, _, _ = self.run_lookup([record(ocr, "Paradox")], {ocr: [profile(1, stranger)]})
            self.assertEqual(results[0]["status"], "not found", ocr)
            self.assertEqual(results[0]["player"], ocr)

    def test_one_character_ocr_misread_is_corrected_and_flagged(self):
        # Real case: OCR read "Or. Night Owl" for "Dr. Night Owl"
        profiles = {"Or. Night Owl": [profile(1, "Dr. Night Owl")]}
        results, _, _ = self.run_lookup([record("Or. Night Owl", "Paradox")], profiles)
        r = results[0]
        self.assertEqual(r["status"], "found")
        self.assertEqual(r["player"], "Dr. Night Owl")
        self.assertEqual(r["corrected_from"], "Or. Night Owl")
        self.assertFalse(r["confident"])

    def test_a_name_the_scoreboard_cut_off_is_matched_by_its_start_and_flagged(self):
        # Real case (Oct 2026): a name too long for its row was drawn ending in "...", read with one dot, and
        # shown only as the "closest name". A short name ending in a dot isn't treated as cut off.
        cut, full = "Grey Mirage the Unstoppab.", "Grey Mirage the Unstoppable"
        profiles = {cut: [profile(9, "Grey Mirage"), profile(1, full)], "moondog.": [profile(2, "moondog the great")]}
        results, _, _ = self.run_lookup([record(cut, "Paradox"), record("moondog.", "Graves")], profiles)
        r = results[0]
        self.assertEqual((r["status"], r["account_id"], r["player"], r["corrected_from"]), ("found", 1, full, cut))
        self.assertFalse(r["confident"])  # NAME FIXED in warning colour: the full name is a guess
        self.assertEqual(results[1]["status"], "not found")

    def test_found_player_gets_top_heroes_sorted_by_matches(self):
        stats = [stat(2, PARADOX, 8, wins=4), stat(2, GRAVES, 20, wins=14)]
        results, _, _ = self.run_lookup([record("Grey Mirage", "Paradox")],
                                        {"Grey Mirage": [profile(2, "Grey Mirage")]}, stats)
        self.assertEqual(results[0]["status"], "found")
        self.assertEqual([h["hero"] for h in results[0]["top_heroes"]], ["Graves", "Paradox"])
        self.assertAlmostEqual(results[0]["top_heroes"][0]["win_rate"], 0.7)

    def test_whole_lobby_uses_one_stats_request(self):
        profiles = {"A": [profile(1, "A")], "B": [profile(2, "B"), profile(3, "B")]}
        _, _, get_stats = self.run_lookup([record("A", "Paradox"), record("B", "Graves")], profiles)
        get_stats.assert_called_once()
        self.assertEqual(sorted(get_stats.call_args.args[0]), [1, 2, 3])

    def test_friend_link_picks_account_and_party_is_reported(self):
        profiles = {
            "PlayerA": [profile(1, "PlayerA")],
            "PlayerB": [profile(20, "PlayerB"), profile(21, "PlayerB", friends={1})],
        }
        stats = [stat(20, PARADOX, 25)]  # the stranger has more games on the current hero
        results, parties, _ = self.run_lookup(
            [record("PlayerA", "Graves", "enemy"), record("PlayerB", "Paradox", "enemy")], profiles, stats)
        self.assertEqual(results[1]["account_id"], 21)
        self.assertEqual(results[1]["note"], "friends with PlayerA in this lobby")
        self.assertEqual(parties, [[0, 1]])

    def test_found_player_gets_rank_badges_and_confidence(self):
        stats = [stat(2, PARADOX, 25, wins=20), stat(2, GRAVES, 5)]
        results, _, _ = self.run_lookup([record("Solo", "Paradox")], {"Solo": [profile(2, "Solo")]}, stats)
        r = results[0]
        self.assertEqual(r["rank"], {"name": "Emissary 2", "color": "#B47FEB", "badge": 72, "as_of": RANKED_AT})  # as_of: for its hover text
        self.assertEqual(r["hero_stats"]["games"], 25)
        self.assertIn(("HIGH WR", "good"), r["badges"])
        self.assertTrue(r["confident"])

    def test_the_user_is_identified_from_settings_even_when_the_name_is_shared(self):
        api = player_lookup.deadlock_api
        profiles = {"Twin": [profile(1, "Twin"), profile(2, "Twin")]}   # strangers with the user's name
        stats = [stat(1, PARADOX, 50)]                                   # a stranger plays the hero more
        with patch.object(player_lookup, "frequent_mates", return_value=[]),                 patch.object(api, "get_profiles", return_value=[profile(2, "Twin")]) as get_profiles,              patch.object(api, "search_steam_profiles", side_effect=fake_search(profiles)),              patch.object(api, "get_hero_stats", return_value=stats),              patch.object(api, "fetch_rank_tiers", return_value=RANK_TIERS),              patch.object(api, "get_player_ranks", side_effect=lambda ids: [rank(a, 7, 2) for a in ids]):
            results, _ = lookup_lobby([record("Twin", "Paradox")], HERO_IDS, HERO_NAMES,
                                      me={"name": "Twin", "account_id": 2})
        get_profiles.assert_called_once_with([2])
        self.assertEqual(results[0]["account_id"], 2)
        self.assertTrue(results[0]["is_me"])
        self.assertTrue(results[0]["confident"])
        self.assertEqual(results[0]["note"], "you (from settings)")

    def test_close_hero_history_call_is_marked_unsure(self):
        profiles = {"Twin": [profile(1, "Twin"), profile(2, "Twin")]}
        stats = [stat(1, PARADOX, 8), stat(2, PARADOX, 5)]  # the real "8 vs 5" case
        results, _, _ = self.run_lookup([record("Twin", "Paradox")], profiles, stats)
        self.assertFalse(results[0]["confident"])

    def test_a_few_games_each_is_unsure_even_at_twice_as_many(self):
        # Found by the identity regression check: "2 matches on this hero (next best: 1)" was called sure,
        # and it was the wrong account of the 8 that share the author's name
        profiles = {"Twin": [profile(1, "Twin"), profile(2, "Twin")]}
        results, _, _ = self.run_lookup([record("Twin", "Paradox")], profiles, [stat(1, PARADOX, 2), stat(2, PARADOX, 1)])
        self.assertFalse(results[0]["confident"])
        results, _, _ = self.run_lookup([record("Twin", "Paradox")], profiles, [stat(1, PARADOX, 25), stat(2, PARADOX, 7)])
        self.assertTrue(results[0]["confident"])  # a real lead (the same lobby, the right account)

    def test_a_misread_found_by_searching_its_lookalike_spelling(self):
        # Real case: "plerix" read as "pierix". Searching "pierix" put the real account 54th of
        # its results, beyond what the app asks for; searching "plerix" puts it first.
        profiles = {"pierix": [profile(9, "Pier"), profile(8, "Virgin")], "plerix": [profile(1, "plerix")]}
        results, _, _ = self.run_lookup([record("pierix", "Paradox")], profiles)
        r = results[0]
        self.assertEqual((r["status"], r["account_id"], r["player"], r["corrected_from"]), ("found", 1, "plerix", "pierix"))
        self.assertFalse(r["confident"])  # the name was a guess

    def test_no_results_at_all_is_not_found_not_an_error(self):
        results, _, _ = self.run_lookup([record("zzqx", "Paradox")], {})
        self.assertEqual((results[0]["status"], results[0]["note"]), ("not found", "no similar names"))

    def test_a_live_match_gives_exact_accounts(self):
        # Two strangers share the name "Twin"; the live match list says which one is playing Paradox
        profiles = {"Twin": [profile(1, "Twin"), profile(2, "Twin")], "Solo": [profile(5, "Solo")]}
        live = [{"match_id": 77, "players": [{"account_id": 2, "hero_id": PARADOX}, {"account_id": 5, "hero_id": GRAVES}]}]
        results, _, _ = self.run_lookup([record("Twin", "Paradox"), record("Solo", "Graves")], profiles,
                                        live=live, live_profiles=[profile(2, "Twin")])
        self.assertEqual((results[0]["account_id"], results[0]["confident"]), (2, True))
        self.assertIn("live match", results[0]["note"])

    def test_a_live_match_with_other_heroes_is_someone_elses(self):
        profiles = {"Twin": [profile(1, "Twin"), profile(2, "Twin")], "Solo": [profile(5, "Solo")]}
        live = [{"match_id": 77, "players": [{"account_id": 2, "hero_id": 99}, {"account_id": 5, "hero_id": 98}]}]
        results, _, _ = self.run_lookup([record("Twin", "Paradox"), record("Solo", "Graves")], profiles,
                                        stats=[stat(1, PARADOX, 30)], live=live, live_profiles=[profile(2, "Twin")])
        self.assertEqual(results[0]["account_id"], 1)  # decided by hero history as before
        self.assertNotIn("live match", results[0]["note"])

    def test_heroes_missing_from_the_hero_list_dont_lower_the_bar(self):
        # Two of four players are on heroes the (stale) hero list doesn't have: a live match sharing only the
        # two known heroes is not this lobby. Before, "all known heroes but one" was just one hero here.
        profiles = {"Twin": [profile(1, "Twin"), profile(2, "Twin")], "Solo": [profile(5, "Solo")]}
        live = [{"match_id": 77, "players": [{"account_id": 2, "hero_id": PARADOX}, {"account_id": 5, "hero_id": GRAVES}]}]
        lobby = [record("Twin", "Paradox"), record("Solo", "Graves"), record("Newbie", "Brand New Hero"),
                 record("Other", "Another New Hero")]
        results, _, _ = self.run_lookup(lobby, profiles, stats=[stat(1, PARADOX, 30)], live=live, live_profiles=[profile(2, "Twin")])
        self.assertNotIn("live match", results[0]["note"])
        self.assertEqual(results[0]["account_id"], 1)

    ME = {"name": "Me", "account_id": 100}

    @staticmethod
    def mate(account_id, name, games):
        return {"account_id": account_id, "name": name, "profile_url": f"https://steam/{account_id}",
                "avatar_url": None, "games": games, "friends": [100]}

    def test_a_friend_with_a_full_width_name_is_found_among_the_people_you_play_with(self):
        # Real case (made-up name): the name search can't find full-width letters at all, even typed
        # exactly, but this friend has 133 matches with the user
        results, parties, _ = self.run_lookup([record("Me", "Haze"), record("m o o n d o g", "Paradox")], {},
                                              me=self.ME, mates=[self.mate(7, "ｍｏｏｎｄｏｇ", 133)])
        r = results[1]
        self.assertEqual((r["status"], r["account_id"], r["confident"]), ("found", 7, True))
        self.assertEqual(r["note"], "you've played 133 matches together")

    def test_your_friend_wins_over_strangers_with_the_same_name(self):
        profiles = {"Twin": [profile(1, "Twin"), profile(2, "Twin")]}
        results, _, _ = self.run_lookup([record("Twin", "Paradox")], profiles, stats=[stat(1, PARADOX, 50)],
                                        me=self.ME, mates=[self.mate(2, "Twin", 40)])
        self.assertEqual(results[0]["account_id"], 2)

    def test_a_misread_name_of_someone_you_play_with(self):
        results, _, _ = self.run_lookup([record("Or. Night Owl", "Paradox")], {}, me=self.ME,
                                        mates=[self.mate(3, "Dr. Night Owl", 12)])
        r = results[0]
        self.assertEqual((r["account_id"], r["player"], r["confident"]), (3, "Dr. Night Owl", False))

    def test_your_history_and_note_are_on_the_players_you_met_but_not_on_you(self):
        met = {"5": [3, 2, 1, 0, 111203456]}  # faced 3 times (you won 2), teamed once (lost)
        notes = {"5": {"text": "plays safe, ganks mid", "name": "Grey Mirage", "updated": 0}}
        results, _, _ = self.run_lookup([record("Me", "Haze"), record("Grey Mirage", "Paradox", "enemy")],
                                        {"Grey Mirage": [profile(5, "Grey Mirage")]}, me=self.ME, met=met, notes=notes,
                                        live_profiles=[profile(100, "Me")])
        me, enemy = results
        self.assertTrue(me["is_me"] and me["status"] == "found")
        self.assertEqual(enemy["history"], {"faced": 3, "won_against": 2, "teamed": 1, "won_with": 0,
                                            "last_match": 111203456})
        self.assertEqual(enemy["my_note"], "plays safe, ganks mid")
        self.assertEqual((me["history"], me["my_note"]), (None, ""))

    def test_the_lobby_still_loads_when_history_fails(self):
        results, _, _ = self.run_lookup([record("Me", "Haze"), record("Grey Mirage", "Paradox", "enemy")],
                                        {"Grey Mirage": [profile(5, "Grey Mirage")]}, me=self.ME,
                                        met=OSError("offline"), live_profiles=[profile(100, "Me")])
        self.assertEqual([r["status"] for r in results], ["found", "found"])
        self.assertIsNone(results[1]["history"])

    def test_network_error_is_reported_not_raised(self):
        with patch.object(player_lookup.deadlock_api, "search_steam_profiles", side_effect=OSError("timed out")):
            results, _ = lookup_lobby([record("Someone", "Paradox")], HERO_IDS, HERO_NAMES)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("timed out", results[0]["note"])



class SearchPlayerTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(player_lookup.deadlock_api, "get_json",
                             side_effect=AssertionError("unit test tried to call the real API"))
        guard.start()
        self.addCleanup(guard.stop)

    def search(self, name, found, stats=()):
        api = player_lookup.deadlock_api
        with patch.object(api, "search_steam_profiles", return_value=found),              patch.object(api, "get_hero_stats", return_value=list(stats)),              patch.object(api, "fetch_rank_tiers", return_value=RANK_TIERS),              patch.object(api, "get_player_ranks", side_effect=lambda ids: [rank(a, 7, 2) for a in ids]):
            return search_player(name, HERO_NAMES)

    def test_exact_names_only_when_there_are_any(self):
        found = [profile(1, "Twin"), profile(2, "Twins"), profile(3, "twin")]
        results = self.search("Twin", found, [stat(1, PARADOX, 10, wins=6), stat(1, GRAVES, 10, wins=4)])
        self.assertEqual([r["account_id"] for r in results], [1, 3])
        self.assertEqual(results[0]["totals"]["games"], 20)
        self.assertAlmostEqual(results[0]["totals"]["win_rate"], 0.5)
        self.assertEqual(results[0]["rank"]["name"], "Emissary 2")

    def test_falls_back_to_closest_names(self):
        results = self.search("Kovas", [profile(1, "Kovmas"), profile(2, "Kovan")])
        self.assertEqual([r["player"] for r in results], ["Kovmas", "Kovan"])
        self.assertEqual(results[0]["note"], "similar name")

    def test_nothing_found(self):
        self.assertEqual(self.search("zzz", []), [])


class LooksLikeMisreadTests(unittest.TestCase):
    def test_names_match_with_spaces_dropped_or_added(self):
        self.assertTrue(same_name("a frog sat ina pond", "A Frog Sat In A Pond"))
        self.assertFalse(same_name("Bob", "Rob"))

    def test_one_swapped_character(self):
        self.assertTrue(looks_like_misread("Or. Night Owl", "Dr. Night Owl"))

    def test_stylised_letters_match_plain_ones(self):
        self.assertTrue(same_name("ｍｏｏｎｄｏｇ", "m o o n d o g"))  # full-width letters, as OCR spaces them out
        self.assertTrue(same_name("Fizz\u200bPop", "Fizz Pop"))  # an invisible zero-width space

    def test_lookalike_spellings_most_likely_first(self):
        self.assertEqual(player_lookup.lookalike_names("pierix")[0], "plerix")
        self.assertIn("modern", player_lookup.lookalike_names("rnodern"))
        self.assertLessEqual(len(player_lookup.lookalike_names("lilililililil")), player_lookup.MAX_LOOKALIKES)

    def test_insertions_deletions_and_short_names_are_rejected(self):
        self.assertFalse(looks_like_misread("Kovas", "Kovmas"))
        self.assertFalse(looks_like_misread("Ravenl", "raven"))
        self.assertFalse(looks_like_misread("Rem", "Ram"))           # too short to guess safely
        self.assertFalse(looks_like_misread("BrightFox", "BrightBoy"))  # two characters different


if __name__ == "__main__":
    unittest.main()
