"""Your history with other players: matches you played against or with them (from the API), and your
own notes about them (kept on this PC only, in notes.json).

The API counts every recorded match for each opponent and teammate in one request each
(/v1/players/{you}/enemy-stats and /mate-stats). "wins" there is *your* result, checked in the API's
source. Matches appear there hours after they end, so someone met earlier tonight may not count yet.
"""

import json
import logging
import time
from typing import Any, Dict, Optional

import deadlock_api
import paths

logger = logging.getLogger(__name__)

NOTES_FILE = paths.data("notes.json")
MAX_NOTE_LENGTH = 200
MODES = ("normal", "street_brawl")


def met_before(me: Dict[str, Any]) -> Dict[str, list]:
    """{account id (as text): [faced, won against, teamed, won with, last match id]} for everyone the
    user has met in a recorded match, both modes together. Rebuilt once a day: four requests at once."""
    def build():
        calls = [lambda m=m, k=k: deadlock_api.get_json(f"/v1/players/{me['account_id']}/{k}-stats",
                                                       {"game_mode": m}, max_age=0)
                 for m in MODES for k in ("enemy", "mate")]
        met: Dict[str, list] = {}
        for n, rows in enumerate(deadlock_api.parallel(*calls)):
            as_enemy = n % 2 == 0
            for r in rows:
                entry = met.setdefault(str(r["enemy_id" if as_enemy else "mate_id"]), [0, 0, 0, 0, 0])
                offset = 0 if as_enemy else 2
                entry[offset] += r["matches_played"]
                entry[offset + 1] += r["wins"]
                entry[4] = max([entry[4]] + list(r.get("matches") or []))
        return met
    return deadlock_api.disk_cached(f"met_{me['account_id']}", build, max_age=86400)


def record_with(account_id: Optional[int], met: Dict[str, list]) -> Optional[Dict[str, int]]:
    """{"faced", "won_against", "teamed", "won_with", "last_match"} for one player, or None if never met."""
    entry = met.get(str(account_id)) if account_id else None
    if not entry:
        return None
    faced, won_against, teamed, won_with, last_match = entry
    return {"faced": faced, "won_against": won_against, "teamed": teamed, "won_with": won_with,
            "last_match": last_match}


def load_notes() -> Dict[str, Dict[str, Any]]:
    """{account id (as text): {"text", "name", "updated"}}; {} if there are none (or the file is unreadable)."""
    try:
        with open(NOTES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get_note(account_id: Optional[int]) -> str:
    return load_notes().get(str(account_id), {}).get("text", "") if account_id else ""


def save_note(account_id: int, name: str, text: str) -> None:
    """Save (or, with empty text, delete) the note on one player. The name is only a reminder of who it
    was when the file is read by hand: Steam names change, account ids don't."""
    notes = load_notes()
    text = " ".join(text.split())[:MAX_NOTE_LENGTH]  # one line: it's shown on a lobby card
    if text:
        notes[str(account_id)] = {"text": text, "name": name, "updated": int(time.time())}
    else:
        notes.pop(str(account_id), None)
    try:
        with open(NOTES_FILE, "w", encoding="utf-8") as f:
            json.dump(notes, f, indent=1, ensure_ascii=False)
    except OSError as e:
        logger.warning(f"Could not save the note ({e})")
        raise
