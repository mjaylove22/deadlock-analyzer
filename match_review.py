"""Post-game review: a finished match's scoreboard, net-worth lead, your build, and how your game
compared with the lobby and with your usual on that hero.

The raw match data is ~1.5 MB, so it's condensed into a small summary straight away. Finished
matches never change, so summaries are also kept on disk (cache/matches, newest 50): reopening a
match costs nothing. That also matters for rate limits: matches the API hasn't stored yet are
fetched from Steam, which allows only 3 requests an hour.
"""

import json
import os
import time
import urllib.error
from typing import Any, Dict, List, Optional

import deadlock_api
from profiles import GAME_MODES

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "matches")
MAX_SAVED_MATCHES = 50
RETRY_AFTER_S = 600  # a match that failed isn't asked for again for 10 minutes (3 Steam fetches/hour)

_failed: Dict[int, tuple] = {}  # match_id -> (time, error message)


class MatchUnavailable(Exception):
    """A match the API can't provide (yet), with a message fit to show the user."""


def explain(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError) and error.code == 429:
        return ("Too many requests for matches the API hasn't stored yet (it allows 3 an hour). "
                "Try again later.")
    # 404, or 503 while the API is still fetching it (seen for a just-finished match: 503, then 404)
    if isinstance(error, urllib.error.HTTPError) and error.code in (404, 503):
        return "This match isn't available yet. Matches appear a while after they end, and bot matches aren't recorded."
    return f"Couldn't load this match ({error})."

# (label, key in a player summary, per minute?) for the "your game" comparison
REVIEW_STATS = [
    ("Net worth", "net_worth", True),
    ("Damage", "damage", True),
    ("Healing", "healing", True),
    ("Last hits", "last_hits", True),
]


