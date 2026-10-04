"""Detect when the scoreboard (Esc menu, PLAYERS tab) is on screen, from a tiny screen grab.

The selected PLAYERS tab is drawn solid, so the game behind it never shows through. Two checks
must both pass:
- Colour: mean pixel difference from the reference image below 15 (0-255 scale). Real scoreboards
  scored 0-1.6 on four very different scenes. On its own this isn't enough: a plain block of the
  tab's colour, with no text at all, scored 14.3.
- Pattern: normalised correlation with the reference above 0.9. This compares where the light and
  dark pixels are (the word "PLAYERS"), so flat areas score 0. Real scoreboards scored 1.00; the
  unselected tab 0.51, pills with other text 0.10-0.22.
Position matters: shifted by even 1 pixel, the real tab fails (correlation 0.73), so callers that
only know roughly where the tab is (other resolutions) use find_tab() to search a few pixels around.
Screen reading only, like any screen recorder.
"""

import math
import operator
import os
from typing import Optional, Tuple

import mss
from PIL import Image, ImageChops, ImageStat

# Where the PLAYERS tab sits on a 1920x1080 screen (left, top, right, bottom)
PLAYERS_TAB_BOX = (1705, 108, 1890, 128)
MATCH_THRESHOLD = 15
MIN_CORRELATION = 0.9
REFERENCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "players_tab.png")

_reference: Optional[Image.Image] = None
_reference_sums: Optional[tuple] = None  # (pixel bytes, sum, n * variance), computed once


def reference_tab() -> Image.Image:
    global _reference
    if _reference is None:
        _reference = Image.open(REFERENCE_PATH).convert("L")
    return _reference


def _reference_stats() -> tuple:
    global _reference_sums
    if _reference_sums is None:
        pixels = reference_tab().tobytes()
        total = sum(pixels)
        _reference_sums = (pixels, total, sum(map(operator.mul, pixels, pixels)) - total * total / len(pixels))
    return _reference_sums


def tab_difference(tab: Image.Image) -> float:
    """Mean pixel difference between a grab of the tab area and the reference (0 = identical)."""
    return ImageStat.Stat(ImageChops.difference(tab.convert("L"), reference_tab())).mean[0]


def tab_correlation(tab: Image.Image) -> float:
    """Normalised cross-correlation with the reference: 1.0 = same pattern, ~0 = unrelated or flat.

    Exact integer sums; sum(map(operator.mul, ...)) keeps the per-pixel work inside Python's C
    built-ins, and the reference's sums are computed once: 0.14 ms per check instead of 0.7 ms."""
    ref, sum_ref, var_ref = _reference_stats()
    pixels = tab.convert("L").resize(reference_tab().size).tobytes() if tab.size != reference_tab().size \
        else tab.convert("L").tobytes()
    n = len(pixels)
    total = sum(pixels)
    variance = sum(map(operator.mul, pixels, pixels)) - total * total / n
    covariance = sum(map(operator.mul, pixels, ref)) - total * sum_ref / n
    return covariance / math.sqrt(variance * var_ref) if variance > 0 and var_ref > 0 else 0.0


def is_scoreboard_open(tab: Image.Image) -> bool:
    return tab_difference(tab) < MATCH_THRESHOLD and tab_correlation(tab) > MIN_CORRELATION


def find_tab(area: Image.Image, scale: float, radius: int = 3) -> Optional[Tuple[int, int]]:
    """Look for the tab near the middle of area, which should be the expected tab position plus
    radius pixels of margin on every side, at the given screen scale (1.0 = 1920x1080).
    Returns the (x, y) offset from the expected position where it matched, or None."""
    width = round((PLAYERS_TAB_BOX[2] - PLAYERS_TAB_BOX[0]) * scale)
    height = round((PLAYERS_TAB_BOX[3] - PLAYERS_TAB_BOX[1]) * scale)
    size = reference_tab().size
    best = None
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            tab = area.crop((radius + dx, radius + dy, radius + dx + width, radius + dy + height))
            if scale != 1:
                tab = tab.resize(size, Image.LANCZOS)  # compare at the reference size
            if tab_difference(tab) >= MATCH_THRESHOLD:
                continue  # the cheap check first
            score = tab_correlation(tab)
            if score > MIN_CORRELATION and (best is None or score > best[0]):
                best = (score, (dx, dy))
    return best[1] if best else None


def grab_tab(sct: mss.base.MSSBase) -> Image.Image:
    """Grab just the tab area of the screen (a few milliseconds). mss objects aren't shareable
    between threads, so the caller creates one per thread."""
    left, top, right, bottom = PLAYERS_TAB_BOX
    shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
    return Image.frombytes("RGB", shot.size, shot.rgb)
