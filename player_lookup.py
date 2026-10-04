"""Look up players from a scoreboard screenshot on the public Deadlock API.

Pipeline: screenshot -> OCR (scoreboard_ocr) -> Steam name search -> hero stats.

Usage:
    python player_lookup.py                      # latest screenshot in screenshots/
    python player_lookup.py path/to/screenshot.png
"""

import logging
import sys
from collections import defaultdict
from typing import Any, Dict, List

import deadlock_api
from scoreboard_ocr import FALLBACK_HERO_NAMES, read_scoreboard
from screenshot_manager import get_screenshot_path
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

TOP_HEROES_SHOWN = 3


def pick_account(candidates: List[Dict[str, Any]], stats_by_account: Dict[int, List[Dict]], current_hero_id: int):
    """Choose which same-named account is the player in the screenshot.

    Steam names aren't unique, so when several accounts share the name, prefer the one
    with the most matches on the hero they're playing right now. Ties keep the API's own
    ranking (name similarity + recent activity), because candidates arrive in that order.
    """
    if len(candidates) == 1:
        return candidates[0], "unique name"

    def matches_on_current_hero(candidate):
        for entry in stats_by_account.get(candidate["account_id"], []):
            if entry["hero_id"] == current_hero_id:
                return entry["matches_played"]
        return 0

    best = max(candidates, key=matches_on_current_hero)  # max() keeps the first of equal items
    best_matches = matches_on_current_hero(best)
    if best_matches > 0:
        return best, f"{len(candidates)} accounts share this name; picked the one with {best_matches} matches on this hero"
    return best, f"{len(candidates)} accounts share this name; none have played this hero, so this pick is a guess"


def lookup_player(record: Dict[str, str], hero_ids_by_name: Dict[str, int], hero_names_by_id: Dict[int, str]) -> Dict[str, Any]:
    """Add Steam account and favourite-hero info to one {"player", "hero", "team"} record."""
    result = dict(record, status=None, note="", account_id=None, profile_url=None, top_heroes=[])
    name = record["player"]

    # In bot lobbies, bots are named after their hero. Searching "Haze" would only find strangers.
    if name.lower() == record["hero"].lower():
        result.update(status="skipped", note="name matches hero, likely a bot")
        return result

    try:
        candidates = deadlock_api.search_steam_profiles(name)

        # Only trust exact name matches: a fuzzy match is usually a different person,
        # or a sign that OCR misread the name.
        exact = [c for c in candidates if c["personaname"].strip().lower() == name.strip().lower()]
        if not exact:
            closest = candidates[0]["personaname"] if candidates else None
            result.update(status="not found", note=f"closest name: {closest!r}" if closest else "no similar names")
            return result

        # One batch request covers every candidate's hero stats
        stats_by_account = defaultdict(list)
        for entry in deadlock_api.get_hero_stats([c["account_id"] for c in exact]):
            stats_by_account[entry["account_id"]].append(entry)

        account, note = pick_account(exact, stats_by_account, hero_ids_by_name.get(record["hero"]))
    except Exception as e:
        result.update(status="error", note=str(e))
        return result

    # Favourite heroes = most matches played
    played = sorted(stats_by_account.get(account["account_id"], []), key=lambda e: e["matches_played"], reverse=True)
    top_heroes = [
        {
            "hero": hero_names_by_id.get(e["hero_id"], f"hero #{e['hero_id']}"),
            "matches": e["matches_played"],
            "win_rate": e["wins"] / e["matches_played"] if e["matches_played"] else 0.0,
        }
        for e in played[:TOP_HEROES_SHOWN]
    ]

    result.update(status="found", note=note, account_id=account["account_id"],
                  profile_url=account["profileurl"], top_heroes=top_heroes)
    return result


def main():
    setup_logger()

    file_path = sys.argv[1] if len(sys.argv) > 1 else get_screenshot_path()
    if not file_path:
        print("No screenshot found.")
        return

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
        print("No players found in the screenshot.")
        return

    results = [lookup_player(r, hero_ids_by_name, hero_names_by_id) for r in records]

    for team in ("friendly", "enemy"):
        print(f"\n=== {team.upper()} TEAM ===")
        for r in results:
            if r["team"] != team:
                continue
            print(f"\n{r['player']}  (playing {r['hero']})  [{r['status']}]")
            if r["note"]:
                print(f"    {r['note']}")
            if r["profile_url"]:
                print(f"    {r['profile_url']}")
            for h in r["top_heroes"]:
                print(f"    {h['hero']:<12} {h['matches']:>4} matches  {h['win_rate']:.0%} wins")


if __name__ == "__main__":
    main()
