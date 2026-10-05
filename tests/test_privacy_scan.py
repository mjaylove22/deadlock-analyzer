import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import privacy_scan  # noqa: E402

FULL_WIDTH_MOONDOG = "".join(chr(0xFF41 + ord(c) - ord("a")) for c in "moondog")


class FindLeaksTest(unittest.TestCase):
    NAMES = {"moondog", "Star", "Grey Mirage"}
    IDS = {"123456789"}

    def leaks(self, *lines):
        return [path for path, _, _ in privacy_scan.find_leaks([(f"file{n}", line) for n, line in enumerate(lines)],
                                                               self.NAMES, self.IDS)]

    def test_finds_names_ids_and_disguised_spellings(self):
        self.assertEqual(self.leaks("played with moondog", "Grey  mirage", FULL_WIDTH_MOONDOG,
                                    "m o o n d o g", "account_id=123456789"),
                         ["file0", "file1", "file2", "file3", "file4"])

    def test_short_names_only_match_whole_words(self):
        self.assertEqual(self.leaks("Start started", "a bright star"), ["file1"])

    def test_ids_only_match_whole_numbers(self):
        self.assertEqual(self.leaks("id 91234567890", "id 123456789"), ["file1"])

    def test_one_report_per_line(self):
        self.assertEqual(len(self.leaks("moondog 123456789 Grey Mirage")), 1)


class AddedLinesTest(unittest.TestCase):
    def test_only_added_lines_with_their_file(self):
        diff = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old moondog\n+new line\n"
                "+++ b/y.md\n+second\n")
        self.assertEqual(list(privacy_scan.added_lines(diff)), [("x.py", "new line"), ("y.md", "second")])


class CollectTermsTest(unittest.TestCase):
    def test_collects_from_local_files_and_skips_heroes_and_short_terms(self):
        with tempfile.TemporaryDirectory() as root:
            def write(path, data):
                os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
                with open(os.path.join(root, path), "w", encoding="utf-8") as f:
                    f.write(data if isinstance(data, str) else json.dumps(data))
            write("settings.json", {"me": {"name": "Grey Mirage", "account_id": 1000}})
            write("cache/api/mates_1000.json", [{"name": "moondog", "account_id": 222333}])
            write("cache/api/heroes.json", [{"name": "Paradox"}])
            write("cache/matches/9.json", {"match_id": 987654321, "players": [{"account_id": 444555}]})
            write("screenshots/s.expected.json", [{"player": "Paradox"}, {"player": "Bo"}, {"player": "Velvet Fox"}])
            write("logs/app.log", "x - Lobby lookup: 10 found, not found: Quiet Owl (closest name: 'Owl'); Zed\n")
            write(".git/info/private-terms.txt", "# comment\nSecret Name\n555666777\n")
            names, ids = privacy_scan.collect_terms(root)
        self.assertEqual(names, {"Grey Mirage", "moondog", "Velvet Fox", "Quiet Owl", "Secret Name"})
        self.assertEqual(ids, {str(1000 + privacy_scan.STEAM64_BASE), "222333", "987654321", "444555", "555666777"})


if __name__ == "__main__":
    unittest.main()
