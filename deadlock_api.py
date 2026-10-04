"""Minimal client for the public Deadlock API (https://api.deadlock-api.com).

Only read-only, public endpoints are used. Endpoint shapes were checked against
the API's OpenAPI spec (https://api.deadlock-api.com/openapi.json) and real responses.
"""

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


def fetch_heroes() -> List[Dict[str, Any]]:
    """Return heroes a player can actually pick right now, as {"id", "name"} dicts."""
    heroes = get_json("/v1/assets/heroes")
    return [
        {"id": hero["id"], "name": hero["name"]}
        for hero in heroes
        if hero["player_selectable"] and not hero["disabled"] and not hero["in_development"]
    ]


def search_steam_profiles(name: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search Steam profiles by display name. Results are ranked by name similarity and activity."""
    return get_json("/v1/players/steam-search", {"search_query": name, "limit": limit})


def get_hero_stats(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Per-hero stats for one or more accounts (one entry per account+hero pair)."""
    return get_json("/v1/players/hero-stats", {"account_ids": ",".join(str(a) for a in account_ids)})
