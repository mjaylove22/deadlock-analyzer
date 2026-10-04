"""Minimal client for the public Deadlock API (https://api.deadlock-api.com).

Only read-only, public endpoints are used. Endpoint shapes were checked against
the API's OpenAPI spec (https://api.deadlock-api.com/openapi.json) and real responses.
"""

import functools
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List

import paths

logger = logging.getLogger(__name__)

BASE_URL = "https://api.deadlock-api.com"
TIMEOUT_SECONDS = 10
# Analytics (/v1/analytics/...) are calculated by the server when asked, e.g. every hero pair at one
# rank range. That usually takes 1-2 s but much longer when the server is busy, so they get more time.
ANALYTICS_TIMEOUT_SECONDS = 30

# Responses are remembered so going Back or revisiting a page doesn't wait on the network again.
# Callers must treat returned data as read-only: the same object is handed out until it expires.
CACHE_SECONDS = 300            # most answers: reused for 5 minutes
ASSET_CACHE_SECONDS = 86400    # hero/rank/item lists change rarely: kept on disk for a day
MAX_CACHED = 300               # oldest entries are dropped beyond this, to keep memory small
DISK_CACHE_DIR = paths.data("cache", "api")

_memory: "OrderedDict[str, tuple]" = OrderedDict()  # url -> (time fetched, data)
_lock = threading.Lock()


