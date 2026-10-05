import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from version import is_newer  # noqa: E402


class IsNewerTest(unittest.TestCase):
    def test_compares_numbers_not_text(self):
        self.assertTrue(is_newer("v0.10.0", "0.9.0"))  # as text, "0.10" sorts before "0.9"
        self.assertTrue(is_newer("v0.2.1", "0.2.0"))
        self.assertTrue(is_newer("1.0.0", "0.2.0"))

    def test_same_or_older_is_not_an_update(self):
        self.assertFalse(is_newer("v0.2.0", "0.2.0"))
        self.assertFalse(is_newer("v0.1.0", "0.2.0"))

    def test_odd_tags_are_ignored(self):
        self.assertFalse(is_newer("v0.3.0-beta", "0.2.0"))
        self.assertFalse(is_newer("latest", "0.2.0"))


if __name__ == "__main__":
    unittest.main()
