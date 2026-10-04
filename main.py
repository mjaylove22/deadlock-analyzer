import sys
import keyboard
from utils.logger import setup_logger
from screenshot_manager import capture_and_save_screenshot

# Setup logging
logger = setup_logger()

def on_hotkey():
    """Handle the hotkey press"""
    logger.info("Hotkey Ctrl+Shift+D triggered")
    
    try:
        filepath = capture_and_save_screenshot()
        logger.info(f"Screenshot saved to: {filepath}")
    except Exception as e:
        logger.error(f"Error capturing screenshot: {e}")

def main():
    """Main application function"""
    logger.info("Deadlock Analyzer started - Press Ctrl+Shift+D to capture screenshot")
    logger.info("Press Ctrl+C to exit")
    
    # Register the hotkey
    keyboard.add_hotkey('ctrl+shift+d', on_hotkey)
    
    try:
        # Keep the application running
        while True:
            # Sleep briefly to prevent high CPU usage
            keyboard.wait('ctrl+c')
            logger.info("Shutting down application...")
            break
    except KeyboardInterrupt:
        logger.info("Shutting down application...")
        sys.exit(0)

if __name__ == "__main__":
    main()
