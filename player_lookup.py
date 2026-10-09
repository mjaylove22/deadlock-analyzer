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
import history
from identity import find_parties, resolve_lobby
from insights import compute_badges, hero_summary
from matchups import build_matchup
from report import build_report, items_text, matchup_text
from settings import get_me
from scoreboard_ocr import FALLBACK_HERO_NAMES, looks_like_misread, read_scoreboard, squash
from screenshot_manager import get_screenshot_path
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

TOP_HEROES_SHOWN = 3


Progress = Optional[Callable[[str], None]]  # called with a short status message at each step


def report_progress(progress: Progress, message: str) -> None:
    if progress:
        progress(message)


def is_likely_bot(record: Dict[str, str]) -> bool:
    """In bot lobbies, bots are named after their hero. Searching "Haze" would only find strangers."""
    return record["player"].lower() == record["hero"].lower()


# Characters OCR mixes up in Steam names, as (what OCR read, what it really was), most common
# first. Names are compared in lower case, so capital I and lower-case l are both "i" / "l" here.
LOOKALIKES = [("i", "l"), ("l", "i"), ("1", "l"), ("l", "1"), ("i", "1"), ("0", "o"), ("o", "0"),
              ("m", "rn"), ("rn", "m"), ("w", "vv"), ("d", "cl"), ("5", "s"), ("s", "5"), ("8", "b"),
              ("b", "8"), ("u", "v"), ("v", "u"), ("e", "c"), ("c", "e")]
MAX_LOOKALIKES = 6        # extra searches per name that had no exact match
LOOKALIKE_RESULTS = 10    # an exact name ranks first, so a few results are enough


def lookalike_names(name: str, limit: int = MAX_LOOKALIKES) -> List[str]:
    """Spellings OCR could have misread as this name, one mix-up each, most likely first.
    e.g. "pierix" -> "plerix": a lower-case L read as i."""
    lowered = name.lower()
    variants: List[str] = []
    for read, real in LOOKALIKES:
        start = lowered.find(read)
        while start != -1 and len(variants) < limit:
            variant = lowered[:start] + real + lowered[start + len(read):]
            if variant not in variants:
                variants.append(variant)
            start = lowered.find(read, start + 1)
    return variants[:limit]


def corrected(profiles: List[Dict[str, Any]], real_name: str) -> List[Dict[str, Any]]:
    """Candidates for a name OCR misread; the real Steam name is shown, and they count as unsure."""
    candidates = [as_candidate(c) for c in profiles if same_name(c["personaname"], real_name)]
    for c in candidates:
        c["corrected_name"] = real_name.strip()
    return candidates


def find_lookalike(name: str) -> List[Dict[str, Any]]:
    """Search the spellings OCR could have misread as this name (all at once); the first one that
    is someone's exact Steam name wins. The name search alone often misses these: the real account
    for "pierix" was 54th in its results, but first when searching "plerix"."""
    variants = lookalike_names(name)
    if not variants:
        return []
    searches = deadlock_api.parallel(*[lambda v=v: deadlock_api.search_steam_profiles(v, LOOKALIKE_RESULTS)
                                       for v in variants], allow_failures=True)
    for results in searches:
        for c in results or []:
            if any(same_name(c["personaname"], v) for v in variants):
                return corrected(results, c["personaname"])
    return []


MATE_MIN_GAMES = 3  # "people you play with": at least 3 matches together (about 20 people for a regular player)


