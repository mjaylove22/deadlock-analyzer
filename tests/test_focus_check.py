"""The log warning for "a capture pulled you out of the game" (app.check_focus_kept), without a window."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402

GAME_BOX = (0, 0, 1920, 1080)


def check(at_capture, focused, game_box, stage="while showing the result", last=True):
    """Run check_focus_kept on a stand-in app; returns (warnings logged, what's left of focus_at_capture)."""
    fake = SimpleNamespace(focus_at_capture=at_capture)
    with mock.patch.object(app.game_window, "focused_window_title", return_value=focused), \
            mock.patch.object(app.game_window, "find_window", return_value=game_box), \
            mock.patch.object(app.game_window, "window_under_mouse", return_value="Deadlock Analyzer"), \
            mock.patch.object(app.game_window, "focused_program", return_value="pythonw.exe"), \
            mock.patch.object(app.logger, "warning") as warning:
        app.AnalyzerApp.check_focus_kept(fake, stage, last)
    return [c.args[0] for c in warning.call_args_list], fake.focus_at_capture


class FocusCheckTest(unittest.TestCase):
    def test_nothing_logged_when_the_game_keeps_focus(self):
        self.assertEqual(check(("Deadlock", True), "Deadlock", GAME_BOX), ([], None))

    def test_focus_moving_to_the_app_is_logged_with_its_stage_program_and_the_pointer(self):
        # The pointer over the app means a click there probably moved focus, not the app itself; the program
        # names a window whose title doesn't ("Launching...", Oct 2026)
        warnings, _ = check(("Deadlock", True), "Deadlock Analyzer", GAME_BOX, "while reading the screenshot")
        self.assertEqual(warnings, ["Keyboard focus moved while reading the screenshot: 'Deadlock' -> 'Deadlock Analyzer'"
                                    " [pythonw.exe]; mouse pointer over 'Deadlock Analyzer'"])

    def test_the_focused_program_and_the_window_under_the_pointer_can_be_read(self):
        # real Windows calls, no window needed
        self.assertIsInstance(app.game_window.window_under_mouse(), str)
        self.assertTrue(app.game_window.focused_program().endswith(".exe") or app.game_window.focused_program() == "")

    def test_a_minimised_game_is_logged_even_if_the_title_matches(self):
        warnings, _ = check(("Deadlock", True), "Deadlock", None)
        self.assertIn("the game window is minimised", warnings[0])

    def test_no_game_at_capture_time_is_not_called_minimised(self):
        # Ctrl+Shift+D on the desktop: the game was never there, so its absence means nothing
        self.assertEqual(check(("Desktop", False), "Desktop", None), ([], None))

    def test_first_check_keeps_watching_until_the_result_is_shown(self):
        self.assertEqual(check(("Deadlock", True), "Deadlock", GAME_BOX, last=False), ([], ("Deadlock", True)))

    def test_warns_once_per_capture(self):
        _, left = check(("Deadlock", True), "Deadlock Analyzer", GAME_BOX, last=False)
        self.assertIsNone(left)  # the second check then has nothing to compare with
        self.assertEqual(check(left, "Deadlock Analyzer", GAME_BOX), ([], None))


if __name__ == "__main__":
    unittest.main()
