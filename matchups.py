"""Your matchup: how your hero does against this enemy team, and what people buy against it.

Win rates come from the API's match data across all skill levels. Items are ranked by how often
they're bought against this team, not by win rate: an item's win rate is inflated when mostly
players who are already winning can afford it, so expensive late items would always look best.
"""

import logging
from typing import Any, Dict, List, Optional

import deadlock_api

logger = logging.getLogger(__name__)

MIN_MATCHUP_GAMES = 100  # below this, a hero-vs-hero win rate is mostly noise
ITEMS_SHOWN = 6


def average_win_rate(counter_stats: List[Dict], hero_id: int) -> float:
    """A hero's win rate across all its matchups. A strong hero wins most matchups, so each
    matchup is judged against this, not against 50%."""
    mine = [s for s in counter_stats if s["hero_id"] == hero_id]
    games = sum(s["matches_played"] for s in mine)
    return sum(s["wins"] for s in mine) / games if games else 0.5


def hero_matchups(counter_stats: List[Dict], my_hero_id: int, enemy_hero_ids: List[int]) -> List[Dict[str, Any]]:
    """Your hero's win rate against each enemy hero, toughest first, with "vs_average": the
    difference from your hero's average win rate (negative = harder than usual)."""
    average = average_win_rate(counter_stats, my_hero_id)
    pairs = {(s["hero_id"], s["enemy_hero_id"]): s for s in counter_stats}
    matchups = []
    for enemy in dict.fromkeys(enemy_hero_ids):  # each enemy hero once, in order
        s = pairs.get((my_hero_id, enemy))
        if s and s["matches_played"] >= MIN_MATCHUP_GAMES:
            win_rate = s["wins"] / s["matches_played"]
            matchups.append({"enemy_hero_id": enemy, "win_rate": win_rate, "vs_average": win_rate - average,
                             "games": s["matches_played"]})
    return sorted(matchups, key=lambda m: m["win_rate"])


def popular_items(item_stats: List[Dict], items_by_id: Dict[int, Dict], count: int = ITEMS_SHOWN) -> List[Dict[str, Any]]:
    """The most-bought shop items (name, slot, tier, cost, image), with their win rate."""
    bought = [s for s in item_stats if s["item_id"] in items_by_id and s["matches"] > 0]
    bought.sort(key=lambda s: s["matches"], reverse=True)
    return [dict(items_by_id[s["item_id"]], win_rate=s["wins"] / s["matches"], matches=s["matches"])
            for s in bought[:count]]


def hero_breakdown(hero_id: int, hero_names_by_id: Dict[int, str], game_mode: str = "normal",
                   ranks: tuple = None, shown: int = 6) -> Dict[str, Any]:
    """For the hero page: the hero's best and toughest matchups (against its own average) and its
    most-bought items. Two requests, made at the same time. If one fails, its part is None and the
    other still comes back."""
    counters, item_stats = deadlock_api.parallel(
        lambda: deadlock_api.fetch_counter_stats(game_mode, ranks),
        lambda: deadlock_api.get_item_stats(hero_id, (), game_mode, ranks), allow_failures=True)
    breakdown = {"average_win_rate": None, "toughest": None, "best": None, "items": None}
    if counters is not None:
        others = [h for h in hero_names_by_id if h != hero_id]
        named = [dict(m, enemy_hero=hero_names_by_id[m["enemy_hero_id"]]) for m in hero_matchups(counters, hero_id, others)]
        breakdown.update(average_win_rate=average_win_rate(counters, hero_id),
                         toughest=named[:shown],  # hero_matchups sorts toughest first
                         best=list(reversed(named[-shown:])) if len(named) > shown else [])
    if item_stats is not None:
        breakdown["items"] = popular_items(item_stats, deadlock_api.fetch_items(), count=10)
    return breakdown


MIN_ITEM_GAMES = 500     # an item needs this many games in these matches to be judged (fewer: luck dominates)
COUNTER_ITEMS_SHOWN = 3
TEAM_ITEMS_SHOWN = 6


def lobby_mode(results: List[Dict[str, Any]]) -> str:
    """ "street_brawl" for 4v4 lobbies, else "normal" (the stats endpoints' names)."""
    sizes = [sum(1 for r in results if r["team"] == team) for team in {r["team"] for r in results}]
    return "street_brawl" if sizes and max(sizes) <= 4 else "normal"


def expected_win_rate(average: float, matchups: List[Dict[str, Any]]) -> float:
    """A rough read of the whole matchup: the hero's average plus the mean of how each enemy hero
    shifts it. Not a prediction (players matter more than heroes), but it sums up the picture."""
    return average + (sum(m["vs_average"] for m in matchups) / len(matchups) if matchups else 0.0)


def item_lift(here: Optional[List[Dict]], usual: Optional[List[Dict]], items_by_id: Dict[int, Dict],
              matchup_shift: float, games: Optional[int] = None, count: int = COUNTER_ITEMS_SHOWN) -> List[Dict[str, Any]]:
    """Items that do better than usual in these matches. "Win rate when bought" favours expensive
    late items (only games that last get to buy them), so each item is compared with itself: its win
    rate here minus its win rate in all of the hero's games, minus how much the matchup moves every
    win rate (a hard matchup lowers them all). What's left is what the item adds against these enemies."""
    if not here or not usual:
        return []
    base = {s["item_id"]: s for s in usual}
    lifted = []
    for s in here:
        b = base.get(s["item_id"])
        if s["item_id"] not in items_by_id or not b or s["matches"] < MIN_ITEM_GAMES or b["matches"] < MIN_ITEM_GAMES:
            continue
        win_rate, usual_rate = s["wins"] / s["matches"], b["wins"] / b["matches"]
        lift = win_rate - usual_rate - matchup_shift
        if lift > 0:
            lifted.append(dict(items_by_id[s["item_id"]], win_rate=win_rate, usual_win_rate=usual_rate, lift=lift,
                               matches=s["matches"], bought_share=s["matches"] / games if games else None))
    return sorted(lifted, key=lambda i: i["lift"], reverse=True)[:count]


