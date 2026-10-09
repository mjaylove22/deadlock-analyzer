"""The diagnostic report must say what went wrong without the player names and paths that app.log holds."""

import time
import unittest

from diagnostics import problem_kinds, server_checks

LOG = """2026-10-08 20:13:38,880 - app - WARNING - Keyboard focus moved while reading the screenshot: 'Deadlock' -> 'Grey Mirage - Discord'
2026-10-08 20:14:00,000 - player_lookup - INFO - Lobby lookup: 11 found, not found: moondog
2026-10-08 21:00:00,000 - app - ERROR - Background task failed
Traceback (most recent call last):
  File "C:\\Users\\someone\\app.py", line 259, in runner
urllib.error.URLError: <urlopen error timed out>
2026-10-08 21:05:00,000 - app - WARNING - Keyboard focus moved during a capture: 'Deadlock' -> 'moondog'
2026-10-08 21:06:00,000 - scoreboard_ocr - WARNING - Couldn't confirm where the scoreboard is in a 1920x1080 image
2026-09-01 10:00:00,000 - app - ERROR - Something from long ago
"""


class DiagnosticsTests(unittest.TestCase):
    def test_problems_are_counted_by_kind_without_names(self):
        now = time.mktime(time.strptime("2026-10-09 00:00:00", "%Y-%m-%d %H:%M:%S"))
        kinds = problem_kinds(LOG, now)
        self.assertEqual(kinds, ["1× app WARNING: Keyboard focus moved while reading the screenshot",
                                 "1× app ERROR: Background task failed (URLError)",
                                 "1× app WARNING: Keyboard focus moved during a capture",
                                 "1× scoreboard_ocr WARNING: Couldn't confirm where the scoreboard is in a NxN image"])
        self.assertNotIn("moondog", "".join(kinds))  # INFO lines and quoted window titles are left out
        self.assertNotIn("Grey Mirage", "".join(kinds))

    def test_a_server_that_fails_is_reported_not_raised(self):
        def get(url):
            if "steampowered" in url:
                raise TimeoutError("timed out")
            return 200, 120
        self.assertEqual(server_checks(get), ["Stats API: OK (200, 120 ms)", "Steam (patch notes): FAILED (TimeoutError: timed out)",
                                              "GitHub (updates): OK (200, 120 ms)"])


if __name__ == "__main__":
    unittest.main()