def frequent_mates(me: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The people the user has played at least MATE_MIN_GAMES matches with: {"account_id", "name",
    "profile_url", "avatar_url", "games", "friends"}, kept on disk for a day.

    Some Steam names can't be found by the name search at all: full-width letters ("ｍｏｏｎｄｏｇ")
    aren't searchable, even typed exactly. But friends you queue with are in this list, and their
    names compare fine once Unicode is normalised."""
    def build():
        games = {m["mate_id"]: m["matches_played"] for m in deadlock_api.get_mate_stats(me["account_id"], MATE_MIN_GAMES)}
        profiles = deadlock_api.get_profiles(list(games)) if games else []
        return [{"account_id": p["account_id"], "name": p["personaname"], "profile_url": p["profileurl"],
                 "avatar_url": p.get("avatarmedium") or p.get("avatar"), "games": games.get(p["account_id"], 0),
                 "friends": [f["account_id"] for f in p.get("friends") or []]} for p in profiles]
    return deadlock_api.disk_cached(f"mates_{me['account_id']}", build, max_age=86400)


def mate_candidate(mate: Dict[str, Any], corrected_from_ocr: bool = False) -> Dict[str, Any]:
    candidate = {"account_id": mate["account_id"], "profile_url": mate["profile_url"], "avatar_url": mate["avatar_url"],
                 "friends": set(mate["friends"]), "mate_games": mate["games"]}
    if corrected_from_ocr:
        candidate["corrected_name"] = mate["name"].strip()
    return candidate


def find_candidates(name: str) -> Tuple[List[Dict[str, Any]], str]:
    """Accounts whose Steam name exactly matches, plus a note explaining an empty result.

    Only exact matches are trusted: a fuzzy match is usually a different person, or a sign
    that OCR misread the name. Friend lists are kept in memory for identity resolution only.
    """
    results = deadlock_api.search_steam_profiles(name)
    exact = [as_candidate(c) for c in results if same_name(c["personaname"], name)]
    if exact:
        return exact, ""

    # No exact match: maybe OCR swapped one character. The API ranks results by similarity,
    # so the first plausible misread is the best one.
    misread = next((c["personaname"] for c in results if looks_like_misread(name, c["personaname"])), None)
    if misread:
        return corrected(results, misread), ""
    # Not among the results: search the likely misreadings themselves
    lookalike = find_lookalike(name)
    if lookalike:
        return lookalike, ""
    return [], f"closest name: {results[0]['personaname']!r}" if results else "no similar names"


def same_name(a: str, b: str) -> bool:
    """Ignoring case and spaces: OCR drops and adds spaces ("Dr.NightOwl")."""
    return squash(a) == squash(b)


def as_candidate(c: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only what we need from a search result. Friend lists stay in memory, for linking only."""
    return {
        "account_id": c["account_id"],
        "profile_url": c["profileurl"],
        "avatar_url": c.get("avatarmedium") or c.get("avatar"),
        "friends": {f["account_id"] for f in (c.get("friends") or [])},
    }


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
                    avatar_url=None, is_me=False, history=None, my_note="") for r in records]

    for result in results:
        if is_likely_bot(result):
            result.update(status="skipped", note="name matches hero, likely a bot")
    to_search = [(i, r) for i, r in enumerate(results) if r["status"] != "skipped"]

    mates: Dict[str, Dict[str, Any]] = {}
    if me and to_search:
        try:
            mates = {squash(m["name"]): m for m in frequent_mates(me)}
        except Exception as e:
            logger.info(f"Couldn't load the people you play with ({e})")

    def candidates_for(result):
        """(candidates, note, is_me); candidates is None if the lookup failed."""
        name = result["player"]
        try:
            if me and same_name(name, me["name"]):
                return my_candidate(me), "", True
            # Someone you play with, by name: no search needed, and a stranger with the same name is
            # far less likely to be in your lobby than your friend
            if squash(name) in mates:
                return [mate_candidate(mates[squash(name)])], "", False
            candidates, note = find_candidates(name)
            if not candidates:
                near = next((m for m in mates.values() if looks_like_misread(name, m["name"])), None)
                if near:
                    return [mate_candidate(near, corrected_from_ocr=True)], "", False
            return candidates, note, False
        except Exception as e:
            return None, str(e), False

    # Every player's name is searched at the same time, not one after another
    report_progress(progress, f"Looking up {len(to_search)} players...")
    found = deadlock_api.parallel(*[lambda r=r: candidates_for(r) for _, r in to_search]) if to_search else []

    candidates_by_player = {}
    for (i, result), (candidates, note, is_me) in zip(to_search, found):
        result["is_me"] = is_me
        if candidates is None:
            result.update(status="error", note=note)
            continue
        if not candidates:
            result.update(status="not found", note=note)
            continue
        if "corrected_name" in candidates[0]:
            # Show the real Steam name, and remember what OCR read
            result.update(corrected_from=result["player"], player=candidates[0]["corrected_name"])
        candidates_by_player[i] = candidates

    if any(r["status"] in ("not found", "error") for r in results) or \
            any(len(cs) > 1 or "corrected_name" in cs[0] for cs in candidates_by_player.values()):
        report_progress(progress, "Checking live matches...")
        for i, candidate in live_match_candidates(results, candidates_by_player, hero_ids_by_name, me).items():
            candidates_by_player[i] = [candidate]
            if not same_name(candidate["name"], results[i]["player"]):
                results[i].update(corrected_from=results[i].get("corrected_from") or results[i]["player"],
                                  player=candidate["name"])
            results[i].update(status=None, note="", live=True)

    # One batch request each for every candidate's hero stats and rank, both at the same time
    all_ids = list(dict.fromkeys(c["account_id"] for cs in candidates_by_player.values() for c in cs))
    stats_by_account = defaultdict(list)
    ranks = {}
    met = {}
    if all_ids:
        report_progress(progress, "Loading hero stats and ranks...")

        def stats():
            try:
                return deadlock_api.get_hero_stats(all_ids)
            except Exception as e:
                logger.warning(f"Could not load hero stats ({e}); identities will rely on names and friends only")
                return []
        entries, ranks, met = deadlock_api.parallel(stats, lambda: fetch_ranks(all_ids), lambda: load_met_before(me))
        for entry in entries:
            stats_by_account[entry["account_id"]].append(entry)

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
        elif results[i].get("live"):
            resolved[i] = (resolved[i][0], "exact: this match is in the live match list")
        elif "mate_games" in resolved[i][0]:
            resolved[i] = (resolved[i][0], f"you've played {resolved[i][0]['mate_games']} matches together")
    for i, (account, reason) in resolved.items():
        entries = stats_by_account[account["account_id"]]
        hero_id = hero_ids_by_name.get(results[i]["hero"])
        results[i].update(status="found", note=reason, account_id=account["account_id"],
                          profile_url=account["profile_url"], avatar_url=account["avatar_url"],
                          top_heroes=top_heroes(entries, hero_names_by_id),
                          hero_stats=hero_summary(entries, hero_id),
                          badges=compute_badges(entries, hero_id),
                          confident=is_confident(candidates_by_player[i], reason))

    apply_ranks(results, ranks)
    notes = history.load_notes()
    for r in results:
        if r["status"] == "found" and not r["is_me"]:
            r["history"] = history.record_with(r["account_id"], met)
            r["my_note"] = notes.get(str(r["account_id"]), {}).get("text", "")
    parties = find_parties(resolved, {i: results[i]["team"] for i in resolved})
    missing = [f"{r['player']} ({r['note']})" for r in results if r["status"] in ("not found", "error")]
    logger.info(f"Lobby lookup: {sum(r['status'] == 'found' for r in results)} found, "
                f"{sum(r['status'] == 'skipped' for r in results)} bots" + (f", not found: {'; '.join(missing)}" if missing else ""))
    return results, parties


