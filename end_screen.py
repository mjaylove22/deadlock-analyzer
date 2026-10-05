"""Spot the end-of-match screen (the final scoreboard with PLAY AGAIN) and read its match ID.

It's recognised by its column headings, "SOULS  K  D  A  PLYR DMG": fixed text on a dark background,
checked the same way as the PLAYERS tab (pattern correlation within a few pixels of where it should
be). Measured on a real end screen against 12 scoreboard screenshots and 4 other game screens:
the end screen scored 0.97-1.00 (also re-compressed or blurred), everything else 0.15 at most. A
check takes ~8 ms, and the watcher runs it every 2 s while the game is open.

The reference comes from a 1920x1080 end screen from June 2026, so a later game update may move
it: every detection is logged, which is how that would show up.
Screen reading only, like any screen recorder.
"""

from typing import Callable, Optional

from PIL import Image

import layout as layout_module
import paths
from scoreboard_detector import correlation, pixel_sums
from scoreboard_ocr import read_match_id

HEADER_BOX = (560, 108, 900, 126)        # the column headings, at 1920x1080
MATCH_ID_BOX = (1700, 47, 1910, 63)      # "MATCH 111203456", top right under the game mode
MIN_CORRELATION = 0.8                    # end screen 0.97+, other screens 0.15 at most; 1 px off: 0.78
SEARCH_RADIUS = 3
REFERENCE_PATH = paths.resource("assets", "end_screen_header.png")
# The top-right text is dim (brightness ~52 on ~5). In testing, a cutoff of 15 read one digit wrong
# with the right length, so two settings must agree; 25-35 all read it right.
MATCH_ID_ATTEMPTS = ((4, 30), (3, 30), (4, 25), (3, 35))

_reference: Optional[tuple] = None  # (size, pixel sums)


def _reference_data() -> tuple:
    global _reference
    if _reference is None:
        image = Image.open(REFERENCE_PATH).convert("L")
        _reference = (image.size, pixel_sums(image.tobytes()))
    return _reference


def search_box(layout: layout_module.Layout) -> tuple:
    left, top, right, bottom = layout.box(HEADER_BOX)
    return left - SEARCH_RADIUS, top - SEARCH_RADIUS, right + SEARCH_RADIUS, bottom + SEARCH_RADIUS


def header_found(area: Image.Image, scale: float = 1.0) -> bool:
    """Are the headings within SEARCH_RADIUS pixels of the middle of area (search_box's grab)?"""
    size, reference = _reference_data()
    width, height = round(size[0] * scale), round(size[1] * scale)
    gray = area.convert("L")
    for dy in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1):
        for dx in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1):
            x, y = SEARCH_RADIUS + dx, SEARCH_RADIUS + dy
            patch = gray.crop((x, y, x + width, y + height))
            if scale != 1:
                patch = patch.resize(size, Image.LANCZOS)
            if correlation(patch.tobytes(), reference) > MIN_CORRELATION:
                return True
    return False


def is_end_screen(grab: Callable[[tuple], Image.Image], layout: layout_module.Layout) -> bool:
    """grab(box) returns that part of the screen (see layout.locate)."""
    return header_found(grab(search_box(layout)), layout.scale)


def read_end_match_id(image: Image.Image, layout: layout_module.Layout) -> Optional[int]:
    return read_match_id(image, layout, MATCH_ID_BOX, MATCH_ID_ATTEMPTS, agree=2)
