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

import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

import pytesseract
from PIL import Image, ImageChops

import layout as layout_module
import paths
from scoreboard_detector import correlation, pixel_sums
from scoreboard_ocr import match_hero, read_match_id

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


# --- the scoreboard on the end screen: everyone's souls, K/D/A, damage, objective damage and healing.
# It's on screen the moment the match ends, while the API takes minutes to hours to have the match.
# Measured on a real 1920x1080 end screen: each team is a table of 6 rows, 56 px apart.
TABLE_BOX = (200, 80, 1100, 920)          # everything read below sits inside this
TEAM_FIRST_ROW = (134, 562)               # top of each team's first row
ROW_PITCH = 56
ROWS_PER_TEAM = 6
NAME_COLUMN = (262, 470)                  # player name, with the hero's name under it
HERO_LINE = (25, 43)                      # the hero's name: this far below the row's top
# Checked against the same match's API data: souls, K/D/A, player damage and OBJ DMG (= the API's
# boss damage) agree. HEALING doesn't (5 of 12 players far apart, e.g. 5.3k on screen, 249 in the
# API), so it's kept under its own name and never graded against the API's healing numbers.
NUMBER_COLUMNS = [("net_worth", 560, 625), ("kills", 662, 698), ("deaths", 704, 738), ("assists", 742, 780),
                  ("damage", 812, 882), ("boss_damage", 900, 970), ("screen_healing", 988, 1058)]
DURATION_BOX = (1680, 106, 1760, 128)     # "44:02", right of the column headings
BANNER_BOXES = ((200, 82, 760, 124), (200, 512, 760, 554))  # team name, plus "Victorious" for the winners
# Rows are orange or blue and some numbers sit on coloured stars (the lobby's best), while all text
# is white or grey. Each pixel's darkest colour channel keeps the text (88+ in every case measured,
# even red numbers on blue stars) and turns every background dark (15-45), so one cutoff fits all.
TEXT_CUTOFF = 75
# The thin 1s get dropped now and then ("61k" read as "6k"). Best single setting: 80 of 84 numbers;
# the most common reading of these three: 84 of 84.
NUMBER_SETTINGS = ((3, 75), (3, 90), (4, 75))  # (upscale, cutoff), like the match ID settings
NUMBER_PATTERN = re.compile(r"(\d+(?:\.\d)?)(k?)")
# Your own row is highlighted: its background was 33 and 52 on two real end screens, where other rows
# are 14-27. On 52 a lobby-best star (~86) sits above the cutoff and reads as digits ("32k" as "232k").
ROW_BACKGROUND = 25


def even_rows(table: Image.Image) -> Image.Image:
    """Darken any row whose background is brighter than an ordinary row's down to it, so the
    highlighted row reads like the others. Rows are judged by their number columns only."""
    left, right = NUMBER_COLUMNS[0][1] - TABLE_BOX[0], NUMBER_COLUMNS[-1][2] - TABLE_BOX[0]
    out = table.copy()
    for top in TEAM_FIRST_ROW:
        for row in range(ROWS_PER_TEAM):
            y = top + row * ROW_PITCH - TABLE_BOX[1]
            band = table.crop((0, y, table.width, y + ROW_PITCH))
            excess = statistics.median(band.crop((left, 4, right, ROW_PITCH - 8)).get_flattened_data()) - ROW_BACKGROUND
            if excess > 0:
                out.paste(band.point(lambda v: max(0, v - excess)), (0, y))
    return out


def darkest_channel(image: Image.Image) -> Image.Image:
    r, g, b = image.convert("RGB").split()
    return ImageChops.darker(ImageChops.darker(r, g), b)


def _binary(gray: Image.Image, scale: int, cutoff: int) -> Image.Image:
    big = gray.resize((gray.width * scale, gray.height * scale), Image.LANCZOS)
    return big.point(lambda v: 0 if v > cutoff else 255)


def parse_number(text: str) -> Optional[float]:
    """The end screen's number format: "409", "5.0k" (5,000), "58k" (58,000). None if it isn't one."""
    found = NUMBER_PATTERN.fullmatch(text or "")
    if not found:
        return None
    return float(found.group(1)) * (1000 if found.group(2) else 1)


