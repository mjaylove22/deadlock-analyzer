"""Post-game review: a finished match's scoreboard, net-worth lead, your build, and how your game
compared with the lobby and with your usual on that hero.

The raw match data is ~1.5 MB, so it's condensed into a small summary straight away. Finished
matches never change, so summaries are also kept on disk (cache/matches, newest 50): reopening a
match costs nothing. That also matters for rate limits: matches the API hasn't stored yet are
fetched from Steam, which allows only 3 requests an hour.
"""

import json
import math
import os
import threading
import time
import urllib.error
from typing import Any, Dict, List, Optional

import deadlock_api
import paths
from performance import band_for_badge, length_window, rate_player
from player_lookup import fetch_ranks
from profiles import GAME_MODES, RANK_BANDS

CACHE_DIR = paths.data("cache", "matches")
MAX_SAVED_MATCHES = 50
RETRY_AFTER_S = 600  # a match that failed isn't asked for again for 10 minutes (3 Steam fetches/hour)
STEAM_FETCHES_PER_HOUR = 3  # the API's limit (per IP) for matches it has to fetch from Steam

_failed: Dict[int, tuple] = {}  # match_id -> (time, error message)
_save_lock = threading.Lock()
_steam_fetches: List[float] = []  # when this app last asked for matches from Steam


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
            "damage": final.get("player_damage", 0), "healing": final.get("player_healing", 0),  # self + allies
            "damage_taken": final.get("player_damage_taken", 0),
            # For the performance ratings: objective damage, accuracy and crit rate
            "boss_damage": final.get("boss_damage", 0),
            "shots_hit": final.get("shots_hit", 0), "shots_missed": final.get("shots_missed", 0),
            "crits": final.get("hero_bullets_hit_crit", 0), "hero_hits": final.get("hero_bullets_hit", 0),
            # The final build: shop items still owned at the end, in the order they were bought
            "items": [{"id": i["item_id"], "name": items_by_id[i["item_id"]]["name"], "slot": items_by_id[i["item_id"]].get("slot")}
                      for i in sorted(p.get("items", []), key=lambda i: i["game_time_s"])
                      if i["item_id"] in items_by_id and not i.get("sold_time_s")],
            **coach_facts(info, p, hero_names_by_id),
        })
    return {
        "match_id": info["match_id"], "start_time": info["start_time"],
        "minutes": info["duration_s"] / 60, "mode": GAME_MODES.get(info["game_mode"], "Other"),
        "ranked": info.get("match_mode") == 4, "winning_team": info["winning_team"],
        "team_badges": [info.get("average_badge_team0", 0), info.get("average_badge_team1", 0)],
        "players": players,
        "networth_lead": networth_lead(info["players"]),
    }


LANE_END_S = 540  # laning is judged at the 9-minute snapshot (snapshots: 3, 6, 9, 12, 15 min, then every 5)
PATH_CLOCK_LEAD_S = 15  # match_paths runs a few seconds ahead of game time (3-8 s seen; pauses?)


