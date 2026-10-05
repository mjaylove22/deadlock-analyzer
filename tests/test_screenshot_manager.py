"""Tests for old-screenshot cleanup, using a throwaway folder so real screenshots are never touched."""

import os
import tempfile
import time
import unittest

from screenshot_manager import delete_old_screenshots

DAY = 24 * 60 * 60


class DeleteOldScreenshotsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def make(self, name, age_days):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write("x")
        old = time.time() - age_days * DAY
        os.utime(path, (old, old))  # pretend the file was written age_days ago
        return path

    def test_deletes_only_captures_older_than_the_limit(self):
        old = self.make("screenshot_20260901_120000.png", age_days=10)
        recent = self.make("screenshot_20261003_120000.png", age_days=2)
        deleted = delete_old_screenshots(self.dir, max_age_days=7)
        self.assertEqual(deleted, [old])
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(recent))

    def test_keeps_screenshots_used_as_regression_tests(self):
        kept = self.make("screenshot_20260901_120000.png", age_days=30)
        self.make("screenshot_20260901_120000.expected.json", age_days=30)
        self.assertEqual(delete_old_screenshots(self.dir, max_age_days=7), [])
        self.assertTrue(os.path.exists(kept))

    def test_never_touches_other_files(self):
        other = [self.make(name, age_days=30) for name in
                 ("notes.txt", "screenshot_20260901_120000.expected.json", "my_screenshot.png")]
        self.assertEqual(delete_old_screenshots(self.dir, max_age_days=7), [])
        self.assertTrue(all(os.path.exists(p) for p in other))

    def test_end_screens_are_cleaned_up_but_never_taken_for_a_lobby(self):
        from unittest.mock import patch
        import screenshot_manager
        old = self.make("endscreen_20260901_120000.png", age_days=10)
        lobby = self.make("screenshot_20261003_120000.png", age_days=2)
        self.make("endscreen_20261004_120000.png", age_days=1)  # newer than the lobby screenshot
        self.assertEqual(delete_old_screenshots(self.dir, max_age_days=7), [old])
        with patch.object(screenshot_manager, "SCREENSHOT_DIR", self.dir):
            self.assertEqual(screenshot_manager.get_screenshot_path(), lobby)

    def test_keeps_three_days_by_default(self):
        old = self.make("screenshot_20260930_120000.png", age_days=3.5)
        recent = self.make("screenshot_20261002_120000.png", age_days=2.5)
        self.assertEqual(delete_old_screenshots(self.dir), [old])
        self.assertTrue(os.path.exists(recent))

    def test_missing_folder_is_fine(self):
        self.assertEqual(delete_old_screenshots(os.path.join(self.dir, "nope")), [])


if __name__ == "__main__":
    unittest.main()