def _download(url: str, timeout: float = TIMEOUT_SECONDS) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "deadlock-analyzer (learning project)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def disk_cached(name: str, build: Callable[[], Any], max_age: float = ASSET_CACHE_SECONDS) -> Any:
    """build()'s result, kept on disk between runs as cache/api/<name>.json. Only slim, processed
    data is stored (the raw hero and item responses are 2 MB and 6 MB). If rebuilding fails, an old
    copy is better than nothing (e.g. offline)."""
    path = os.path.join(DISK_CACHE_DIR, name + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    try:
        data = build()
    except OSError:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        raise
    os.makedirs(DISK_CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def get_json(path: str, params: Dict[str, Any] = None, max_age: float = CACHE_SECONDS) -> Any:
    """GET an API path and return the decoded JSON body, reusing answers younger than max_age
    seconds (0 = always fetch)."""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    now = time.time()
    with _lock:
        hit = _memory.get(url)
        if hit and now - hit[0] < max_age:
            _memory.move_to_end(url)
            return hit[1]
    data = _download(url, ANALYTICS_TIMEOUT_SECONDS if path.startswith("/v1/analytics/") else TIMEOUT_SECONDS)
    if max_age <= 0:
        return data  # asked not to reuse it, so don't hold on to it either (e.g. 1.5 MB match data)
    with _lock:
        _memory[url] = (now, data)
        _memory.move_to_end(url)
        while len(_memory) > MAX_CACHED:
            _memory.popitem(last=False)
    return data


def parallel(*calls: Callable[[], Any], allow_failures: bool = False) -> List[Any]:
    """Run independent API calls at the same time; results come back in the same order.
    Waiting for the slowest call beats waiting for all of them one after another.

    allow_failures: a call that fails on the network or server gives None (logged) instead of
    raising, so a page can show what did load."""
    def run(call):
        if not allow_failures:
            return call()
        try:
            return call()
        except (OSError, ValueError) as e:  # timeouts, HTTP errors and bad JSON
            logger.warning(f"A request failed, showing the rest without it: {e}")
            return None
    with ThreadPoolExecutor(max_workers=min(len(calls), 8)) as pool:
        return list(pool.map(run, calls))


@functools.lru_cache(maxsize=None)
def fetch_hero_assets() -> List[Dict[str, Any]]:
    """Playable heroes: {"id", "name", "icon", "card", "color"} (image URLs and the hero's colour)."""
    def build():
        return [
            {"id": h["id"], "name": h["name"],
             "icon": (h.get("images") or {}).get("icon_image_small"),
             "card": (h.get("images") or {}).get("icon_hero_card"),
             "color": (h.get("colors") or {}).get("style_hex") or "#4a5a6a"}
            for h in get_json("/v1/assets/heroes", max_age=ASSET_CACHE_SECONDS)
            if h["player_selectable"] and not h["disabled"] and not h["in_development"]
        ]
    return disk_cached("heroes", build)


def fetch_heroes() -> List[Dict[str, Any]]:
    """Heroes a player can actually pick right now, as {"id", "name"} dicts."""
    return [{"id": h["id"], "name": h["name"]} for h in fetch_hero_assets()]


def search_steam_profiles(name: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search Steam profiles by display name. Results are ranked by name similarity and activity.

    The API hides accounts with fewer than 5 recorded matches in the last 30 days by default.
    Bot matches aren't recorded, so that filter can hide the very player we're looking for;
    it is turned off here, and same-named accounts are told apart by hero history instead.
    """
    return get_json("/v1/players/steam-search",
                    {"search_query": name, "limit": limit, "min_matches_played_last_30d": 0})


def get_hero_stats(account_ids: List[int], game_mode: str = "normal", match_mode: str = None) -> List[Dict[str, Any]]:
    """Per-hero stats for one or more accounts (one entry per account+hero pair).
    game_mode: "normal" (the API default) or "street_brawl". match_mode: "ranked" or "unranked"
    (default: both)."""
    params = {"account_ids": ",".join(str(a) for a in account_ids), "game_mode": game_mode}
    if match_mode:
        params["match_mode"] = match_mode
    return get_json("/v1/players/hero-stats", params)


def get_match_history(account_id: int) -> List[Dict[str, Any]]:
    """A player's recorded matches (not guaranteed newest first; sort by start_time)."""
    return get_json(f"/v1/players/{account_id}/match-history")


def badge_range(ranks: tuple = None) -> Dict[str, int]:
    """Analytics filter for matches whose average rank falls in (lowest tier, highest tier).
    Badges are tier * 10 + subrank, e.g. Emissary 3 = 73."""
    if not ranks:
        return {}
    low, high = ranks
    return {"min_average_badge": low * 10, "max_average_badge": high * 10 + 9}


def get_global_hero_stats(game_mode: str = "normal", ranks: tuple = None) -> List[Dict[str, Any]]:
    """Every hero's totals across recorded matches: {"hero_id", "matches", "losses", "total_kills", ...}.
    ranks: (lowest tier, highest tier) to only count matches at that skill level."""
    return get_json("/v1/analytics/hero-stats", {"game_mode": game_mode, **badge_range(ranks)}, max_age=3600)


@functools.lru_cache(maxsize=None)
def fetch_rank_assets() -> List[Dict[str, Any]]:
    """Ranks: {"tier", "name", "color", "emblems": {"1": url, ... "6": url}} (small emblem per subrank)."""
    def build():
        return [
            {"tier": r["tier"], "name": r["name"], "color": r["color"],
             "emblems": {str(n): (r.get("images") or {}).get(f"small_subrank{n}") for n in range(1, 7)}}
            for r in get_json("/v1/assets/ranks", max_age=ASSET_CACHE_SECONDS)
        ]
    return disk_cached("ranks", build)


@functools.lru_cache(maxsize=None)
def fetch_rank_tiers() -> Dict[int, Dict[str, str]]:
    """Rank tier number -> {"name", "color"}, e.g. 7 -> Emissary. Tier 0 (Obscurus) means unranked."""
    return {tier["tier"]: {"name": tier["name"], "color": tier["color"]} for tier in fetch_rank_assets()}


def get_profiles(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Steam profiles (same shape as search results) for specific accounts."""
    return get_json("/v1/players/steam", {"account_ids": ",".join(str(a) for a in account_ids)})


def fetch_counter_stats(game_mode: str = "normal", ranks: tuple = None) -> List[Dict[str, Any]]:
    """Every hero-vs-hero pair: {"hero_id", "enemy_hero_id", "wins", "matches_played", ...}.
    The server recomputes these hourly, so they're reused for an hour."""
    return get_json("/v1/analytics/hero-counter-stats", {"game_mode": game_mode, **badge_range(ranks)}, max_age=3600)


def get_item_stats(hero_id: int, enemy_hero_ids: List[int] = (), game_mode: str = "normal",
                   ranks: tuple = None) -> List[Dict[str, Any]]:
    """Per-item {"item_id", "wins", "losses", "matches"} for one hero, optionally only in matches
    against these enemy heroes."""
    params = {"hero_id": hero_id, "game_mode": game_mode, **badge_range(ranks)}
    if enemy_hero_ids:
        params["enemy_hero_ids"] = ",".join(str(h) for h in enemy_hero_ids)
    return get_json("/v1/analytics/item-stats", params, max_age=3600)


def fetch_rank_curves(game_mode: str = "normal") -> Dict[str, Dict[str, List[int]]]:
    """Every hero's [wins, games] at each rank tier: {hero_id: {tier: [wins, games]}} (string keys,
    as stored in JSON). The API's per-rank answer is ~1.6 MB with 24 fields per row; only these
    numbers are kept, on disk for 6 hours (the server recomputes its stats hourly)."""
    def build():
        rows = get_json("/v1/analytics/hero-stats", {"game_mode": game_mode, "bucket": "avg_badge"}, max_age=0)
        curves: Dict[str, Dict[str, List[int]]] = {}
        for row in rows:
            tier = row["bucket"] // 10  # buckets are badges: tier * 10 + subrank; 0 = unranked matches
            if tier:
                totals = curves.setdefault(str(row["hero_id"]), {}).setdefault(str(tier), [0, 0])
                totals[0] += row["matches"] - row["losses"]
                totals[1] += row["matches"]
        return curves
    return disk_cached(f"rank_curves_{game_mode}", build, max_age=6 * 3600)


def get_hero_bans(ranks: tuple = None) -> List[Dict[str, Any]]:
    """How many times each hero was banned: {"hero_id", "bans"}. Only counts, not how many matches
    they came from, so a true ban rate can't be computed from this; a hero's share of all bans can."""
    return get_json("/v1/analytics/hero-ban-stats", badge_range(ranks), max_age=3600)


def get_mate_stats(account_id: int, min_matches: int = 10) -> List[Dict[str, Any]]:
    """Teammates a player has played at least min_matches with: {"mate_id", "wins", "matches_played", ...}.
    Without the filter this returns every teammate ever (3,400+ entries, ~275 KB, for one player)."""
    return get_json(f"/v1/players/{account_id}/mate-stats", {"min_matches_played": min_matches})


@functools.lru_cache(maxsize=None)
def fetch_items() -> Dict[int, Dict[str, Any]]:
    """Shop items by id: {"name", "slot", "tier", "cost", "image", "symbol"}. Only buyable upgrades (not
    abilities etc.). "image" is the item's white symbol (about 1 KB), drawn on its category's colour like
    in the shop; a few items have none (or only an SVG, which Pillow can't read), so their shop artwork
    is used instead and "symbol" is False."""
    def symbol(item):
        url = item.get("image") or ""
        return url if url and not url.endswith(".svg") else None

    def build():  # stored as a list: JSON object keys can't be numbers
        return [{"id": item["id"], "name": item["name"], "slot": item.get("item_slot_type"),
                 "tier": item.get("item_tier"), "cost": item.get("cost"),
                 "image": symbol(item) or item.get("shop_image"), "symbol": bool(symbol(item))}
                for item in get_json("/v1/assets/items", max_age=ASSET_CACHE_SECONDS)
                if item.get("type") == "upgrade" and item.get("shopable")]
    # The file name changes when the fields do, so an older copy without them isn't used
    return {item["id"]: item for item in disk_cached("items_v3", build)}


def get_player_ranks(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Rank after each player's latest ranked match: {"account_id", "rank" (tier), "subrank", ...}.

    Batch endpoint with a tighter rate limit (20 requests/min per IP), so call it once per lobby.
    """
    return get_json("/v1/players/rank", {"account_ids": ",".join(str(a) for a in account_ids)})
