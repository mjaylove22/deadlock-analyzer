"""Tests for your history with other players: "met before" from the API (mocked) and local notes
(in a throwaway folder, so the real notes.json is never touched)."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import history
from report import history_labels, history_text

ME = {"name": "Me", "account_id": 100}


def fake_api(responses):
    """get_json stand-in: answers by (endpoint kind, game mode), and records what was asked."""
    asked = []

    def get_json(path, params=None, max_age=None):
        kind = "enemy" if path.endswith("/enemy-stats") else "mate"
        asked.append((path, params["game_mode"], max_age))
        return responses.get((kind, params["game_mode"]), [])
    return get_json, asked


class MetBeforeTests(unittest.TestCase):
    def met(self, responses):
        get_json, asked = fake_api(responses)
        with patch.object(history.deadlock_api, "get_json", side_effect=get_json), \
             patch.object(history.deadlock_api, "disk_cached", side_effect=lambda name, build, max_age: build()):
            return history.met_before(ME), asked

    def test_both_modes_and_both_sides_are_added_up(self):
        met, asked = self.met({
            ("enemy", "normal"): [{"enemy_id": 5, "matches_played": 2, "wins": 1, "matches": [111203400, 111203456]}],
            ("enemy", "street_brawl"): [{"enemy_id": 5, "matches_played": 1, "wins": 1, "matches": [111203300]}],
            ("mate", "normal"): [{"mate_id": 5, "matches_played": 1, "wins": 0, "matches": [111203100]},
                                 {"mate_id": 6, "matches_played": 4, "wins": 3, "matches": [111203200]}],
        })
        self.assertEqual(met["5"], [3, 2, 1, 0, 111203456])  # faced 3 (won 2), teamed 1 (won 0), latest match
        self.assertEqual(met["6"], [0, 0, 4, 3, 111203200])
        # Four requests (two kinds x two modes), none kept in memory: they're large and stored slimmed on disk
        self.assertEqual(sorted((p.rsplit("/", 1)[1], m) for p, m, _ in asked),
                         [("enemy-stats", "normal"), ("enemy-stats", "street_brawl"),
                          ("mate-stats", "normal"), ("mate-stats", "street_brawl")])
        self.assertTrue(all(age == 0 and "/100/" in p for p, _, age in asked))

    def test_record_with_one_player(self):
        met = {"5": [3, 2, 0, 0, 111203456]}
        self.assertEqual(history.record_with(5, met), {"faced": 3, "won_against": 2, "teamed": 0, "won_with": 0,
                                                       "last_match": 111203456})
        self.assertIsNone(history.record_with(6, met))
        self.assertIsNone(history.record_with(None, met))


class NotesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.file = os.path.join(folder, "notes.json")
        patcher = patch.object(history, "NOTES_FILE", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_file_means_no_notes(self):
        self.assertEqual(history.load_notes(), {})
        self.assertEqual(history.get_note(5), "")

    def test_save_read_and_delete(self):
        history.save_note(5, "Grey Mirage", "plays safe,\n  ganks mid ")
        self.assertEqual(history.get_note(5), "plays safe, ganks mid")  # one line: it's shown on a card
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["5"]["name"], "Grey Mirage")
        history.save_note(5, "Grey Mirage", "   ")
        self.assertEqual(history.load_notes(), {})

    def test_long_notes_are_cut(self):
        history.save_note(5, "Grey Mirage", "x" * 500)
        self.assertEqual(len(history.get_note(5)), history.MAX_NOTE_LENGTH)

    def test_other_players_notes_are_kept(self):
        history.save_note(5, "Grey Mirage", "first")
        history.save_note(6, "moondog", "second")
        self.assertEqual({k: v["text"] for k, v in history.load_notes().items()}, {"5": "first", "6": "second"})


class WordingTests(unittest.TestCase):
    def test_pills_put_your_wins_first(self):
        record = {"faced": 3, "won_against": 2, "teamed": 4, "won_with": 1, "last_match": 1}
        self.assertEqual(history_labels(record), ["FACED 3× · 2-1", "ALLY 4× · 1-3"])
        self.assertEqual(history_labels(dict(record, teamed=0)), ["FACED 3× · 2-1"])
        self.assertEqual(history_labels(None), [])

    def test_sentence(self):
        self.assertEqual(history_text({"faced": 1, "won_against": 0, "teamed": 2, "won_with": 2, "last_match": 1}),
                         "You've faced them once (you won 0) and played with them twice (won 2)")
        self.assertIn("haven't", history_text(None))


if __name__ == "__main__":
    unittest.main()