def summarize(metadata: Dict[str, Any], hero_names_by_id: Dict[int, str],
              items_by_id: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Condense raw match metadata into what the review page shows (a few KB instead of ~1.5 MB)."""
    info = metadata.get("match_info", metadata)
    players = []
    for p in info["players"]:
        final = p["stats"][-1] if p.get("stats") else {}
        players.append({
            "account_id": p["account_id"], "team": p["team"], "won": p["team"] == info["winning_team"],
            "hero": hero_names_by_id.get(p["hero_id"], f"hero #{p['hero_id']}"),
            "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
            "net_worth": p["net_worth"], "last_hits": p.get("last_hits", 0), "denies": p.get("denies", 0),
            "level": p.get("level", 0),
            "damage": final.get("player_damage", 0), "healing": final.get("player_healing", 0),
            "damage_taken": final.get("player_damage_taken", 0),
            # The final build: shop items still owned at the end, in the order they were bought
            "items": [{"name": items_by_id[i["item_id"]]["name"], "slot": items_by_id[i["item_id"]].get("slot")}
                      for i in sorted(p.get("items", []), key=lambda i: i["game_time_s"])
                      if i["item_id"] in items_by_id and not i.get("sold_time_s")],
        })
    return {
        "match_id": info["match_id"], "start_time": info["start_time"],
        "minutes": info["duration_s"] / 60, "mode": GAME_MODES.get(info["game_mode"], "Other"),
        "ranked": info.get("match_mode") == 4, "winning_team": info["winning_team"],
        "team_badges": [info.get("average_badge_team0", 0), info.get("average_badge_team1", 0)],
        "players": players,
        "networth_lead": networth_lead(info["players"]),
    }


def networth_lead(players: List[Dict[str, Any]]) -> List[List[float]]:
    """[minute, team 0's total net worth minus team 1's] at each stats snapshot."""
    by_time: Dict[int, List[float]] = {}
    for p in players:
        for snap in p.get("stats", []):
            totals = by_time.setdefault(snap["time_stamp_s"], [0.0, 0.0])
            totals[p["team"]] += snap.get("net_worth", 0)
    return [[time / 60, team0 - team1] for time, (team0, team1) in sorted(by_time.items())]


def lobby_place(players: List[Dict[str, Any]], player: Dict[str, Any], key: str) -> int:
    """1 = the highest value of key in the lobby."""
    return 1 + sum(1 for p in players if p[key] > player[key])


def kda(p: Dict[str, Any]) -> float:
    return (p["kills"] + p["assists"]) / max(p["deaths"], 1)


def compare_to_usual(player: Dict[str, Any], minutes: float, hero_entry: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """For each review stat: this match per minute vs the player's average per minute on this hero,
    as a fraction (+0.12 = 12% better). None when there's no history to compare with."""
    if not hero_entry or not minutes:
        return {key: None for _, key, _ in REVIEW_STATS}
    usual = {"net_worth": hero_entry.get("networth_per_min"), "damage": hero_entry.get("damage_per_min"),
             "last_hits": hero_entry.get("last_hits_per_min"),
             "healing": None}  # hero stats have no healing average
    result = {}
    for _, key, _ in REVIEW_STATS:
        average = usual.get(key)
        result[key] = (player[key] / minutes) / average - 1 if average else None
    return result


def save(summary: Dict[str, Any]) -> None:
    """Keep a summary on disk, and only the newest MAX_SAVED_MATCHES, so the cache stays small."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, f"{summary['match_id']}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f)
    saved = sorted((os.path.join(CACHE_DIR, name) for name in os.listdir(CACHE_DIR)), key=os.path.getmtime)
    for old in saved[:-MAX_SAVED_MATCHES]:
        os.remove(old)


def load_saved(match_id: int) -> Optional[Dict[str, Any]]:
    path = os.path.join(CACHE_DIR, f"{match_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def match_review(match_id: int, hero_names_by_id: Dict[int, str], me: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the review page needs (worker thread). Raises if the match isn't available yet."""
    summary = load_saved(match_id)
    if summary is None:
        failed_at, message = _failed.get(match_id, (0, ""))
        if time.time() - failed_at < RETRY_AFTER_S:
            raise MatchUnavailable(message)  # asked recently and it failed: don't spend another request
        try:
            # max_age=0: don't keep the raw 1.5 MB response in the memory cache; the summary is kept instead
            metadata = deadlock_api.get_json(f"/v1/matches/{match_id}/metadata", max_age=0)
        except OSError as e:  # HTTP errors and network problems
            _failed[match_id] = (time.time(), explain(e))
            raise MatchUnavailable(explain(e)) from e
        summary = summarize(metadata, hero_names_by_id, deadlock_api.fetch_items())
        save(summary)

    ids = [p["account_id"] for p in summary["players"] if p["account_id"]]
    my_player = next((p for p in summary["players"] if me and p["account_id"] == me["account_id"]), None)
    hero_id = next((i for i, n in hero_names_by_id.items() if my_player and n == my_player["hero"]), None)
    profiles, my_heroes = deadlock_api.parallel(
        lambda: deadlock_api.get_profiles(ids),
        lambda: deadlock_api.get_hero_stats([me["account_id"]], "street_brawl" if summary["mode"] == "Street Brawl" else "normal")
        if my_player else [])
    by_id = {p["account_id"]: p for p in profiles}
    for p in summary["players"]:
        profile = by_id.get(p["account_id"], {})
        p["name"] = profile.get("personaname", "Unknown player")
        p["avatar_url"] = profile.get("avatarmedium")
        p["kda"] = kda(p)
    review = dict(summary, me=my_player)
    if my_player:
        hero_entry = next((e for e in my_heroes if e["hero_id"] == hero_id), None)
        review["vs_usual"] = compare_to_usual(my_player, summary["minutes"], hero_entry)
        review["places"] = {key: lobby_place(summary["players"], my_player, key)
                            for key in ("kda", "net_worth", "damage", "healing", "last_hits")}
    return review
