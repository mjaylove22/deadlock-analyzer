"""Tests for one-click updates (updater.py) with fake downloads: nothing goes over the network."""

import hashlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import updater

INSTALLER = b"MZ pretend installer" * 1000
RELEASE = {"tag": "v9.9.9", "url": "https://github.com/x/y/releases/download/v9.9.9/DeadlockAnalyzer-Setup.exe",
           "sha256": hashlib.sha256(INSTALLER).hexdigest()}


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, final_url: str):
        super().__init__(body)
        self.final_url, self.headers = final_url, {"Content-Length": str(len(body))}

    def geturl(self):
        return self.final_url


def serve(body=INSTALLER, final_url="https://objects.githubusercontent.com/release-asset"):
    return patch.object(updater.urllib.request, "urlopen", lambda request, timeout: FakeResponse(body, final_url))


class UpdaterTests(unittest.TestCase):
    def test_a_matching_download_is_kept(self):
        with tempfile.TemporaryDirectory() as folder, serve():
            path = updater.download(RELEASE, folder=folder)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), INSTALLER)

    def test_a_download_that_doesnt_match_is_deleted_and_refused(self):
        with tempfile.TemporaryDirectory() as folder, serve(body=INSTALLER + b"tampered"):
            with self.assertRaises(ValueError):
                updater.download(RELEASE, folder=folder)
            self.assertEqual(os.listdir(folder), [])  # nothing left that could be run

    def test_a_download_redirected_away_from_github_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, serve(final_url="https://evil.example.com/setup.exe"):
            with self.assertRaises(ValueError):
                updater.download(RELEASE, folder=folder)

    def test_release_without_a_checksum_offers_only_the_download_page(self):
        data = {"tag_name": "v9.9.9", "assets": [{"name": updater.SETUP_NAME, "browser_download_url": RELEASE["url"]}]}
        self.assertEqual(updater.release_info(data), {"tag": "v9.9.9", "url": None, "sha256": None})
        data["assets"][0]["digest"] = "sha256:" + RELEASE["sha256"].upper()
        self.assertEqual(updater.release_info(data), RELEASE)


class UpdateCheckTests(unittest.TestCase):
    def test_a_release_out_while_the_app_is_open_is_found_within_the_hour(self):
        # A friend never saw Update now: the app looked once at start-up and kept that answer for 6 hours
        import app
        from types import SimpleNamespace
        queued = []
        fake = SimpleNamespace(update_tag=None, events=SimpleNamespace(put=queued.append))
        answers = iter([{"tag": "v0.0.1"}, {"tag": "v9.9.9"}, {"tag": "v9.9.9"}])
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 3:
                raise StopIteration  # end the endless loop after three checks
            fake.update_tag = "v9.9.9" if queued else None  # as show_update does on the main thread

        with patch.object(app.deadlock_api, "disk_cached", side_effect=lambda *a, **k: next(answers)), \
                patch.object(app.time, "sleep", side_effect=sleep):
            with self.assertRaises(StopIteration):
                app.AnalyzerApp._check_for_update(fake)
        self.assertEqual(sleeps, [3600] * 3)
        self.assertEqual(len(queued), 1)  # shown once, at the second hourly check, not again at the third


if __name__ == "__main__":
    unittest.main()
