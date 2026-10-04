"""Tests for layout maths and the tab search (no screenshots needed)."""

import unittest

from PIL import Image

from layout import REFERENCE_LAYOUT, Layout, candidates, locate_in_image, normalized_crop
from scoreboard_detector import PLAYERS_TAB_BOX, reference_tab


class LayoutTests(unittest.TestCase):
    def test_1080p_has_exactly_one_layout(self):
        self.assertEqual(candidates(1920, 1080), [REFERENCE_LAYOUT])

    def test_16_9_screens_scale_without_offset(self):
        self.assertIn(Layout(round(1440 / 1080, 6), 0.0, 0.0), candidates(2560, 1440))

    def test_ultrawide_offers_right_edge_and_centred(self):
        found = candidates(2560, 1080)
        self.assertIn(Layout(1.0, 640.0, 0.0), found)   # UI on the right edge
        self.assertIn(Layout(1.0, 320.0, 0.0), found)   # UI in a centred 16:9 area

    def test_box_maps_reference_coordinates(self):
        self.assertEqual(Layout(2.0, 10, 20).box((100, 50, 200, 60)), (210, 120, 410, 140))

    def test_tab_is_found_a_couple_of_pixels_off(self):
        # Rounding at other sizes can put the tab a pixel or two from where it's predicted
        screen = Image.new("RGB", (1920, 1080), (12, 12, 14))
        screen.paste(reference_tab().convert("RGB"), (PLAYERS_TAB_BOX[0] + 2, PLAYERS_TAB_BOX[1] - 1))
        self.assertEqual(locate_in_image(screen), Layout(1.0, 2.0, -1.0))

    def test_no_tab_no_layout(self):
        self.assertIsNone(locate_in_image(Image.new("RGB", (1920, 1080), (12, 12, 14))))

    def test_normalized_crop_is_reference_sized(self):
        crop = normalized_crop(Image.new("RGB", (3840, 2160)), Layout(2.0), (100, 100, 400, 300))
        self.assertEqual(crop.size, (300, 200))


if __name__ == "__main__":
    unittest.main()
