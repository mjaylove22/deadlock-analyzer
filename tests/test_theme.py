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


class FitToScreenTest(unittest.TestCase):
    def test_the_window_shrinks_exactly_to_fit_a_smaller_screen(self):
        from ui.theme import fit_factor
        self.assertEqual(fit_factor(1920, 1032, 1.0), 1.0)  # 1080p at 100% (the author's screens): as designed
        self.assertAlmostEqual(fit_factor(1920, 1032, 1.5) * 1000 * 1.5, 1032)  # 1080p laptop at 150%: exactly fits
        self.assertAlmostEqual(fit_factor(1366, 728, 1.0), 0.728)  # 1366x768 at 100%
        self.assertAlmostEqual(fit_factor(1080, 1872, 1.0), 0.9)  # 1080p monitor turned portrait: too narrow
        self.assertEqual(fit_factor(1920, 1152, 1.0), 1.0)  # 1920x1200

    def test_the_window_grows_on_bigger_screens(self):
        # A friend's 4K screen at 100%: the app filled a quarter of it, the text unreadably small
        from ui.theme import fit_factor
        self.assertEqual(fit_factor(3840, 2112, 1.0), 2.0)  # 4K at 100%: the share of the screen it has on 1080p
        self.assertEqual(fit_factor(3840, 2112, 1.25), 1.5)
        self.assertEqual(fit_factor(3840, 2112, 1.5), 1.25)  # 4K at 150%: like 1440p
        self.assertEqual(fit_factor(2560, 1392, 1.0), 1.25)  # 1440p
        self.assertEqual(fit_factor(3440, 1392, 1.0), 1.25)  # 1440p ultrawide: the height decides
        self.assertEqual(fit_factor(2560, 1032, 1.0), 1.0)  # 1080p ultrawide

    def test_a_chosen_size_wins_but_never_past_the_screen(self):
        # Auto left a friend's 4K screen hard to read: Settings -> Size picks it by hand
        from ui.theme import fit_factor
        self.assertEqual(fit_factor(3840, 2112, 1.5, "200%") * 1.5, 2.0)  # 4K at 150%: twice the 1080p size
        self.assertEqual(fit_factor(3840, 2112, 1.0, "150%"), 1.5)
        self.assertAlmostEqual(fit_factor(1920, 1032, 1.0, "200%"), 1.032)  # 1080p: only as big as fits
        self.assertEqual(fit_factor(3840, 2112, 1.0, "auto"), 2.0)

    def test_the_monitor_of_any_point_can_be_read(self):
        from ui.theme import monitor_at
        width, height, dpi = monitor_at(-100000, -100000)  # no screen there: the nearest one
        self.assertTrue(width > 0 and height > 0 and dpi >= 1)


if __name__ == "__main__":
    unittest.main()