def load_met_before(me: Optional[Dict[str, Any]]) -> Dict[str, list]:
    """Everyone the user has met in a recorded match (see history.met_before); {} without an account
    set, or if it can't be loaded: the lobby is still shown, just without "met before"."""
    if not me:
        return {}
    try:
        return history.met_before(me)
    except Exception as e:
        logger.info(f"Couldn't load who you've met before ({e})")
        return {}


def live_match_candidates(results: List[Dict[str, Any]], candidates_by_player: Dict[int, List[Dict[str, Any]]],
                          hero_ids_by_name: Dict[str, int], me: Optional[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """{result index: candidate} from the live match list, for players not identified for sure.

    The API knows every account in the top ~200 matches being played. The accounts we're sure of
    (the user, unique names) find the match; it must have this lobby's heroes, then each hero's
    account is exact. Usually there's no such match, which costs one small request."""
    sure = [cs[0]["account_id"] for cs in candidates_by_player.values() if len(cs) == 1 and "corrected_name" not in cs[0]]
    if me:
        sure.append(me["account_id"])
    if not sure:
        return {}
    try:
        matches = deadlock_api.get_active_matches(list(dict.fromkeys(sure)))
    except Exception as e:
        logger.info(f"Live match check failed ({e})")
        return {}
    lobby_heroes = {hero_ids_by_name.get(r["hero"]) for r in results} - {None}
    for match in matches:
        by_hero = {p["hero_id"]: p["account_id"] for p in match.get("players", [])}
        if len(lobby_heroes & set(by_hero)) < max(len(lobby_heroes) - 1, 1):
            continue  # a different match one of these accounts is in
        wanted = {i: by_hero[hero_ids_by_name[r["hero"]]] for i, r in enumerate(results)
                  if r["status"] != "skipped" and hero_ids_by_name.get(r["hero"]) in by_hero
                  and not (i in candidates_by_player and len(candidates_by_player[i]) == 1
                           and "corrected_name" not in candidates_by_player[i][0])}
        if not wanted:
            return {}
        profiles = {p["account_id"]: p for p in deadlock_api.get_profiles(list(wanted.values()))}
        return {i: dict(as_candidate(profiles[a]), name=profiles[a]["personaname"])
                for i, a in wanted.items() if a in profiles}
    return {}


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

    report_progress(progress, "Loading stats...")
    ids = [c["account_id"] for c in shown]
    entries, ranks = deadlock_api.parallel(lambda: deadlock_api.get_hero_stats(ids), lambda: fetch_ranks(ids))
    stats_by_account = defaultdict(list)
    for entry in entries:
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
    apply_ranks(results, ranks)
    return results


def my_candidate(me: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The user's own account as the only candidate (their name may be shared by strangers)."""
    profiles = deadlock_api.get_profiles([me["account_id"]])
    return [as_candidate(profiles[0])] if profiles else []


def is_confident(candidates: List[Dict[str, Any]], reason: str) -> bool:
    """Unique names and friend links are strong evidence. Hero history counts only when the winner has
    at least twice the runner-up's games on this hero and HERO_LEAD_GAMES more (55 vs 7 yes, 8 vs 5 no,
    2 vs 1 no: on a few games each, twice as many is luck, and it once picked the wrong account)."""
    if "corrected_name" in candidates[0]:
        return False  # the name itself was a guess at an OCR misread
    if len(candidates) == 1 or reason.startswith("friends with"):
        return True
    games = sorted((c["current_hero_matches"] for c in candidates), reverse=True)
    return games[0] >= 2 * games[1] and games[0] - games[1] >= HERO_LEAD_GAMES


HERO_LEAD_GAMES = 5  # the real calls seen: 25 vs 7 and 74 vs 12 right and sure; 2 vs 1 wrong


def fetch_ranks(account_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """{account_id: {"name", "color", "badge", "as_of"}} from one batch request ({} if it fails). as_of: when
    their last ranked match started (the rank is the one after it), or None."""
    if not account_ids:
        return {}
    try:
        tiers = deadlock_api.fetch_rank_tiers()
        entries = deadlock_api.get_player_ranks(account_ids)
    except Exception as e:
        logger.warning(f"Could not load ranks ({e})")
        return {}
    ranks = {}
    for entry in entries:
        tier = tiers.get(entry["rank"])
        if tier:
            # Tier 0 (Obscurus) means no recent ranked games. "badge" (tier * 10 + subrank) lets ranks be compared
            name = "Unranked" if entry["rank"] == 0 else f"{tier['name']} {entry['subrank']}"
            ranks[entry["account_id"]] = {"name": name, "color": tier["color"],
                                          "badge": entry["rank"] * 10 + entry["subrank"],
                                          "as_of": (entry.get("last_match") or {}).get("start_time")}
    return ranks


def apply_ranks(results: List[Dict[str, Any]], ranks: Dict[int, Dict[str, Any]]) -> None:
    for result in results:
        if result.get("account_id") in ranks:
            result["rank"] = ranks[result["account_id"]]


def attach_ranks(results: List[Dict[str, Any]]) -> None:
    """Add ranks to found players, using one batch request."""
    apply_ranks(results, fetch_ranks([r["account_id"] for r in results if r.get("account_id")]))


def hero_maps() -> Tuple[Dict[str, int], Dict[int, str]]:
    """(hero ids by name, hero names by id). One hero list feeds both OCR (names) and stats (ids)."""
    try:
        heroes = deadlock_api.fetch_heroes()
    except Exception as e:
        logger.warning(f"Could not load heroes from the API ({e}); player lookups will fail")
        heroes = [{"id": None, "name": name} for name in FALLBACK_HERO_NAMES]
    return {h["name"]: h["id"] for h in heroes}, {h["id"]: h["name"] for h in heroes}


def read_lobby(file_path: str, progress: Progress = None) -> List[Dict[str, str]]:
    """Step 1, OCR only (no player lookups): the {"player", "hero", "team"} records on screen."""
    report_progress(progress, "Reading the scoreboard...")
    return read_scoreboard(file_path, list(hero_maps()[0]))


def analyze_records(records: List[Dict[str, str]], progress: Progress = None,
                    me: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
    """Step 2: look up every player found on the scoreboard -> (results, parties)."""
    if not records:
        return [], []
    hero_ids_by_name, hero_names_by_id = hero_maps()
    return lookup_lobby(records, hero_ids_by_name, hero_names_by_id, progress, me)


def analyze_screenshot(file_path: str, progress: Progress = None,
                       me: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
    """The whole pipeline: screenshot -> (results, parties). Used by the terminal and the app."""
    return analyze_records(read_lobby(file_path, progress), progress, me)


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
