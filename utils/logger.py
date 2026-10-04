import logging
import os
from datetime import datetime

def setup_logger():
    """Setup logging with file and console output. Call once, from a script's entry point.

    Handlers go on the root logger so every module's logging.getLogger(__name__) is captured.
    """
    # Create logs directory if it doesn't exist
    os.makedirs('logs', exist_ok=True)

    # Configure the root logger
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    logger = logging.getLogger('deadlock_analyzer')

    # Prevent adding multiple handlers if function is called multiple times
    if not root.handlers:
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # File handler
        file_handler = logging.FileHandler('logs/app.log')
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)
    
    return logger
