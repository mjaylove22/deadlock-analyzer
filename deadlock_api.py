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


def search_steam_profiles(name: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search Steam profiles by display name. Results are ranked by name similarity and activity.

    The API hides accounts with fewer than 5 recorded matches in the last 30 days by default.
    Bot matches aren't recorded, so that filter can hide the very player we're looking for;
    it is turned off here, and same-named accounts are told apart by hero history instead.
    """
    return get_json("/v1/players/steam-search",
                    {"search_query": name, "limit": limit, "min_matches_played_last_30d": 0})


def get_hero_stats(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Per-hero stats for one or more accounts (one entry per account+hero pair)."""
    return get_json("/v1/players/hero-stats", {"account_ids": ",".join(str(a) for a in account_ids)})


def fetch_rank_tiers() -> Dict[int, Dict[str, str]]:
    """Rank tier number -> {"name", "color"}, e.g. 7 -> Emissary. Tier 0 (Obscurus) means unranked."""
    return {tier["tier"]: {"name": tier["name"], "color": tier["color"]} for tier in get_json("/v1/assets/ranks")}


def get_player_ranks(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Rank after each player's latest ranked match: {"account_id", "rank" (tier), "subrank", ...}.

    Batch endpoint with a tighter rate limit (20 requests/min per IP), so call it once per lobby.
    """
    return get_json("/v1/players/rank", {"account_ids": ",".join(str(a) for a in account_ids)})
