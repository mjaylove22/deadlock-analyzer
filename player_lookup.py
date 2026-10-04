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
from typing import Any, Callable, Dict, List, Optional, Tuple

import deadlock_api
from identity import find_parties, resolve_lobby
from insights import compute_badges, hero_summary
from matchups import build_matchup
from report import build_report, items_text, matchup_text
from settings import get_me
from scoreboard_ocr import FALLBACK_HERO_NAMES, read_scoreboard
from screenshot_manager import get_screenshot_path
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

TOP_HEROES_SHOWN = 3

# OCR sometimes swaps one character for a lookalike ("Or. Night Owl" for "Dr. Night Owl").
# Such a name is accepted as a misread, flagged so the user can see it. A general similarity score
# was tried first and wrongly "corrected" names OCR had read right ("Kovas" -> "Kovmas", "Ravenl" ->
# "raven"): misreads swap characters, they don't add or drop them. Short names have too many
# one-letter neighbours to guess safely, hence the minimum length.
MISREAD_MIN_LENGTH = 6

Progress = Optional[Callable[[str], None]]  # called with a short status message at each step


def report_progress(progress: Progress, message: str) -> None:
    if progress:
        progress(message)


def is_likely_bot(record: Dict[str, str]) -> bool:
    """In bot lobbies, bots are named after their hero. Searching "Haze" would only find strangers."""
    return record["player"].lower() == record["hero"].lower()


def find_candidates(name: str) -> Tuple[List[Dict[str, Any]], str]:
    """Accounts whose Steam name exactly matches, plus a note explaining an empty result.

    Only exact matches are trusted: a fuzzy match is usually a different person, or a sign
    that OCR misread the name. Friend lists are kept in memory for identity resolution only.
    """
    results = deadlock_api.search_steam_profiles(name)
    exact = [as_candidate(c) for c in results if same_name(c["personaname"], name)]
    if exact:
        return exact, ""
    if not results:
        return [], "no similar names"

    # No exact match: maybe OCR swapped one character. The API ranks results by similarity,
    # so the first plausible misread is the best one.
    misread = next((c["personaname"] for c in results if looks_like_misread(name, c["personaname"])), None)
    if misread:
        near = [as_candidate(c) for c in results if same_name(c["personaname"], misread)]
        for c in near:
            c["corrected_name"] = misread.strip()
        return near, ""
    return [], f"closest name: {results[0]['personaname']!r}"


