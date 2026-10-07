"""Per-user settings in settings.json (gitignored): window layout and which Steam account is you.

    {"geometry": "1180x820+40+40", "overlay": false,
     "me": {"name": "Your Steam name", "account_id": 123456789}}

Saving merges into the existing file, so the app's window settings and "me" never overwrite each other.
"""

import json
import logging
from typing import Any, Dict, Optional

import paths

logger = logging.getLogger(__name__)

SETTINGS_FILE = paths.data("settings.json")


def load_settings() -> Dict[str, Any]:
    """Saved settings, or {} on first run (or if the file is unreadable)."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(changes: Dict[str, Any]) -> None:
    """Update some settings, keeping the rest."""
    settings = load_settings()
    settings.update(changes)
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError as e:
        logger.warning(f"Could not save settings ({e})")


# Choices on the Settings page, with their defaults. Stored under "preferences", so new choices can be
# added later without touching old settings files.
PREFERENCES = {
    "pop_up": False,             # bring the window to the front when a lobby is captured in game
    "sound": True,               # a sound when the lobby is ready
    "reshow_same_lobby": True,   # reopening the scoreboard in the same lobby shows the lobby again
    "post_game_review": True,    # the end-of-match screen opens that match's review
    "show_rank": True,
    "show_hero_stats": True,
    "show_badges": True,
    "show_matchup": True,
    "show_history": True,        # your record with each player, and your notes on them
}


def get_preferences() -> Dict[str, bool]:
    saved = load_settings().get("preferences", {})
    return {key: saved.get(key, default) for key, default in PREFERENCES.items()}


def set_preference(key: str, value: bool) -> None:
    saved = load_settings().get("preferences", {})
    save_settings({"preferences": dict(saved, **{key: value})})


def get_me() -> Optional[Dict[str, Any]]:
    """{"name", "account_id"} of the user's own Steam account, if they've set it."""
    me = load_settings().get("me")
    return me if me and me.get("account_id") else None
