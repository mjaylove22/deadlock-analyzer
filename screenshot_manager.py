import os
import re
import mss
from datetime import datetime

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
    screenshot_files = [
        f for f in os.listdir(screenshots_dir)
        if re.fullmatch(r'screenshot_\d{8}_\d{6}\.png', f)
    ]
    
    if not screenshot_files:
        return None
    
    # Sort by modification time (newest first)
    screenshot_files.sort(key=lambda x: os.path.getmtime(os.path.join(screenshots_dir, x)), reverse=True)
    
    return os.path.join(screenshots_dir, screenshot_files[0])