def coach_facts(info: Dict[str, Any], p: Dict[str, Any], hero_names_by_id: Dict[int, str]) -> Dict[str, Any]:
    """What the Coach tab needs from one player's raw data: each death (when, where, by whom, how long the
    fight took, how far the nearest living teammate was), laning at 9 minutes, and where their souls came from."""
    players = {q.get("player_slot"): q for q in info["players"]}
    mp = info.get("match_paths") or {}
    paths_by_slot = {path["player_slot"]: path for path in mp.get("paths", [])}

    def position(slot, i):
        path = paths_by_slot[slot]
        i = min(i, len(path["x_pos"]) - 1)
        return (path["x_min"] + path["x_pos"][i] / mp["x_resolution"] * (path["x_max"] - path["x_min"]),
                path["y_min"] + path["y_pos"][i] / mp["y_resolution"] * (path["y_max"] - path["y_min"]))

    def dead_at(slot, t):
        return any(d["game_time_s"] <= t < d["game_time_s"] + d["death_duration_s"] for d in players[slot].get("death_details", []))

    deaths = []
    for d in p.get("death_details", []):
        t, spot = d["game_time_s"], (d["death_pos"]["x"], d["death_pos"]["y"])
        mate = None
        if p.get("player_slot") in paths_by_slot and mp.get("interval_s") == 1:
            # The path sample where they died: the path clock runs ahead of game time, so search just after t
            at = min(range(t, t + PATH_CLOCK_LEAD_S + 1), key=lambda i: math.dist(position(p["player_slot"], i), spot))
            alive = [s for s, q in players.items() if q["team"] == p["team"] and s != p["player_slot"]
                     and s in paths_by_slot and not dead_at(s, t)]
            mate = round(min(math.dist(spot, position(s, at)) for s in alive)) if alive else None
        killer = players.get(d.get("killer_player_slot"))
        deaths.append({"t": t, "x": round(spot[0]), "y": round(spot[1]), "fight_s": round(d.get("time_to_kill_s", 0), 1),
                       "killer": hero_names_by_id.get(killer["hero_id"]) if killer else None, "mate": mate})
    lane = next((s for s in p.get("stats", []) if s["time_stamp_s"] >= LANE_END_S), None)
    final = p["stats"][-1] if p.get("stats") else {}
    both = lambda key: final.get(key, 0) + final.get(key + "_orbs", 0)  # noqa: E731 (souls picked up as orbs count too)
    return {
        "death_list": deaths,
        "lane": {"minute": lane["time_stamp_s"] // 60, "last_hits": lane.get("creep_kills", 0), "possible": lane.get("possible_creeps", 0),
                 "denies": lane.get("denies", 0), "net_worth": lane.get("net_worth", 0)} if lane else None,
        "sources": {"kills": both("gold_player"), "lane": both("gold_lane_creep"), "jungle": both("gold_neutral_creep"),
                    "objectives": both("gold_boss"), "other": final.get("gold_treasure", 0) + final.get("gold_denied", 0)},
        "souls_lost": final.get("gold_death_loss", 0),
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
    """Keep a summary on disk, and only the newest MAX_SAVED_MATCHES, so the cache stays small. Thread-safe:
    the Coach saves many at once, and one thread pruning a file another was sorting by lost that match."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    with _save_lock:
        with open(os.path.join(CACHE_DIR, f"{summary['match_id']}.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f)
        saved = sorted((os.path.join(CACHE_DIR, name) for name in os.listdir(CACHE_DIR)), key=os.path.getmtime)
        for old in saved[:-MAX_SAVED_MATCHES]:
            try:
                os.remove(old)
            except FileNotFoundError:  # already gone (e.g. two app windows): nothing to do
                pass


def load_saved(match_id: int) -> Optional[Dict[str, Any]]:
    path = os.path.join(CACHE_DIR, f"{match_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def steam_fetches_left(now: Optional[float] = None) -> int:
    """How many matches can still be fetched from Steam this hour."""
    now = time.time() if now is None else now
    _steam_fetches[:] = [t for t in _steam_fetches if now - t < 3600]
    return STEAM_FETCHES_PER_HOUR - len(_steam_fetches)


def fetch_metadata(match_id: int, allow_steam: bool = True) -> Dict[str, Any]:
    """A match's raw data. The API's stored copy is tried first: it's free (100 requests per 10 s),
    while a match it has to fetch from Steam counts against 3 an hour. Raises OSError for HTTP and
    network errors, or MatchUnavailable when this hour's Steam fetches are used up.

    max_age=0: the raw ~1.5 MB response isn't kept in the memory cache; the summary is kept instead."""
    path = f"/v1/matches/{match_id}/metadata"
    try:
        return deadlock_api.get_json(path, {"disable_steam": "true"}, max_age=0)
    except urllib.error.HTTPError as e:
        if e.code != 404 or not allow_steam:  # 404: not stored (yet)
            raise
    if steam_fetches_left() <= 0:
        raise MatchUnavailable("This match isn't stored yet, and this hour's fetches from Steam (3 an hour) "
                               "are used up. Try again later.")
    _steam_fetches.append(time.time())
    return deadlock_api.get_json(path, max_age=0)


def get_summary(match_id: int, hero_names_by_id: Dict[int, str], allow_steam: bool = True) -> Dict[str, Any]:
    """The match's summary: saved on disk, or fetched, condensed and saved. Raises MatchUnavailable."""
    summary = load_saved(match_id)
    if summary is None or "death_list" not in summary["players"][0]:  # saved before the Coach tab: fetch again once
        try:
            metadata = fetch_metadata(match_id, allow_steam)
        except OSError as e:  # HTTP errors and network problems
            if summary:
                return summary  # the old summary still serves the review
            raise MatchUnavailable(explain(e)) from e
        summary = summarize(metadata, hero_names_by_id, deadlock_api.fetch_items())
        save(summary)
    return summary


def match_review(match_id: int, hero_names_by_id: Dict[int, str], me: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the review page needs (worker thread). Raises if the match isn't available yet."""
    summary = load_saved(match_id)
    if summary is None:
        failed_at, message = _failed.get(match_id, (0, ""))
        if time.time() - failed_at < RETRY_AFTER_S:
            raise MatchUnavailable(message)  # asked recently and it failed: don't spend another request
        try:
            summary = get_summary(match_id, hero_names_by_id)
        except MatchUnavailable as e:
            _failed[match_id] = (time.time(), str(e))
            raise

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


NOTABLE_VS_USUAL = 0.10  # one match this far from your usual is worth a mention
STORY_STATS = {"net_worth": "souls", "damage": "damage", "healing": "healing", "last_hits": "last hits"}


def souls(amount: float) -> str:
    return f"{abs(amount) / 1000:.0f}k souls" if abs(amount) >= 1000 else f"{abs(amount):,.0f} souls"


def story(review: Dict[str, Any]) -> List[str]:
    """A few sentences about the match from the net-worth lead and, if you played, your numbers:
    the result, when the lead changed hands for the last time, the biggest swing, and how you did
    against your usual on that hero. Told from your side, or the winners' if you weren't in it."""
    me, lead = review.get("me"), review["networth_lead"]
    side = me["team"] if me else review["winning_team"]
    ours = [(minute, value if side == 0 else -value) for minute, value in lead]  # + = our side ahead
    we, they = ("Your team", "the enemy") if me else ("The winners", "the other team")
    lines = []
    if ours:
        final = ours[-1][1]
        if me:
            lines.append(f"You {'won' if me['won'] else 'lost'}, {souls(final)} {'ahead' if final > 0 else 'behind'} at the end.")
        else:
            lines.append(f"The winners finished {souls(final)} {'ahead' if final > 0 else 'behind'}.")
        changes = [n for n in range(1, len(ours)) if (ours[n][1] > 0) != (ours[n - 1][1] > 0)]
        if not changes:
            lines.append(f"{we} {'led' if final > 0 else 'trailed'} from start to finish.")
        else:
            taker = we if final > 0 else they.capitalize()
            turning = f"{taker} took the lead for good at minute {ours[changes[-1]][0]:.0f}."
            if len(ours) > 1:
                n = max(range(1, len(ours)), key=lambda n: abs(ours[n][1] - ours[n - 1][1]))
                swing = ours[n][1] - ours[n - 1][1]
                turning += (f" The biggest swing was {souls(swing)} toward {we.lower() if swing > 0 else they}, "
                            f"between minutes {ours[n - 1][0]:.0f} and {ours[n][0]:.0f}.")
            lines.append(turning)
    if me:
        notable = sorted(((key, v) for key, v in review.get("vs_usual", {}).items()
                          if v is not None and abs(v) >= NOTABLE_VS_USUAL), key=lambda kv: -abs(kv[1]))[:2]
        if notable:
            lines.append(f"Against your usual {me['hero']}: " + ", ".join(
                f"{STORY_STATS[key]} {abs(v):.0%} {'higher' if v > 0 else 'lower'}" for key, v in notable) + ".")
        best = [STORY_STATS[key] for key in STORY_STATS if review.get("places", {}).get(key) == 1]
        if best:
            lines.append("Most " + " and ".join(best) + " in the lobby.")
    return lines


def match_badge(review: Dict[str, Any]) -> Optional[float]:
    """The match's average rank (badge = tier * 10 + subrank). Matches often come without it, so then
    the players' current ranks are averaged instead (one batch request; unranked players skipped)."""
    badges = [b for b in review["team_badges"] if b]
    if not badges:
        ranks = fetch_ranks([p["account_id"] for p in review["players"] if p["account_id"]])
        badges = [r["badge"] for r in ranks.values() if r["badge"] >= 10]  # tier 0: no recent ranked games
    return sum(badges) / len(badges) if badges else None


def rate_match(review: Dict[str, Any], hero_names_by_id: Dict[int, str]) -> Dict[str, Any]:
    """Every player's performance on their hero (worker thread): {"ratings": one per player, in
    review["players"] order (None when their hero's numbers didn't load), "band", "window"}.
    One ~10 KB request per hero, all at once; the same hero, rank and length reuse the answer."""
    street_brawl = review["mode"] == "Street Brawl"
    band = band_for_badge(None if street_brawl else match_badge(review), RANK_BANDS)  # no rank filter in Street Brawl
    low, high = length_window(review["minutes"])
    ids_by_name = {name: hero_id for hero_id, name in hero_names_by_id.items()}
    heroes = sorted({p["hero"] for p in review["players"] if p["hero"] in ids_by_name})
    game_mode = "street_brawl" if street_brawl else "normal"
    answers = deadlock_api.parallel(
        *[lambda hero=hero: deadlock_api.get_player_metrics(ids_by_name[hero], game_mode, band[1], low, high)
          for hero in heroes], allow_failures=True)
    metrics = dict(zip(heroes, answers))
    ratings = [rate_player(p, review["minutes"], metrics[p["hero"]]) if metrics.get(p["hero"]) else None
               for p in review["players"]]
    return {"ratings": ratings, "band": band[0], "window": (low, high)}
