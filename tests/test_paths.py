"""Tests for paths.py: shipped files vs. files the app writes, from source and installed."""

import importlib
import os
import sys
import unittest
from unittest.mock import patch

import paths

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join("C:\\", "Users", "someone", "AppData", "Local", "Programs", "Deadlock Analyzer")


class PathsTest(unittest.TestCase):
    def tearDown(self):
        importlib.reload(paths)  # back to the real values for the other tests

    def test_from_source_everything_is_in_the_project_folder(self):
        self.assertFalse(paths.INSTALLED)
        self.assertEqual(paths.resource("assets", "icon.ico"), os.path.join(PROJECT, "assets", "icon.ico"))
        self.assertEqual(paths.data("settings.json"), os.path.join(PROJECT, "settings.json"))

    def test_installed_shipped_files_come_from_the_bundle_and_data_sits_next_to_the_exe(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "_MEIPASS", os.path.join(INSTALL, "_internal"), create=True), \
                patch.object(sys, "executable", os.path.join(INSTALL, "Deadlock Analyzer.exe")):
            importlib.reload(paths)
            self.assertTrue(paths.INSTALLED)
            self.assertEqual(paths.resource("tesseract", "tesseract.exe"),
                             os.path.join(INSTALL, "_internal", "tesseract", "tesseract.exe"))
            self.assertEqual(paths.data("settings.json"), os.path.join(INSTALL, "settings.json"))


if __name__ == "__main__":
    unittest.main()