def per_game(rows: List[Dict], *keys: str) -> Optional[tuple]:
    games = sum(r["matches_played"] for r in rows)
    return tuple(sum(r[k] for r in rows) / games for k in keys) if games else None


def matchup_details(my_hero_id: int, enemy_ids: List[int], ally_ids: List[int], game_mode: str = "normal") -> Dict[str, Any]:
    """Everything for the matchup page, from requests made at the same time (each reused for an hour).
    Parts that fail to load are left empty; only the main matchup stats are required."""
    counters, lane, synergy, usual_items, team_items, *enemy_items = deadlock_api.parallel(
        lambda: deadlock_api.fetch_counter_stats(game_mode),
        lambda: deadlock_api.fetch_counter_stats(game_mode, same_lane=True),
        lambda: deadlock_api.get_synergy_stats(game_mode),
        lambda: deadlock_api.get_item_stats(my_hero_id, (), game_mode),
        lambda: deadlock_api.get_item_stats(my_hero_id, enemy_ids, game_mode),
        *[lambda e=e: deadlock_api.get_item_stats(my_hero_id, [e], game_mode) for e in enemy_ids],
        allow_failures=True)
    if counters is None:
        raise OSError("the matchup stats didn't load (the stats server is slow or busy)")
    items_by_id = deadlock_api.fetch_items()
    average = average_win_rate(counters, my_hero_id)
    mine = [s for s in counters if s["hero_id"] == my_hero_id]
    pairs = {s["enemy_hero_id"]: s for s in mine}
    lane_pairs = {s["enemy_hero_id"]: s for s in lane or [] if s["hero_id"] == my_hero_id}

    enemies = []
    for enemy, items in zip(enemy_ids, enemy_items):
        s = pairs.get(enemy)
        if not s or s["matches_played"] < MIN_MATCHUP_GAMES:
            enemies.append({"hero_id": enemy, "games": s["matches_played"] if s else 0, "win_rate": None})
            continue
        win_rate = s["wins"] / s["matches_played"]
        l = lane_pairs.get(enemy)
        laned = l and l["matches_played"] >= MIN_MATCHUP_GAMES
        enemies.append({
            "hero_id": enemy, "games": s["matches_played"], "win_rate": win_rate, "vs_average": win_rate - average,
            "lane_win_rate": l["wins"] / l["matches_played"] if laned else None,
            "lane_vs_average": l["wins"] / l["matches_played"] - average if laned else None,
            "lane_games": l["matches_played"] if laned else 0,
            "kda": per_game([s], "kills", "deaths", "assists"),
            "souls": per_game([s], "networth", "enemy_networth"),
            "counter_items": item_lift(items, usual_items, items_by_id, win_rate - average, s["matches_played"]),
        })
    judged = [e for e in enemies if e["win_rate"] is not None]
    enemies.sort(key=lambda e: e["vs_average"] if e["win_rate"] is not None else 0)  # toughest first

    allies = []
    for ally in ally_ids:
        row = next((r for r in synergy or [] if {r["hero_id1"], r["hero_id2"]} == {my_hero_id, ally}), None)
        if row and row["matches_played"] >= MIN_MATCHUP_GAMES:
            win_rate = row["wins"] / row["matches_played"]
            allies.append({"hero_id": ally, "win_rate": win_rate, "vs_average": win_rate - average, "games": row["matches_played"]})
    allies.sort(key=lambda a: a["vs_average"], reverse=True)

    shift = expected_win_rate(average, judged) - average
    return {"average_win_rate": average, "expected": expected_win_rate(average, judged), "enemies": enemies,
            "allies": allies, "usual_kda": per_game(mine, "kills", "deaths", "assists"),
            "team_items": item_lift(team_items, usual_items, items_by_id, shift, count=TEAM_ITEMS_SHOWN),
            "lane_loaded": lane is not None, "game_mode": game_mode}


def build_matchup(results: List[Dict[str, Any]], hero_ids_by_name: Dict[str, int],
                  hero_names_by_id: Dict[int, str]) -> Optional[Dict[str, Any]]:
    """Matchup data for the user (the result marked is_me), or None if they aren't in the lobby."""
    me = next((r for r in results if r.get("is_me")), None)
    if not me or me["hero"] not in hero_ids_by_name:
        return None
    my_hero = hero_ids_by_name[me["hero"]]
    # The user is on "MY TEAM", so the other team is the enemy, whatever its rows were labelled
    enemy_heroes = [hero_ids_by_name[r["hero"]] for r in results
                    if r["team"] != me["team"] and r["hero"] in hero_ids_by_name]
    if not enemy_heroes:
        return None

    mode = lobby_mode(results)
    counters = deadlock_api.fetch_counter_stats(mode)
    matchups = hero_matchups(counters, my_hero, enemy_heroes)
    items = popular_items(deadlock_api.get_item_stats(my_hero, enemy_heroes, mode), deadlock_api.fetch_items())
    average = average_win_rate(counters, my_hero)
    return {
        "hero": me["hero"],
        "average_win_rate": average,
        "expected": expected_win_rate(average, matchups),
        "game_mode": mode,
        "matchups": [dict(m, enemy_hero=hero_names_by_id.get(m["enemy_hero_id"], "?")) for m in matchups],
        "items": items,
    }
