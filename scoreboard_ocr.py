"""Read the in-game scoreboard from a screenshot: which player is on which hero and team.

Screenshot OCR only - nothing here touches the game process or game files.

Usage (debug view of the raw OCR lines and parsed rows):
    python scoreboard_ocr.py [path/to/screenshot.png]
"""

import logging
import sys
from typing import Dict, List, Tuple

import pytesseract
from PIL import Image

import deadlock_api

logger = logging.getLogger(__name__)

# Scoreboard geometry, measured on a 1920x1080 screenshot of the Esc menu's PLAYERS tab (6v6).
SCREEN_SIZE = (1920, 1080)

# Player list crop (left, top, right, bottom).
# - Left starts just past the hero portraits, which OCR otherwise reads as junk like "sy" or "@".
# - Right must stay inside the panel (which ends ~x=1892): including the black strip past it breaks OCR.
PLAYER_LIST_BOX = (1560, 110, 1875, 940)

# The ENEMY TEAM header sits in the gap at y~545 on screen, which is y~435 inside the crop.
TEAM_SPLIT_Y = 435

# OCR preprocessing. The panel is semi-transparent, so its brightness shifts with whatever is
# behind it; left to its own automatic black/white conversion, Tesseract read nothing on a
# slightly brighter, red-tinted screenshot. Measured brightness: panel ~55-63, text ~159.
# A fixed cutoff between them, after upscaling the small text, read 12/12 on every test
# screenshot for any cutoff from 90 to 130.
OCR_SCALE = 2
TEXT_THRESHOLD = 110

# Used only when the API is unreachable. Verified against the API on 2026-10-03.
FALLBACK_HERO_NAMES = [
    "Abrams", "Apollo", "Bebop", "Billy", "Calico", "Celeste", "Drifter",
    "Dynamo", "Graves", "Grey Talon", "Haze", "Holliday", "Infernus",
    "Ivy", "Kelvin", "Lady Geist", "Lash", "McGinnis", "Mina", "Mirage",
    "Mo & Krill", "Paige", "Paradox", "Pocket", "Rat King", "Rem", "Seven",
    "Shiv", "Silver", "Sinclair", "The Doorman", "Venator", "Victor",
    "Vindicta", "Viscous", "Vyper", "Warden", "Wraith", "Yamato",
]


def load_hero_names() -> List[str]:
    """Current playable hero names from the API, falling back to the hardcoded list."""
    try:
        return [hero["name"] for hero in deadlock_api.fetch_heroes()]
    except Exception as e:
        logger.warning(f"Could not load heroes from the API ({e}); using fallback list")
        return FALLBACK_HERO_NAMES


def prepare_for_ocr(crop: Image.Image) -> Image.Image:
    """Upscale, then turn light text into black-on-white with a fixed brightness cutoff."""
    big = crop.resize((crop.width * OCR_SCALE, crop.height * OCR_SCALE), Image.LANCZOS)
    return big.convert("L").point(lambda v: 0 if v > TEXT_THRESHOLD else 255)


def read_team_lines(image: Image.Image) -> Dict[str, List[str]]:
    """OCR the player list once and split its text lines by team, using each line's position."""
    prepared = prepare_for_ocr(image.crop(PLAYER_LIST_BOX))
    # --psm 6 treats the crop as one uniform block of text
    data = pytesseract.image_to_data(prepared, config="--psm 6", output_type=pytesseract.Output.DICT)

    # Tesseract returns individual words; rebuild lines and remember how far down each one is
    lines = {}  # (block, paragraph, line) -> {"top": y, "words": [...]}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in lines:
            lines[key] = {"top": data["top"][i], "words": []}
        lines[key]["words"].append(word)

    team_lines = {"friendly": [], "enemy": []}
    for line in lines.values():
        # Positions come from the upscaled image, so scale back to crop coordinates
        team = "friendly" if line["top"] / OCR_SCALE < TEAM_SPLIT_Y else "enemy"
        team_lines[team].append(" ".join(line["words"]))
    return team_lines


def parse_player_rows(lines: List[str], hero_names: List[str]) -> List[Tuple[str, str]]:
    """Pair each player's Steam name with their hero.

    Each scoreboard row is two lines:
        <Steam name>
        <Hero> Level -1
    so a line containing a hero name and "Level" is paired with the line above it.
    Returns (player, hero) tuples in screen order.
    """
    # Check longer names first so a short name can never match inside a longer one
    heroes_longest_first = sorted(hero_names, key=len, reverse=True)
    rows = []
    previous_line = None  # Candidate Steam name: the last meaningful line seen

    for line in lines:
        line = line.strip()

        # Skip blank lines and pure noise (no letters at all, e.g. "=" or ",")
        if not any(ch.isalpha() for ch in line):
            continue

        # Team headers are never player names
        if any(header in line.upper() for header in ("MY TEAM", "ENEMY TEAM", "FRIENDS")):
            previous_line = None
            continue

        if "level" in line.lower():
            hero = next((h for h in heroes_longest_first if h.lower() in line.lower()), None)
            if hero and previous_line:
                rows.append((previous_line, hero))
            # A hero line is never a Steam name for the next row
            previous_line = None
        else:
            previous_line = line

    return rows


def read_scoreboard(file_path: str, hero_names: List[str]) -> List[Dict[str, str]]:
    """Return one {"player", "hero", "team"} record per player found in the screenshot."""
    with Image.open(file_path) as image:
        if image.size != SCREEN_SIZE:
            logger.warning(f"Screenshot is {image.size}, but the crop was measured on {SCREEN_SIZE}; results may be wrong")
        team_lines = read_team_lines(image)

    records = []
    for team, lines in team_lines.items():
        logger.debug(f"OCR lines ({team}): {lines}")
        for player, hero in parse_player_rows(lines, hero_names):
            records.append({"player": player, "hero": hero, "team": team})
    return records


def main():
    from screenshot_manager import get_screenshot_path
    from utils.logger import setup_logger
    setup_logger()

    file_path = sys.argv[1] if len(sys.argv) > 1 else get_screenshot_path()
    if not file_path:
        print("No screenshot found.")
        return

    print(f"Reading {file_path}")
    with Image.open(file_path) as image:
        team_lines = read_team_lines(image)
    hero_names = load_hero_names()

    for team, lines in team_lines.items():
        print(f"\n=== {team.upper()} TEAM ===")
        print("Raw OCR lines:")
        for i, line in enumerate(lines, start=1):
            print(f"  {i:2d}. {line!r}")
        print("Parsed rows:")
        for player, hero in parse_player_rows(lines, hero_names):
            print(f"  {player} -> {hero}")


if __name__ == "__main__":
    main()
