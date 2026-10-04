"""Look up every player from a scoreboard screenshot on the public Deadlock API.

Pipeline: screenshot -> OCR (scoreboard_ocr) -> Steam name search -> hero stats
          -> identity resolution and party detection (identity) -> report.

Usage:
    python player_lookup.py                      # latest screenshot in screenshots/
    python player_lookup.py path/to/screenshot.png
"""

import logging
import sys
from collections import defaultdict
from typing import Any, Dict, List, Tuple

import deadlock_api
from identity import find_parties, resolve_lobby
from report import build_report
from scoreboard_ocr import FALLBACK_HERO_NAMES, read_scoreboard
from screenshot_manager import get_screenshot_path
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

TOP_HEROES_SHOWN = 3


def is_likely_bot(record: Dict[str, str]) -> bool:
    """In bot lobbies, bots are named after their hero. Searching "Haze" would only find strangers."""
    return record["player"].lower() == record["hero"].lower()


def find_candidates(name: str) -> Tuple[List[Dict[str, Any]], str]:
    """Accounts whose Steam name exactly matches, plus a note explaining an empty result.

    Only exact matches are trusted: a fuzzy match is usually a different person, or a sign
    that OCR misread the name. Friend lists are kept in memory for identity resolution only.
    """
    results = deadlock_api.search_steam_profiles(name)
    exact = [
        {
            "account_id": c["account_id"],
            "profile_url": c["profileurl"],
            "friends": {f["account_id"] for f in (c.get("friends") or [])},
        }
        for c in results
        if c["personaname"].strip().lower() == name.strip().lower()
    ]
    if exact:
        return exact, ""
    return [], f"closest name: {results[0]['personaname']!r}" if results else "no similar names"


def top_heroes(entries: List[Dict], hero_names_by_id: Dict[int, str]) -> List[Dict[str, Any]]:
    """Favourite heroes = most matches played."""
    played = sorted(entries, key=lambda e: e["matches_played"], reverse=True)
    return [
        {
            "hero": hero_names_by_id.get(e["hero_id"], f"hero #{e['hero_id']}"),
            "matches": e["matches_played"],
            "win_rate": e["wins"] / e["matches_played"] if e["matches_played"] else 0.0,
        }
        for e in played[:TOP_HEROES_SHOWN]
    ]


def lookup_lobby(records: List[Dict[str, str]], hero_ids_by_name: Dict[str, int],
                 hero_names_by_id: Dict[int, str]) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
    """Resolve every {"player", "hero", "team"} record to an account with stats.

    Returns (results, parties): one result per record, and parties as lists of record indexes.
    The whole lobby is resolved together, because friend links between players are evidence.
    """
    results = [dict(r, status=None, note="", account_id=None, profile_url=None, top_heroes=[]) for r in records]

    candidates_by_player = {}
    for i, result in enumerate(results):
        if is_likely_bot(result):
            result.update(status="skipped", note="name matches hero, likely a bot")
            continue
        try:
            candidates, note = find_candidates(result["player"])
        except Exception as e:
            result.update(status="error", note=str(e))
            continue
        if not candidates:
            result.update(status="not found", note=note)
            continue
        candidates_by_player[i] = candidates

    # One batch request covers every candidate in the lobby (the API accepts up to 1000 ids)
    all_ids = list(dict.fromkeys(c["account_id"] for cs in candidates_by_player.values() for c in cs))
    stats_by_account = defaultdict(list)
    if all_ids:
        try:
            for entry in deadlock_api.get_hero_stats(all_ids):
                stats_by_account[entry["account_id"]].append(entry)
        except Exception as e:
            logger.warning(f"Could not load hero stats ({e}); identities will rely on names and friends only")

    for i, candidates in candidates_by_player.items():
        hero_id = hero_ids_by_name.get(results[i]["hero"])
        for c in candidates:
            c["current_hero_matches"] = sum(e["matches_played"] for e in stats_by_account[c["account_id"]]
                                            if e["hero_id"] == hero_id)

    names = {i: results[i]["player"] for i in candidates_by_player}
    resolved = resolve_lobby(candidates_by_player, names)
    for i, (account, reason) in resolved.items():
        results[i].update(status="found", note=reason, account_id=account["account_id"],
                          profile_url=account["profile_url"],
                          top_heroes=top_heroes(stats_by_account[account["account_id"]], hero_names_by_id))

    parties = find_parties(resolved, {i: results[i]["team"] for i in resolved})
    return results, parties


def analyze_screenshot(file_path: str) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
    """The whole pipeline: screenshot -> (results, parties). Used by the terminal and the app."""
    # One hero list feeds both OCR (names) and stats (ids)
    try:
        heroes = deadlock_api.fetch_heroes()
    except Exception as e:
        logger.warning(f"Could not load heroes from the API ({e}); player lookups will fail")
        heroes = [{"id": None, "name": name} for name in FALLBACK_HERO_NAMES]
    hero_ids_by_name = {h["name"]: h["id"] for h in heroes}
    hero_names_by_id = {h["id"]: h["name"] for h in heroes}

    records = read_scoreboard(file_path, list(hero_ids_by_name))
    if not records:
        return [], []
    return lookup_lobby(records, hero_ids_by_name, hero_names_by_id)


def main():
    setup_logger()

    file_path = sys.argv[1] if len(sys.argv) > 1 else get_screenshot_path()
    if not file_path:
        print("No screenshot found.")
        return

    results, parties = analyze_screenshot(file_path)
    for text, style in build_report(results, parties):
        print(text)


if __name__ == "__main__":
    main()
