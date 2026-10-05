"""After a match ends: get its data as soon as it's ready, without wasting fetches from Steam.

A match's data isn't available the moment the end screen appears ("REPLAY NOT YET READY"). The
API's stored copy costs nothing to ask for, so it's checked every minute; Steam allows only 3
fetches an hour, so it's asked at most three times, spread out (3, 10 and 25 minutes after the end
screen), and only while the stored copy still says no. After an hour the app stops waiting.
"""

import time
from typing import Any, Callable, Dict, Iterable, Optional

from match_review import MatchUnavailable, steam_fetches_left

CHECK_EVERY_S = 60
STEAM_AFTER_S = (180, 600, 1500)
GIVE_UP_AFTER_S = 3600


class PostGame:
    """One finished match the app is waiting for."""

    def __init__(self, match_id: int, ended_at: Optional[float] = None):
        self.match_id = match_id
        self.ended_at = time.time() if ended_at is None else ended_at
        self.steam_tries = 0
        self.checks = 0
        self.done = False       # ready, or given up
        self.ready = False
        self.last_error = ""

    def allow_steam(self, now: float) -> bool:
        return (self.steam_tries < len(STEAM_AFTER_S) and now - self.ended_at >= STEAM_AFTER_S[self.steam_tries]
                and steam_fetches_left(now) > 0)

    def expired(self, now: float) -> bool:
        return now - self.ended_at > GIVE_UP_AFTER_S

    def attempt(self, get_summary: Callable[[int, bool], Dict[str, Any]], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """One check (worker thread): the summary when it's ready, else None (and last_error says why)."""
        now = time.time() if now is None else now
        steam = self.allow_steam(now)
        if steam:
            self.steam_tries += 1
        self.checks += 1
        try:
            summary = get_summary(self.match_id, steam)
        except MatchUnavailable as e:
            self.last_error = str(e)
            return None
        self.ready = self.done = True
        return summary


def is_our_match(summary: Dict[str, Any], me: Optional[Dict[str, Any]], lobby_heroes: Iterable[str] = ()) -> bool:
    """A misread match ID would open a stranger's match: it's ours if your account played in it, or
    (account not set) if at least half the heroes from the captured lobby are in it."""
    if me:
        return any(p["account_id"] == me["account_id"] for p in summary["players"])
    lobby_heroes = set(lobby_heroes)
    match_heroes = {p["hero"] for p in summary["players"]}
    return bool(lobby_heroes) and len(lobby_heroes & match_heroes) * 2 >= len(lobby_heroes)
