"""Tests for settings: saving must merge, so window settings and "me" never erase each other."""

import os
import tempfile
import unittest
from unittest.mock import patch

import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        path = os.path.join(tempfile.mkdtemp(), "settings.json")  # never touch the real settings file
        patcher = patch.object(settings, "SETTINGS_FILE", path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_saving_merges_with_existing_settings(self):
        settings.save_settings({"me": {"name": "Me", "account_id": 1}})
        settings.save_settings({"geometry": "800x600", "overlay": True})  # what the app saves on close
        self.assertEqual(settings.load_settings(),
                         {"me": {"name": "Me", "account_id": 1}, "geometry": "800x600", "overlay": True})

    def test_get_me(self):
        self.assertIsNone(settings.get_me())
        settings.save_settings({"me": {"name": "Me", "account_id": 1}})
        self.assertEqual(settings.get_me(), {"name": "Me", "account_id": 1})

    def test_preferences_start_at_their_defaults(self):
        self.assertEqual(settings.get_preferences(), settings.PREFERENCES)

    def test_changing_one_preference_keeps_the_rest(self):
        settings.save_settings({"me": {"name": "Me", "account_id": 1}})
        settings.set_preference("pop_up", True)
        settings.set_preference("show_badges", False)
        prefs = settings.get_preferences()
        self.assertEqual((prefs["pop_up"], prefs["show_badges"], prefs["sound"]), (True, False, True))
        self.assertEqual(settings.get_me()["account_id"], 1)

    def test_unreadable_file_means_defaults(self):
        with open(settings.SETTINGS_FILE, "w") as f:
            f.write("{not json")
        self.assertEqual(settings.load_settings(), {})


def lobby(*account_ids):
    return [{"status": "found", "account_id": a, "player": f"player{a}", "avatar_url": None} for a in account_ids] + \
           [{"status": "not found", "account_id": None, "player": "unreadname"}]


class IsThisYouTests(unittest.TestCase):
    def test_the_player_in_every_lobby_is_asked_about_and_friends_drop_out(self):
        update, asked = settings.update_me_guess, lambda g: sorted(c["account_id"] for c in settings.guessed_me(g))
        guess = update(None, lobby(1, 2, *range(10, 20)), 111203456)  # you (1), a friend (2) and strangers
        self.assertEqual(asked(guess), [])  # one lobby: too early to ask
        guess = update(guess, lobby(1, 2, *range(10, 19)), None)  # the same lobby read again, its ID unread
        self.assertEqual(guess["lobbies"], 1)
        guess = update(guess, lobby(1, 2, *range(20, 30)), 111203457)
        self.assertEqual(asked(guess), [1, 2])  # "Is one of these you?"
        guess = update(guess, lobby(1, *range(30, 41)), 111203458)  # the friend wasn't there
        self.assertEqual(asked(guess), [1])
        guess = update(guess, lobby(*range(50, 61)), 111203459)  # your name misread: nobody in common
        self.assertEqual((guess["lobbies"], asked(guess)), (1, []))  # the count starts again


if __name__ == "__main__":
    unittest.main()
