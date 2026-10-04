"""Minimal client for the public Deadlock API (https://api.deadlock-api.com).

Only read-only, public endpoints are used. Endpoint shapes were checked against
the API's OpenAPI spec (https://api.deadlock-api.com/openapi.json) and real responses.
"""

import functools
import json
import urllib.parse
import urllib.request
from typing import Any, Dict, List

BASE_URL = "https://api.deadlock-api.com"
TIMEOUT_SECONDS = 10


def get_json(path: str, params: Dict[str, Any] = None) -> Any:
    """GET an API path and return the decoded JSON body."""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "deadlock-analyzer (learning project)"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


@functools.lru_cache(maxsize=None)  # rarely changes: fetch once per run (failures aren't cached)
def fetch_heroes() -> List[Dict[str, Any]]:
    """Return heroes a player can actually pick right now, as {"id", "name"} dicts."""
    heroes = get_json("/v1/assets/heroes")
    return [
        {"id": hero["id"], "name": hero["name"]}
        for hero in heroes
        if hero["player_selectable"] and not hero["disabled"] and not hero["in_development"]
    ]


def search_steam_profiles(name: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search Steam profiles by display name. Results are ranked by name similarity and activity.

    The API hides accounts with fewer than 5 recorded matches in the last 30 days by default.
    Bot matches aren't recorded, so that filter can hide the very player we're looking for;
    it is turned off here, and same-named accounts are told apart by hero history instead.
    """
    return get_json("/v1/players/steam-search",
                    {"search_query": name, "limit": limit, "min_matches_played_last_30d": 0})


def get_hero_stats(account_ids: List[int], game_mode: str = "normal") -> List[Dict[str, Any]]:
    """Per-hero stats for one or more accounts (one entry per account+hero pair).
    game_mode: "normal" (the API default) or "street_brawl"."""
    return get_json("/v1/players/hero-stats",
                    {"account_ids": ",".join(str(a) for a in account_ids), "game_mode": game_mode})


def get_match_history(account_id: int) -> List[Dict[str, Any]]:
    """A player's recorded matches (not guaranteed newest first; sort by start_time)."""
    return get_json(f"/v1/players/{account_id}/match-history")


def get_global_hero_stats(game_mode: str = "normal") -> List[Dict[str, Any]]:
    """Every hero's totals across all recorded matches: {"hero_id", "matches", "losses", "total_kills", ...}."""
    return get_json("/v1/analytics/hero-stats", {"game_mode": game_mode})


@functools.lru_cache(maxsize=None)
def fetch_hero_assets() -> List[Dict[str, Any]]:
    """Full hero assets (images, colours) for playable heroes."""
    return [h for h in get_json("/v1/assets/heroes")
            if h["player_selectable"] and not h["disabled"] and not h["in_development"]]


@functools.lru_cache(maxsize=None)
def fetch_rank_assets() -> List[Dict[str, Any]]:
    """Full rank assets (tier, name, colour, emblem images)."""
    return get_json("/v1/assets/ranks")


@functools.lru_cache(maxsize=None)
def fetch_rank_tiers() -> Dict[int, Dict[str, str]]:
    """Rank tier number -> {"name", "color"}, e.g. 7 -> Emissary. Tier 0 (Obscurus) means unranked."""
    return {tier["tier"]: {"name": tier["name"], "color": tier["color"]} for tier in get_json("/v1/assets/ranks")}


def get_profiles(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Steam profiles (same shape as search results) for specific accounts."""
    return get_json("/v1/players/steam", {"account_ids": ",".join(str(a) for a in account_ids)})


@functools.lru_cache(maxsize=None)
def fetch_counter_stats() -> List[Dict[str, Any]]:
    """Every hero-vs-hero pair: {"hero_id", "enemy_hero_id", "wins", "matches_played", ...}, all ranks."""
    return get_json("/v1/analytics/hero-counter-stats")


def get_item_stats(hero_id: int, enemy_hero_ids: List[int]) -> List[Dict[str, Any]]:
    """Per-item {"item_id", "wins", "losses", "matches"} for one hero, in matches against these enemy heroes."""
    return get_json("/v1/analytics/item-stats",
                    {"hero_id": hero_id, "enemy_hero_ids": ",".join(str(h) for h in enemy_hero_ids)})


@functools.lru_cache(maxsize=None)
def fetch_items() -> Dict[int, Dict[str, Any]]:
    """Shop items by id: {"name", "slot", "tier", "cost"}. Only buyable upgrades (not abilities etc.)."""
    return {
        item["id"]: {"name": item["name"], "slot": item.get("item_slot_type"),
                     "tier": item.get("item_tier"), "cost": item.get("cost")}
        for item in get_json("/v1/assets/items")
        if item.get("type") == "upgrade" and item.get("shopable")
    }


def get_player_ranks(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Rank after each player's latest ranked match: {"account_id", "rank" (tier), "subrank", ...}.

    Batch endpoint with a tighter rate limit (20 requests/min per IP), so call it once per lobby.
    """
    return get_json("/v1/players/rank", {"account_ids": ",".join(str(a) for a in account_ids)})