def _column_readings(table: Image.Image, top: int, left: int, right: int, scale: int, cutoff: int) -> Dict[int, str]:
    """{row: text} for one number column of one team (one OCR run; words placed in rows by height)."""
    strip = table.crop((left - TABLE_BOX[0], top - TABLE_BOX[1], right - TABLE_BOX[0],
                        top + ROWS_PER_TEAM * ROW_PITCH - TABLE_BOX[1]))
    data = pytesseract.image_to_data(_binary(strip, scale, cutoff),
                                     config="--psm 6 -c tessedit_char_whitelist=0123456789.k",
                                     output_type=pytesseract.Output.DICT)
    rows: Dict[int, str] = {}
    for i, word in enumerate(data["text"]):
        if word.strip():
            row = int((data["top"][i] + data["height"][i] / 2) / scale // ROW_PITCH)
            rows[row] = rows.get(row, "") + word.strip()
    return rows


def _line(gray: Image.Image, scale: int = 3, cutoff: int = TEXT_CUTOFF, config: str = "--psm 7") -> str:
    return pytesseract.image_to_string(_binary(gray, scale, cutoff), config=config).strip()


def most_common(readings: List[Optional[str]]) -> Optional[str]:
    """The reading most settings agree on; a tie goes to the longest (1s get dropped, not added)."""
    readings = [r for r in readings if r]
    if not readings:
        return None
    return max(readings, key=lambda r: (readings.count(r), len(r)))


def read_scoreboard(image: Image.Image, layout: layout_module.Layout, hero_names: List[str]) -> Optional[Dict[str, Any]]:
    """Everyone's numbers from the end screen, shaped like a match summary (worker thread, ~2 s):
    {"minutes", "mode", "winning_team", "players": [{"name", "hero", "team", "won", "kills", ...}]}.
    Teams are 0 (upper table) and 1 (lower). None if no hero could be read."""
    table = darkest_channel(layout_module.normalized_crop(image, layout, TABLE_BOX))
    numbers = even_rows(table)
    full = layout_module.normalized_crop(image, layout, (0, 0, 1920, 1080))
    heroes_longest_first = sorted(hero_names, key=len, reverse=True)

    def region(box):
        return table.crop((box[0] - TABLE_BOX[0], box[1] - TABLE_BOX[1], box[2] - TABLE_BOX[0], box[3] - TABLE_BOX[1]))

    jobs = {}
    with ThreadPoolExecutor(max_workers=4) as pool:  # each OCR run is its own tesseract process
        for team, top in enumerate(TEAM_FIRST_ROW):
            for key, left, right in NUMBER_COLUMNS:
                for setting in NUMBER_SETTINGS:
                    jobs[(team, key, setting)] = pool.submit(_column_readings, numbers, top, left, right, *setting)
            for row in range(ROWS_PER_TEAM):
                y = top + row * ROW_PITCH
                jobs[(team, "hero", row)] = pool.submit(_line, region((NAME_COLUMN[0], y + HERO_LINE[0], NAME_COLUMN[1], y + HERO_LINE[1])))
                jobs[(team, "name", row)] = pool.submit(_line, region((NAME_COLUMN[0], y + 4, NAME_COLUMN[1], y + HERO_LINE[0])), 3, 150)
            jobs[(team, "banner")] = pool.submit(_line, region(BANNER_BOXES[team]), 3, 150)
        jobs["duration"] = pool.submit(_line, darkest_channel(full.crop(DURATION_BOX)), 3, TEXT_CUTOFF,
                                       "--psm 7 -c tessedit_char_whitelist=0123456789:")
        result = {key: job.result() for key, job in jobs.items()}

    winners = [team for team in (0, 1) if "victor" in result[(team, "banner")].lower()]
    duration = re.fullmatch(r"(\d{1,3}):(\d{2})", result["duration"])
    players = []
    for team in (0, 1):
        for row in range(ROWS_PER_TEAM):
            hero = match_hero(result[(team, "hero", row)], heroes_longest_first)
            if not hero:
                continue  # an empty row (fewer players), or unreadable
            player = {"name": result[(team, "name", row)], "hero": hero, "team": team, "account_id": None,
                      "won": winners == [team]}
            for key, _, _ in NUMBER_COLUMNS:
                value = parse_number(most_common([result[(team, key, s)].get(row) for s in NUMBER_SETTINGS]))
                if value is not None:
                    player[key] = value
            players.append(player)
    if not players:
        return None
    return {
        "minutes": int(duration.group(1)) + int(duration.group(2)) / 60 if duration else None,
        "mode": "Normal" if len(players) > 8 else "Street Brawl",
        "winning_team": winners[0] if len(winners) == 1 else None,
        "team_badges": [0, 0], "players": players, "source": "screen",
    }
