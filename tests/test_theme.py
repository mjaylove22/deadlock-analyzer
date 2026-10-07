"""Tests for the theme's pill colours (pure logic, no window)."""

import unittest
from unittest.mock import patch

from ui import images
from ui.theme import BADGE_COLORS, COLORS, LIGHT_COLORS, MATCHUP_COLORS, PARTY_COLORS, text_color_for


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


class ThemeContrastTest(unittest.TestCase):
    def test_light_theme_sets_every_colour(self):  # a name it misses would stay dark in light mode
        self.assertEqual(set(LIGHT_COLORS), set(COLORS))

    def test_text_colours_are_readable_on_cards_in_both_themes(self):
        for name, palette in (("dark", COLORS), ("light", dict(COLORS, **LIGHT_COLORS))):
            for key in ("text", "dim", "friendly", "enemy", "accent", "link", "win", "loss", "note"):
                with self.subTest(theme=name, colour=key):
                    self.assertGreaterEqual(contrast(palette[key], palette["card"]), 4.5)

    def test_hero_colours_are_adjusted_for_the_theme(self):
        for light, card in ((False, COLORS["card"]), (True, LIGHT_COLORS["card"])):
            with patch.object(images, "is_light", return_value=light):
                for hero_color in ("#f5e642", "#3a2a8c", "#7fd0ff"):  # bright yellow, dark purple, light blue
                    with self.subTest(light=light, colour=hero_color):
                        self.assertGreaterEqual(contrast(images.readable(hero_color), card), 4.5)


if __name__ == "__main__":
    unittest.main()
