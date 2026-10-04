import logging
import os
import re
import time
from datetime import datetime
from typing import List

import mss

logger = logging.getLogger(__name__)

SCREENSHOT_NAME = re.compile(r'screenshot_\d{8}_\d{6}\.png')  # e.g. screenshot_20260930_123906.png
RETENTION_DAYS = 7


def capture_and_save_screenshot():
    """Capture screenshot and save with timestamped filename"""
    # Create screenshots directory if it doesn't exist
    os.makedirs('screenshots', exist_ok=True)

    # Generate timestamped filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"screenshot_{timestamp}.png"
    filepath = os.path.join('screenshots', filename)

    # Capture screenshot
    with mss.mss() as sct:
        # Capture primary monitor
        sct.shot(output=filepath)

    return filepath


def get_screenshot_path():
    """Get the path to the latest screenshot"""
    screenshots_dir = 'screenshots'
    if not os.path.exists(screenshots_dir):
        return None

    # Get original captures only (e.g. screenshot_20260930_123906.png, not *_gray.png debug files)
    screenshot_files = [f for f in os.listdir(screenshots_dir) if SCREENSHOT_NAME.fullmatch(f)]

    if not screenshot_files:
        return None

    # Sort by modification time (newest first)
    screenshot_files.sort(key=lambda x: os.path.getmtime(os.path.join(screenshots_dir, x)), reverse=True)

    return os.path.join(screenshots_dir, screenshot_files[0])


def delete_old_screenshots(screenshots_dir: str = 'screenshots', max_age_days: float = RETENTION_DAYS) -> List[str]:
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
        if not SCREENSHOT_NAME.fullmatch(name):
            continue
        if os.path.exists(path.replace('.png', '.expected.json')):
            continue
        if os.path.getmtime(path) < cutoff:
            os.remove(path)
            deleted.append(path)
    if deleted:
        logger.info(f"Deleted {len(deleted)} screenshot(s) older than {max_age_days} days")
    return deleted
