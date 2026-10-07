"""Tests for the theme's pill colours (pure logic, no window)."""

import unittest

from ui.theme import BADGE_COLORS, MATCHUP_COLORS, PARTY_COLORS, text_color_for


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class PillContrastTest(unittest.TestCase):
    def test_every_pill_is_readable(self):
        # 4.5:1 is WCAG's minimum for small text; pills are 8-9 pt
        for fill in [*BADGE_COLORS.values(), *PARTY_COLORS, *MATCHUP_COLORS.values()]:
            with self.subTest(fill=fill):
                self.assertGreaterEqual(contrast(fill, text_color_for(fill)), 4.5)


if __name__ == "__main__":
    unittest.main()
