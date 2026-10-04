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

    def test_unreadable_file_means_defaults(self):
        with open(settings.SETTINGS_FILE, "w") as f:
            f.write("{not json")
        self.assertEqual(settings.load_settings(), {})


if __name__ == "__main__":
    unittest.main()