def same_name(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def as_candidate(c: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only what we need from a search result. Friend lists stay in memory, for linking only."""
    return {
        "account_id": c["account_id"],
        "profile_url": c["profileurl"],
        "avatar_url": c.get("avatarmedium") or c.get("avatar"),
        "friends": {f["account_id"] for f in (c.get("friends") or [])},
    }


def looks_like_misread(ocr_name: str, real_name: str) -> bool:
    """True if OCR could have produced ocr_name by misreading one character of real_name."""
    a, b = ocr_name.strip().lower(), real_name.strip().lower()
    if len(a) != len(b) or len(a) < MISREAD_MIN_LENGTH:
        return False
    return sum(x != y for x, y in zip(a, b)) == 1


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
                 hero_names_by_id: Dict[int, str], progress: Progress = None,
                 me: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
    """Resolve every {"player", "hero", "team"} record to an account with stats.

    Returns (results, parties): one result per record, and parties as lists of record indexes.
    The whole lobby is resolved together, because friend links between players are evidence.
    me: the user's own {"name", "account_id"} from settings; a player with that name is them, for certain.
    """
    results = [dict(r, status=None, note="", account_id=None, profile_url=None, top_heroes=[],
                    hero_stats=None, badges=[], confident=False, rank=None, corrected_from=None,
                    avatar_url=None, is_me=False) for r in records]

    candidates_by_player = {}
    for i, result in enumerate(results):
        report_progress(progress, f"Looking up {result['player']} ({i + 1}/{len(results)})...")
        if is_likely_bot(result):
            result.update(status="skipped", note="name matches hero, likely a bot")
            continue
        try:
            if me and same_name(result["player"], me["name"]):
                candidates, note = my_candidate(me), ""
                result["is_me"] = True
            else:
                candidates, note = find_candidates(result["player"])
        except Exception as e:
            result.update(status="error", note=str(e))
            continue
        if not candidates:
            result.update(status="not found", note=note)
            continue
        if "corrected_name" in candidates[0]:
            # Show the real Steam name, and remember what OCR read
            result.update(corrected_from=result["player"], player=candidates[0]["corrected_name"])
        candidates_by_player[i] = candidates

    # One batch request covers every candidate in the lobby (the API accepts up to 1000 ids)
    all_ids = list(dict.fromkeys(c["account_id"] for cs in candidates_by_player.values() for c in cs))
    stats_by_account = defaultdict(list)
    if all_ids:
        report_progress(progress, "Loading hero stats...")
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
    for i in resolved:
        if results[i]["is_me"]:
            resolved[i] = (resolved[i][0], "you (from settings)")
    for i, (account, reason) in resolved.items():
        entries = stats_by_account[account["account_id"]]
        hero_id = hero_ids_by_name.get(results[i]["hero"])
        results[i].update(status="found", note=reason, account_id=account["account_id"],
                          profile_url=account["profile_url"], avatar_url=account["avatar_url"],
                          top_heroes=top_heroes(entries, hero_names_by_id),
                          hero_stats=hero_summary(entries, hero_id),
                          badges=compute_badges(entries, hero_id),
                          confident=is_confident(candidates_by_player[i], reason))

    report_progress(progress, "Loading ranks...")
    attach_ranks(results)
    parties = find_parties(resolved, {i: results[i]["team"] for i in resolved})
    return results, parties


SEARCH_RESULTS_SHOWN = 6


def search_player(name: str, hero_names_by_id: Dict[int, str], progress: Progress = None) -> List[Dict[str, Any]]:
    """Manual search: one result per matching account (exact names first, else the closest names).
    
    Results have the same shape as lobby results, with team "search" and no current hero,
    plus "totals" (all games, overall win rate, games in the last 30 days).
    """
    report_progress(progress, f"Searching for {name}...")
    found = deadlock_api.search_steam_profiles(name)
    exact = [c for c in found if same_name(c["personaname"], name)]
    shown = (exact or found)[:SEARCH_RESULTS_SHOWN]
    if not shown:
        return []

    report_progress(progress, "Loading hero stats...")
    stats_by_account = defaultdict(list)
    for entry in deadlock_api.get_hero_stats([c["account_id"] for c in shown]):
        stats_by_account[entry["account_id"]].append(entry)

    results = []
    for c in shown:
        entries = stats_by_account[c["account_id"]]
        games = sum(e["matches_played"] for e in entries)
        wins = sum(e["wins"] for e in entries)
        results.append(dict(as_candidate(c), player=c["personaname"], hero="", team="search", status="found",
                            note="exact name" if exact else "similar name", confident=True, rank=None,
                            corrected_from=None, hero_stats=None, badges=[],
                            top_heroes=top_heroes(entries, hero_names_by_id),
                            totals={"games": games, "win_rate": wins / games if games else 0.0,
                                    "recent": c.get("matches_played_last_30d")}))
    report_progress(progress, "Loading ranks...")
    attach_ranks(results)
    return results


def my_candidate(me: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The user's own account as the only candidate (their name may be shared by strangers)."""
    profiles = deadlock_api.get_profiles([me["account_id"]])
    return [as_candidate(profiles[0])] if profiles else []


def is_confident(candidates: List[Dict[str, Any]], reason: str) -> bool:
    """Unique names and friend links are strong evidence. Hero history counts only when the
    winner has at least twice the runner-up's games on this hero (55 vs 7 yes, 8 vs 5 no)."""
    if "corrected_name" in candidates[0]:
        return False  # the name itself was a guess at an OCR misread
    if len(candidates) == 1 or reason.startswith("friends with"):
        return True
    games = sorted((c["current_hero_matches"] for c in candidates), reverse=True)
    return games[0] > 0 and games[0] >= 2 * games[1]


def attach_ranks(results: List[Dict[str, Any]]) -> None:
    """Add {"name", "subrank", "color"} ranks for found players, using one batch request."""
    ids = [r["account_id"] for r in results if r["account_id"]]
    if not ids:
        return
    try:
        tiers = deadlock_api.fetch_rank_tiers()
        ranks = {r["account_id"]: r for r in deadlock_api.get_player_ranks(ids)}
    except Exception as e:
        logger.warning(f"Could not load ranks ({e})")
        return
    for result in results:
        rank = ranks.get(result["account_id"])
        if rank and rank["rank"] in tiers:
            tier = tiers[rank["rank"]]
            # Tier 0 (Obscurus) means no recent ranked games
            name = "Unranked" if rank["rank"] == 0 else f"{tier['name']} {rank['subrank']}"
            # "badge" (tier * 10 + subrank) is kept so ranks can be compared
            result["rank"] = {"name": name, "color": tier["color"], "badge": rank["rank"] * 10 + rank["subrank"]}


def analyze_screenshot(file_path: str, progress: Progress = None,
                       me: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
    """The whole pipeline: screenshot -> (results, parties). Used by the terminal and the app."""
    # One hero list feeds both OCR (names) and stats (ids)
    try:
        heroes = deadlock_api.fetch_heroes()
    except Exception as e:
        logger.warning(f"Could not load heroes from the API ({e}); player lookups will fail")
        heroes = [{"id": None, "name": name} for name in FALLBACK_HERO_NAMES]
    hero_ids_by_name = {h["name"]: h["id"] for h in heroes}
    hero_names_by_id = {h["id"]: h["name"] for h in heroes}

    report_progress(progress, "Reading the scoreboard...")
    records = read_scoreboard(file_path, list(hero_ids_by_name))
    if not records:
        return [], []
    return lookup_lobby(records, hero_ids_by_name, hero_names_by_id, progress, me)


def main():
    setup_logger()
    # The report uses characters like "·"; Windows consoles default to a legacy encoding
    sys.stdout.reconfigure(encoding="utf-8")

    file_path = sys.argv[1] if len(sys.argv) > 1 else get_screenshot_path()
    if not file_path:
        print("No screenshot found.")
        return

    results, parties = analyze_screenshot(file_path, me=get_me())
    for text, style in build_report(results, parties):
        print(text)

    heroes = deadlock_api.fetch_heroes()
    matchup = build_matchup(results, {h["name"]: h["id"] for h in heroes}, {h["id"]: h["name"] for h in heroes})
    if matchup:
        print(f"YOUR MATCHUP: {matchup['hero']} (averages {matchup['average_win_rate']:.0%})")
        print("    vs " + " · ".join(matchup_text(m) for m in matchup["matchups"]))
        print("    " + items_text(matchup))


if __name__ == "__main__":
    main()
