"""Read the in-game scoreboard from a screenshot: which player is on which hero and team.

Screenshot OCR only - nothing here touches the game process or game files.

Usage (debug view of the raw OCR lines and parsed rows):
    python scoreboard_ocr.py [path/to/screenshot.png]
"""

import logging
import os
import re
import sys
import unicodedata
from typing import Dict, List, Optional, Tuple

import pytesseract
from PIL import Image, ImageFilter

import deadlock_api
import paths
import layout as layout_module  # "layout" alone would clash with the local variable names

logger = logging.getLogger(__name__)

# The installed app ships its own trimmed Tesseract; from source, use the normal install if present
for candidate in (paths.resource("tesseract", "tesseract.exe"), r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
    if os.path.exists(candidate):
        pytesseract.pytesseract.tesseract_cmd = candidate
        break

# Scoreboard geometry, measured on 1920x1080 screenshots of the Esc menu's PLAYERS tab.
# Other screen sizes are located with layout.py and scaled back to this size before reading.

# Player list crop (left, top, right, bottom).
# - Left starts just past the hero portraits, which OCR otherwise reads as junk like "sy" or "@".
# - Right must stay inside the panel (which ends ~x=1892): including the black strip past it breaks OCR.
PLAYER_LIST_BOX = (1560, 110, 1875, 940)

# Row layout inside the crop: player rows are 60px apart and the first name line starts at y~77.
# The ENEMY TEAM header pushes every enemy row down an extra 40px, so enemy rows sit ~40px off
# the friendly rows' 60px grid. That holds for any team size (6v6, or 4v4 Street Brawl, or a
# lobby where players are still connecting), unlike a fixed split height.
FIRST_ROW_TOP = 77
ROW_PITCH = 60

# OCR preprocessing. The panel is semi-transparent, so its brightness shifts with whatever is
# behind it; left to its own automatic black/white conversion, Tesseract read nothing on a
# slightly brighter, red-tinted screenshot. Measured brightness: panel ~55-63, text ~159.
# A fixed cutoff between them, after upscaling the small text, read 12/12 on every test
# screenshot for any cutoff from 90 to 130.
OCR_SCALE = 2
TEXT_THRESHOLD = 110

# Screens smaller than 1080p: text scaled back up is blurry, and at 720p the plain settings read only
# 1 of 7 players on one test screenshot. Sharpening plus a slightly higher cutoff read 7 of 7 (and
# 11 of 12 on the 6v6 ones). Tested on 1080p screenshots scaled down; at 1080p and above, sharpening
# slightly hurt, so it's only used below.
SMALL_SCREEN_SCALE = 0.9
SMALL_SCREEN_THRESHOLD = 120
SMALL_SCREEN_SHARPEN = ImageFilter.UnsharpMask(radius=2, percent=150, threshold=2)

# The panel's textured background sometimes adds junk words at the end of a line
# ("BrightFox ." or "... Owl . pees"). Those junk words had confidence 0-47, while real name
# words scored 60+, so low-confidence words are dropped from the end of each line.
TRAILING_JUNK_CONFIDENCE = 50

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


def prepare_for_ocr(crop: Image.Image, screen_scale: float = 1.0) -> Image.Image:
    """Upscale, then turn light text into black-on-white with a fixed brightness cutoff.
    screen_scale < 1 means the screenshot came from a screen smaller than 1080p."""
    gray = crop.resize((crop.width * OCR_SCALE, crop.height * OCR_SCALE), Image.LANCZOS).convert("L")
    threshold = TEXT_THRESHOLD
    if screen_scale < SMALL_SCREEN_SCALE:
        gray = gray.filter(SMALL_SCREEN_SHARPEN)
        threshold = SMALL_SCREEN_THRESHOLD
    return gray.point(lambda v: 0 if v > threshold else 255)


def find_layout(image: Image.Image) -> layout_module.Layout:
    """Where the scoreboard is in this screenshot. If the PLAYERS tab can't be confirmed (e.g. the
    menu was closing), fall back to the most likely layout for the image's size."""
    width, height = image.size
    found = layout_module.locate_in_image(image, layout_module.remembered(width, height))
    if found:
        return found
    logger.warning(f"Couldn't confirm where the scoreboard is in a {width}x{height} image; using the most likely layout")
    return layout_module.candidates(width, height)[0]


def read_ocr_lines(image: Image.Image, layout: layout_module.Layout = None) -> List[Tuple[int, str]]:
    """OCR the player list once. Returns (top, text) per line in screen order; top is in pixels of
    the crop as it looks at 1920x1080 (other sizes are scaled back to that first)."""
    layout = layout or find_layout(image)
    prepared = prepare_for_ocr(layout_module.normalized_crop(image, layout, PLAYER_LIST_BOX), layout.scale)
    # --psm 6 treats the crop as one uniform block of text
    data = pytesseract.image_to_data(prepared, config="--psm 6", output_type=pytesseract.Output.DICT)

    # Tesseract returns individual words; rebuild lines and remember how far down each one is
    lines = {}  # (block, paragraph, line) -> {"top": y, "words": [...]}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in lines:
            # Positions come from the upscaled image, so scale back to crop coordinates
            lines[key] = {"top": data["top"][i] // OCR_SCALE, "words": []}
        lines[key]["words"].append((word, float(data["conf"][i])))

    result = []
    for line in lines.values():
        words = line["words"]
        # Only trailing words, and always keep the first, so a real name is never emptied
        while len(words) > 1 and words[-1][1] < TRAILING_JUNK_CONFIDENCE:
            words.pop()
        result.append((line["top"], " ".join(word for word, _ in words)))
    return result


# OCR sometimes swaps one character for a lookalike ("Or. Night Owl", "Oynamo") and drops or
# adds spaces ("Dr.NightOwl", "ina pond"). A general similarity score was tried first and wrongly
# "corrected" correctly read names ("Kovas" -> "Kovmas"): misreads swap characters, they don't add or
# drop them. Short names have too many one-letter neighbours to guess safely, hence a minimum length.
MISREAD_MIN_LENGTH = 6
HERO_MISREAD_MIN_LENGTH = 5  # only 39 hero names to confuse, so a slightly shorter minimum is safe


def squash(text: str) -> str:
    """Lower case, without spaces, and with stylised letters made plain: OCR adds and drops spaces,
    and some Steam names use full-width letters ("ｍｏｏｎｄｏｇ") or invisible characters."""
    plain = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in plain if not c.isspace() and unicodedata.category(c) != "Cf")


def looks_like_misread(ocr_text: str, real_text: str, min_length: int = MISREAD_MIN_LENGTH) -> bool:
    """True if OCR could have produced ocr_text by misreading one character of real_text (spaces ignored)."""
    a, b = squash(ocr_text), squash(real_text)
    if len(a) != len(b) or len(a) < min_length:
        return False
    return sum(x != y for x, y in zip(a, b)) == 1


def match_hero(line: str, heroes_longest_first: List[str]) -> Optional[str]:
    """The hero named in a "<Hero> Level" line: an exact match, or else one letter off ("Oynamo")."""
    lowered = line.lower()
    exact = next((h for h in heroes_longest_first if h.lower() in lowered), None)
    if exact:
        return exact
    words = re.findall(r"[^\s]+", line)
    for hero in heroes_longest_first:
        size = len(hero.split())
        for i in range(len(words) - size + 1):
            if looks_like_misread(" ".join(words[i:i + size]), hero, HERO_MISREAD_MIN_LENGTH):
                return hero
    return None


def team_for_row(name_top: int) -> str:
    """Which team a row belongs to, from where its name line sits on the 60px row grid.

    Friendly rows sit ~0px off the grid and enemy rows ~40px off; the cutoffs (20 and 50)
    are halfway between, leaving ~10px of tolerance either way.
    """
    offset = (name_top - FIRST_ROW_TOP) % ROW_PITCH
    return "enemy" if 20 <= offset < 50 else "friendly"


def parse_player_rows(lines: List[Tuple[int, str]], hero_names: List[str]) -> List[Tuple[int, str, str]]:
    """Pair each player's Steam name with their hero.

    Each scoreboard row is two lines:
        <Steam name>
        <Hero> Level -1
    so a line containing a hero name and "Level" is paired with the line above it.
    Takes (top, text) lines; returns (name_top, player, hero) tuples in screen order.
    """
    # Check longer names first so a short name can never match inside a longer one
    heroes_longest_first = sorted(hero_names, key=len, reverse=True)
    rows = []
    previous_line = None  # Candidate Steam name: (top, text) of the last meaningful line seen

    for top, line in lines:
        line = line.strip()

        # Skip blank lines and pure noise (no letters at all, e.g. "=" or ",")
        if not any(ch.isalpha() for ch in line):
            continue

        # Team headers are never player names
        if any(header in line.upper() for header in ("MY TEAM", "ENEMY TEAM", "FRIENDS")):
            previous_line = None
            continue

        if "level" in line.lower():
            hero = match_hero(line, heroes_longest_first)
            if hero and previous_line:
                name_top, name = previous_line
                rows.append((name_top, name, hero))
            # A hero line is never a Steam name for the next row
            previous_line = None
        else:
            previous_line = (top, line)

    return rows


def read_scoreboard(file_path: str, hero_names: List[str]) -> List[Dict[str, str]]:
    """Return one {"player", "hero", "team"} record per player found in the screenshot."""
    with Image.open(file_path) as image:
        lines = read_ocr_lines(image)
    logger.debug(f"OCR lines: {lines}")

    return [
        {"player": player, "hero": hero, "team": team_for_row(name_top)}
        for name_top, player, hero in parse_player_rows(lines, hero_names)
    ]


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
        lines = read_ocr_lines(image)
    hero_names = load_hero_names()

    print("\nRaw OCR lines (top = pixels from the top of the crop):")
    for top, text in lines:
        print(f"  top={top:3d}  {text!r}")
    print("\nParsed rows:")
    for name_top, player, hero in parse_player_rows(lines, hero_names):
        print(f"  {team_for_row(name_top):<8}  {player} -> {hero}")


if __name__ == "__main__":
    main()
