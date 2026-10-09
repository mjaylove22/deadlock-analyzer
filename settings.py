"""Per-user settings in settings.json (gitignored): window layout and which Steam account is you.

    {"geometry": "1180x820+40+40", "overlay": false,
     "me": {"name": "Your Steam name", "account_id": 123456789}}

Saving merges into the existing file, so the app's window settings and "me" never overwrite each other.
"""

import json
import logging
from typing import Any, Dict, List, Optional

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
    "show_kda": True,            # the second stat line: KDA and damage on their hero
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


def set_me(name: str, account_id: int) -> None:
    save_settings({"me": {"name": name, "account_id": account_id}, "me_guess": None})


# "Is this you?" before the account is set: you're in every lobby you capture, a stranger almost never is,
# and a friend drops out at the first lobby without them.
GUESS_AFTER_LOBBIES = 2
MAX_GUESSES = 3  # more means a group that always plays together: wait for a lobby without some of them


def update_me_guess(guess: Optional[Dict[str, Any]], results: List[Dict[str, Any]], match_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """The accounts found in every lobby captured in game so far: {"lobbies", "match_id", "accounts":
    {account_id: {"name", "avatar_url"}}} (JSON keys are strings). A lobby that misses everyone in it (your
    name misread, say) starts the count again from that lobby. ponytail: an account found under a different
    ID in each lobby (a name shared by several accounts, unsure each time) never adds up; searching still works."""
    found = {str(r["account_id"]): {"name": r["player"], "avatar_url": r.get("avatar_url")}
             for r in results if r.get("status") == "found" and r.get("account_id")}
    if not found:
        return guess
    if guess:
        common = {a: found[a] for a in guess["accounts"] if a in found}
        if (match_id and match_id == guess.get("match_id")) or len(common) > 6:
            return guess  # the same lobby read again (more than a full party in common)
        if common:
            return {"lobbies": guess["lobbies"] + 1, "match_id": match_id, "accounts": common}
    return {"lobbies": 1, "match_id": match_id, "accounts": found}


def guessed_me(guess: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[{"account_id", "name", "avatar_url"}] to ask "Is this you?" about, or [] while it's too early."""
    if not guess or guess["lobbies"] < GUESS_AFTER_LOBBIES or len(guess["accounts"]) > MAX_GUESSES:
        return []
    return [dict(v, account_id=int(k)) for k, v in guess["accounts"].items()]
