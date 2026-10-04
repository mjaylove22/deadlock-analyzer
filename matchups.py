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

    counters = deadlock_api.fetch_counter_stats()
    matchups = hero_matchups(counters, my_hero, enemy_heroes)
    items = popular_items(deadlock_api.get_item_stats(my_hero, enemy_heroes), deadlock_api.fetch_items())
    return {
        "hero": me["hero"],
        "average_win_rate": average_win_rate(counters, my_hero),
        "matchups": [dict(m, enemy_hero=hero_names_by_id.get(m["enemy_hero_id"], "?")) for m in matchups],
        "items": items,
    }
