"""Where the app's files live, whether it runs from source or as the installed program.

- Resources: files shipped with the app (icons, the reference tab image, the bundled Tesseract).
  From source: the project folder. Installed (a PyInstaller build): the bundle's "_internal" folder.
- Data: files the app writes (settings, cache, screenshots, logs). From source: the project
  folder. Installed: the folder holding the .exe. It's installed per user, so it's writable.
"""

import os
import sys

INSTALLED = getattr(sys, "frozen", False)  # set by PyInstaller in a built app
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
RESOURCE_DIR = getattr(sys, "_MEIPASS", PROJECT_DIR) if INSTALLED else PROJECT_DIR
DATA_DIR = os.path.dirname(sys.executable) if INSTALLED else PROJECT_DIR


def resource(*parts: str) -> str:
    return os.path.join(RESOURCE_DIR, *parts)


def data(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)
