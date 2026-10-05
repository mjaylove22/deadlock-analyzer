import logging
import os
import re
import time
from datetime import datetime
from typing import List

import mss
import mss.tools

import game_window
import paths

logger = logging.getLogger(__name__)

SCREENSHOT_NAME = re.compile(r'screenshot_\d{8}_\d{6}\.png')  # e.g. screenshot_20260930_123906.png
END_SCREEN_NAME = re.compile(r'endscreen_\d{8}_\d{6}\.png')   # end-of-match screens (not lobbies)
RETENTION_DAYS = 3  # ~1 MB each, up to ~11 captures a day: ~33 MB at most
SCREENSHOT_DIR = paths.data("screenshots")


def capture_and_save_screenshot():
    """Capture screenshot and save with timestamped filename"""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    # Generate timestamped filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"screenshot_{timestamp}.png"
    filepath = os.path.join(SCREENSHOT_DIR, filename)

    # The game's window wherever it is (any monitor, windowed too); else the main monitor
    region = game_window.find_window()
    with mss.mss() as sct:
        area = sct.monitors[1] if region is None else {
            "left": region[0], "top": region[1], "width": region[2] - region[0], "height": region[3] - region[1]}
        mss.tools.to_png(sct.grab(area).rgb, (area["width"], area["height"]), output=filepath)

    return filepath


def save_end_screen(image) -> str:
    """Keep the end-of-match screen the app detected, like other captures (deleted after RETENTION_DAYS).
    Named apart from lobby screenshots, so "Analyze latest" never picks one up."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"endscreen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
    image.save(path)
    return path


def get_screenshot_path():
    """Get the path to the latest screenshot"""
    screenshots_dir = SCREENSHOT_DIR
    if not os.path.exists(screenshots_dir):
        return None

    # Get original captures only (e.g. screenshot_20260930_123906.png, not *_gray.png debug files)
    screenshot_files = [f for f in os.listdir(screenshots_dir) if SCREENSHOT_NAME.fullmatch(f)]

    if not screenshot_files:
        return None

    # Sort by modification time (newest first)
    screenshot_files.sort(key=lambda x: os.path.getmtime(os.path.join(screenshots_dir, x)), reverse=True)

    return os.path.join(screenshots_dir, screenshot_files[0])


def delete_old_screenshots(screenshots_dir: str = SCREENSHOT_DIR, max_age_days: float = RETENTION_DAYS) -> List[str]:
    """Delete captures older than max_age_days and return their paths.

    Only files named like the app's own captures are touched. A screenshot with a matching
    .expected.json is a regression test case (see tests/test_screenshots.py) and is always kept.
    """
    if not os.path.isdir(screenshots_dir):
        return []
    cutoff = time.time() - max_age_days * 24 * 60 * 60
    deleted = []
    for name in os.listdir(screenshots_dir):
        path = os.path.join(screenshots_dir, name)
        if not (SCREENSHOT_NAME.fullmatch(name) or END_SCREEN_NAME.fullmatch(name)):
            continue
        if os.path.exists(path.replace('.png', '.expected.json')):
            continue
        if os.path.getmtime(path) < cutoff:
            os.remove(path)
            deleted.append(path)
    if deleted:
        logger.info(f"Deleted {len(deleted)} screenshot(s) older than {max_age_days} days")
    return deleted
