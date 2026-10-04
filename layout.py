"""Where the scoreboard is on screens other than 1920x1080.

All the measurements in this project (crop box, row grid, the PLAYERS tab) were taken on a
1920x1080 screen. Rather than hard-code guesses about how Deadlock's UI moves at other sizes,
a few plausible layouts are tried and each is confirmed by the PLAYERS-tab detector:
  - the UI scales with the screen's height, or with its width
  - anchored to the right edge, or inside a centred 16:9 area (common on ultrawide screens)
  - at the top, or vertically centred (letterboxed)
The one that matches is remembered per screen size. Screenshots are then scaled back to the
1920x1080 size before reading, so every tuned number keeps working unchanged.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from PIL import Image

from scoreboard_detector import PLAYERS_TAB_BOX, find_tab
from settings import load_settings, save_settings

REFERENCE_SIZE = (1920, 1080)
SEARCH_RADIUS = 3  # pixels around each predicted tab position (rounding at other sizes)

Box = Tuple[int, int, int, int]


@dataclass(frozen=True)
class Layout:
    """Maps a 1920x1080 coordinate to the screen: screen = offset + reference * scale."""
    scale: float
    offset_x: float = 0.0
    offset_y: float = 0.0

    def box(self, reference_box: Box) -> Box:
        left, top, right, bottom = reference_box
        return (round(self.offset_x + left * self.scale), round(self.offset_y + top * self.scale),
                round(self.offset_x + right * self.scale), round(self.offset_y + bottom * self.scale))

    def shifted(self, dx: int, dy: int) -> "Layout":
        return Layout(self.scale, self.offset_x + dx, self.offset_y + dy)

    def to_list(self) -> list:
        return [self.scale, self.offset_x, self.offset_y]


REFERENCE_LAYOUT = Layout(1.0)


def candidates(width: int, height: int) -> List[Layout]:
    """Plausible layouts for a screen size, most likely first. At 1920x1080 they're all the same."""
    ref_w, ref_h = REFERENCE_SIZE
    found = []
    for scale in (height / ref_h, width / ref_w):
        ui_w, ui_h = ref_w * scale, ref_h * scale
        for offset_x in (width - ui_w, (width - ui_w) / 2):          # right edge, or centred
            for offset_y in (0.0, (height - ui_h) / 2):                # top, or centred
                layout = Layout(round(scale, 6), round(offset_x, 2), round(offset_y, 2))
                if layout not in found:
                    found.append(layout)
    return found


def tab_search_box(layout: Layout) -> Box:
    """The predicted tab position, plus SEARCH_RADIUS pixels of margin."""
    left, top, right, bottom = layout.box(PLAYERS_TAB_BOX)
    return left - SEARCH_RADIUS, top - SEARCH_RADIUS, right + SEARCH_RADIUS, bottom + SEARCH_RADIUS


def locate(grab: Callable[[Box], Image.Image], width: int, height: int,
           remembered: Optional[Layout] = None) -> Optional[Layout]:
    """The layout whose PLAYERS tab is showing, trying the remembered one first.
    grab(box) returns that part of the screen (or of a screenshot), in its coordinates."""
    to_try = ([remembered] if remembered else []) + [c for c in candidates(width, height) if c != remembered]
    for layout in to_try:
        box = tab_search_box(layout)
        if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
            continue  # this layout would put the tab off the screen
        found = check(grab, layout)
        if found:
            return found
    return None


def check(grab: Callable[[Box], Image.Image], layout: Layout) -> Optional[Layout]:
    """Is the PLAYERS tab showing at this layout (within a few pixels)? Returns the exact layout."""
    offset = find_tab(grab(tab_search_box(layout)), layout.scale, SEARCH_RADIUS)
    return layout.shifted(*offset) if offset is not None else None


def locate_in_image(image: Image.Image, remembered: Optional[Layout] = None) -> Optional[Layout]:
    return locate(image.crop, image.width, image.height, remembered)


def normalized_crop(image: Image.Image, layout: Layout, reference_box: Box) -> Image.Image:
    """Part of a screenshot, scaled back to how it looks at 1920x1080."""
    crop = image.crop(layout.box(reference_box))
    if layout.scale == 1:
        return crop
    left, top, right, bottom = reference_box
    return crop.resize((right - left, bottom - top), Image.LANCZOS)


def remembered(width: int, height: int) -> Optional[Layout]:
    """The layout that matched last time on a screen (or game window) of this size."""
    saved = load_settings().get("layouts", {}).get(f"{width}x{height}")
    return Layout(*saved) if saved else None


def remember(width: int, height: int, layout: Layout) -> None:
    layouts = load_settings().get("layouts", {})
    if layouts.get(f"{width}x{height}") != layout.to_list():
        save_settings({"layouts": dict(layouts, **{f"{width}x{height}": layout.to_list()})})
