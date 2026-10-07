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


if __name__ == "__main__":
    unittest.main()
