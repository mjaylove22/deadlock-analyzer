"""After a match ends: get its data as soon as it's ready, without wasting fetches from Steam.

A match's data isn't available the moment the end screen appears ("REPLAY NOT YET READY"). The
API's stored copy costs nothing to ask for, so it's checked every minute; Steam allows only 3
fetches an hour, so it's asked at most three times, spread out (3, 10 and 25 minutes after the end
screen), and only while the stored copy still says no. After an hour the app stops waiting.

The API's match history often gets a match hours after it ends, so your row from each end screen is
kept too: Home lists tonight's matches straight away and sums up the session.
"""

import json
import logging
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

import paths
from match_review import MatchUnavailable, steam_fetches_left
from player_lookup import same_name

logger = logging.getLogger(__name__)

END_SCREENS_FILE = paths.data("cache", "end_screens.json")
KEEP_END_SCREENS = 20
SESSION_GAP_S = 2 * 3600  # a longer break between two matches starts a new session
CHECK_EVERY_S = 60
STEAM_AFTER_S = (180, 600, 1500)
GIVE_UP_AFTER_S = 3600


class PostGame:
    """One finished match the app is waiting for."""

    def __init__(self, match_id: Optional[int], ended_at: Optional[float] = None,
                 screen: Optional[Dict[str, Any]] = None):
        self.match_id = match_id   # None when it couldn't be read: then only the screen's numbers exist
        self.screen = screen       # the scoreboard read off the end screen (end_screen.read_scoreboard)
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


def link_players(screen: Dict[str, Any], me: Optional[Dict[str, Any]], lobby_results: List[Dict[str, Any]] = ()) -> None:
    """Give the end screen's players their accounts, from the lobby captured during the match (each
    hero is played once, so the hero says who's who), and mark which one is you (by account, else by
    your Steam name). Accounts let the ratings use everyone's rank, and the page link to players."""
    by_hero = {r["hero"]: r for r in lobby_results if r.get("account_id")}
    for p in screen["players"]:
        known = by_hero.get(p["hero"])
        if known:
            p["account_id"] = known["account_id"]
            p["name"] = known.get("player") or p["name"]
    mine = None
    if me:
        mine = (next((p for p in screen["players"] if p["account_id"] == me["account_id"]), None)
                or next((p for p in screen["players"] if p["name"] and same_name(p["name"], me["name"])), None))
        if mine:
            mine["account_id"] = me["account_id"]
    screen["me"] = mine


def is_our_match(summary: Dict[str, Any], me: Optional[Dict[str, Any]], lobby_heroes: Iterable[str] = ()) -> bool:
    """A misread match ID would open a stranger's match: it's ours if your account played in it, or
    (account not set) if at least half the heroes from the captured lobby are in it."""
    if me:
        return any(p["account_id"] == me["account_id"] for p in summary["players"])
    lobby_heroes = set(lobby_heroes)
    match_heroes = {p["hero"] for p in summary["players"]}
    return bool(lobby_heroes) and len(lobby_heroes & match_heroes) * 2 >= len(lobby_heroes)


def load_end_screens() -> List[Dict[str, Any]]:
    try:
        with open(END_SCREENS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def remember_end_screen(screen: Dict[str, Any], match_id: Optional[int], now: Optional[float] = None) -> None:
    """Keep your row from an end screen, shaped like a match-history entry (profiles.describe_match).
    ponytail: needs the match ID (to drop it once the API has it); an unread ID shows once the API has it."""
    me = screen.get("me")
    if not me or not match_id:
        return
    minutes = screen.get("minutes") or 0
    row = {"match_id": match_id, "hero": me["hero"], "mode": screen["mode"],
           "won": me["won"] if screen.get("winning_team") is not None else None,  # banner unread: unknown
           "kills": me.get("kills", 0), "deaths": me.get("deaths", 0), "assists": me.get("assists", 0),
           "net_worth": me.get("net_worth", 0), "minutes": round(minutes),
           "start_time": int((time.time() if now is None else now) - minutes * 60)}
    rows = [r for r in load_end_screens() if r["match_id"] != match_id][-(KEEP_END_SCREENS - 1):] + [row]
    try:
        os.makedirs(os.path.dirname(END_SCREENS_FILE), exist_ok=True)
        with open(END_SCREENS_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f)
    except OSError as e:
        logger.warning(f"Couldn't keep the end screen's numbers ({e})")


def recent_matches(api_matches: List[Dict[str, Any]], end_screens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The API's matches plus end-screen ones it doesn't have yet, newest first. The API's row wins:
    it has the real start time (an old match opened in game shows an end screen too)."""
    known = {m["match_id"] for m in api_matches}
    return sorted(list(api_matches) + [m for m in end_screens if m["match_id"] not in known],
                  key=lambda m: m["start_time"], reverse=True)


def last_session(matches: List[Dict[str, Any]], gap_s: float = SESSION_GAP_S) -> Optional[Dict[str, Any]]:
    """The newest run of matches (newest first) with under gap_s from one's end to the next's start,
    with the record, average K/D/A and souls per minute. None without matches."""
    if not matches:
        return None
    session = matches[:1]
    for m in matches[1:]:
        if session[-1]["start_time"] - (m["start_time"] + m["minutes"] * 60) > gap_s:
            break
        session.append(m)
    games, minutes = len(session), sum(m["minutes"] for m in session)
    return {"games": games, "wins": sum(m["won"] is True for m in session), "losses": sum(m["won"] is False for m in session),
            **{key: sum(m[key] for m in session) / games for key in ("kills", "deaths", "assists")},
            "souls_per_min": sum(m["net_worth"] for m in session) / minutes if minutes else None,
            "ended": session[0]["start_time"] + session[0]["minutes"] * 60}
