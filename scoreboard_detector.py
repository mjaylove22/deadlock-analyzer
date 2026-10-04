"""Detect when the scoreboard (Esc menu, PLAYERS tab) is on screen, from a tiny screen grab.

The selected PLAYERS tab is drawn solid, so the game behind it never shows through. Two checks
must both pass:
- Colour: mean pixel difference from the reference image below 15 (0-255 scale). Real scoreboards
  scored 0-1.6 on four very different scenes. On its own this isn't enough: a plain block of the
  tab's colour, with no text at all, scored 14.3.
- Pattern: normalised correlation with the reference above 0.9. This compares where the light and
  dark pixels are (the word "PLAYERS"), so flat areas score 0. Real scoreboards scored 1.00; the
  unselected tab 0.51, pills with other text 0.10-0.22.
Screen reading only, like any screen recorder.
"""

import math
import os
from typing import Optional

import mss
from PIL import Image, ImageChops, ImageStat

# Where the PLAYERS tab sits on a 1920x1080 screen (left, top, right, bottom)
PLAYERS_TAB_BOX = (1705, 108, 1890, 128)
MATCH_THRESHOLD = 15
MIN_CORRELATION = 0.9
REFERENCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "players_tab.png")

_reference: Optional[Image.Image] = None


def reference_tab() -> Image.Image:
    global _reference
    if _reference is None:
        _reference = Image.open(REFERENCE_PATH).convert("L")
    return _reference


def tab_difference(tab: Image.Image) -> float:
    """Mean pixel difference between a grab of the tab area and the reference (0 = identical)."""
    return ImageStat.Stat(ImageChops.difference(tab.convert("L"), reference_tab())).mean[0]


def tab_correlation(tab: Image.Image) -> float:
    """Normalised cross-correlation with the reference: 1.0 = same pattern, ~0 = unrelated or flat."""
    a, b = list(tab.convert("L").tobytes()), list(reference_tab().tobytes())
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    covariance = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    spread = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
    return covariance / spread if spread else 0.0


def is_scoreboard_open(tab: Image.Image) -> bool:
    return tab_difference(tab) < MATCH_THRESHOLD and tab_correlation(tab) > MIN_CORRELATION


def grab_tab(sct: mss.base.MSSBase) -> Image.Image:
    """Grab just the tab area of the screen (a few milliseconds). mss objects aren't shareable
    between threads, so the caller creates one per thread."""
    left, top, right, bottom = PLAYERS_TAB_BOX
    shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
    return Image.frombytes("RGB", shot.size, shot.rgb)
