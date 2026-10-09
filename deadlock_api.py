"""Minimal client for the public Deadlock API (https://api.deadlock-api.com).

Only read-only, public endpoints are used. Endpoint shapes were checked against
the API's OpenAPI spec (https://api.deadlock-api.com/openapi.json) and real responses.
"""

import functools
import html
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

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
    try:
        data = _download(url, ANALYTICS_TIMEOUT_SECONDS if path.startswith("/v1/analytics/") else TIMEOUT_SECONDS)
    except OSError as e:  # offline, timed out or a server error: an older answer beats a page that can't load
        if hit:
            logger.info(f"Using a {(now - hit[0]) / 60:.0f}-minute-old answer for {path}: {e}")
            return hit[1]
        raise
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


def plain_text(markup: Optional[str]) -> str:
    """Game text without its markup: inline SVG icons, tags and HTML entities removed."""
    text = re.sub(r"<svg.*?</svg>", "", markup or "", flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)  # a space, so words either side of a tag don't merge...
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return re.sub(r" ([.,;:!?])", r"\1", text)  # ...but none before punctuation ("applying slow .")


def _number(prop: Any) -> Optional[float]:
    """A numeric ability property ({"value": "33"}), or None for 0, missing or non-numbers like "4m"."""
    try:
        value = float((prop or {}).get("value"))
    except (TypeError, ValueError):
        return None
    return value or None


@functools.lru_cache(maxsize=None)
def fetch_hero_guides() -> Dict[str, Dict[str, Any]]:
    """Per hero name: {"type", "tags", "complexity", "gun", "health", "speed", "abilities": [{"name",
    "image", "text", "cooldown", "charges"}]}, from the game's hero and item lists. Those are 2 MB and
    6 MB; ~60 KB is kept on disk for 3 days (abilities change only with patches)."""
    def build():
        abilities = {i["class_name"]: i for i in get_json("/v1/assets/items", max_age=0) if i.get("type") == "ability"}
        guides = {}
        for h in get_json("/v1/assets/heroes", max_age=0):
            if not h["player_selectable"] or h["disabled"] or h["in_development"]:
                continue
            stats = h.get("starting_stats") or {}
            kit = []
            for slot in ("signature1", "signature2", "signature3", "signature4"):
                a = abilities.get((h.get("items") or {}).get(slot))
                if not a:
                    continue
                props = a.get("properties") or {}
                image = next((u for u in (a.get("image_webp"), a.get("image")) if u and not u.endswith(".svg")), None)
                kit.append({"name": a["name"], "image": image, "text": plain_text((a.get("description") or {}).get("desc")),
                            "cooldown": _number(props.get("AbilityCooldown")), "charges": _number(props.get("AbilityCharges"))})
            guides[h["name"]] = {"type": h.get("hero_type"), "tags": h.get("tags") or [], "complexity": h.get("complexity"),
                                 "gun": h.get("gun_tag"), "health": (stats.get("max_health") or {}).get("value"),
                                 "speed": (stats.get("max_move_speed") or {}).get("value"), "abilities": kit}
        return guides
    return disk_cached("hero_guides", build, max_age=3 * 86400)


@functools.lru_cache(maxsize=None)
def fetch_hero_assets() -> List[Dict[str, Any]]:
    """Playable heroes: {"id", "name", "icon", "card", "color"} (image URLs and the hero's colour)."""
    def build():
        return [
            {"id": h["id"], "name": h["name"],
             "icon": (h.get("images") or {}).get("icon_image_small"),
             "card": (h.get("images") or {}).get("icon_hero_card"),
             "color": (h.get("colors") or {}).get("style_hex") or "#4a5a6a"}
            for h in get_json("/v1/assets/heroes", max_age=0)
            # not in_development: it lags behind the game (Baba was in matches while still flagged)
            if h["player_selectable"] and not h["disabled"]
        ]
    return disk_cached("playable_heroes", build)  # renamed so a list cached by an older version isn't used


def fetch_heroes() -> List[Dict[str, Any]]:
    """Heroes a player can actually pick right now, as {"id", "name"} dicts."""
    return [{"id": h["id"], "name": h["name"]} for h in fetch_hero_assets()]


def search_steam_profiles(name: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search Steam profiles by display name. Results are ranked by name similarity and activity.

    The API hides accounts with fewer than 5 recorded matches in the last 30 days by default.
    Bot matches aren't recorded, so that filter can hide the very player we're looking for;
    it is turned off here, and same-named accounts are told apart by hero history instead.
    Each result carries the account's friend list, so results are ~3 KB each: keep limit small.
    """
    try:
        return get_json("/v1/players/steam-search",
                        {"search_query": name, "limit": limit, "min_matches_played_last_30d": 0})
    except urllib.error.HTTPError as e:
        if e.code == 404:  # the API's answer when nothing matches: "No Steam profiles found."
            return []
        raise


def get_active_matches(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Live matches that include any of these accounts, with every player's account and hero.
    Only the top ~200 matches being played (the game's Watch tab) are known, so usually empty."""
    return get_json("/v1/matches/active", {"account_ids": ",".join(str(a) for a in account_ids)}, max_age=60)


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
            for r in get_json("/v1/assets/ranks", max_age=0)
        ]
    return disk_cached("ranks", build)


@functools.lru_cache(maxsize=None)
def fetch_rank_tiers() -> Dict[int, Dict[str, str]]:
    """Rank tier number -> {"name", "color"}, e.g. 7 -> Emissary. Tier 0 (Obscurus) means unranked."""
    return {tier["tier"]: {"name": tier["name"], "color": tier["color"]} for tier in fetch_rank_assets()}


def badge_name(badge: int) -> Optional[str]:
    """A rank badge (tier * 10 + subrank) as its name, e.g. 73 -> "Emissary 3"; None for 0 or an unknown tier."""
    tier = fetch_rank_tiers().get(badge // 10) if badge else None
    return f"{tier['name']} {badge % 10}" if tier else None


def get_profiles(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Steam profiles (same shape as search results) for specific accounts."""
    return get_json("/v1/players/steam", {"account_ids": ",".join(str(a) for a in account_ids)})


def fetch_counter_stats(game_mode: str = "normal", ranks: tuple = None, same_lane: bool = False) -> List[Dict[str, Any]]:
    """Every hero-vs-hero pair: {"hero_id", "enemy_hero_id", "wins", "matches_played", and both sides'
    totals: "kills"/"enemy_kills", "deaths", "assists", "networth"...}. same_lane: only games where
    the two heroes were assigned the same lane, i.e. laned against each other.
    Always sent explicitly: the API's default is same lane only (a quarter of the games), which the
    app used by mistake for all its matchups until this was checked against the spec.
    The server recomputes these hourly, so they're reused for an hour."""
    params = {"game_mode": game_mode, **badge_range(ranks), "same_lane_filter": "true" if same_lane else "false"}
    return get_json("/v1/analytics/hero-counter-stats", params, max_age=3600)


def get_synergy_stats(game_mode: str = "normal") -> List[Dict[str, Any]]:
    """Every pair of heroes on the same team: {"hero_id1", "hero_id2", "wins", "matches_played", ...}."""
    return get_json("/v1/analytics/hero-synergy-stats", {"game_mode": game_mode}, max_age=3600)


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


WEEK_SECONDS = 7 * 86400


def fetch_weekly_hero_stats(game_mode: str = "normal", ranks: tuple = None, weeks: int = 12) -> Dict[str, Any]:
    """Every hero's [wins, games] in each of the last `weeks` complete weeks:
    {"weeks": [start of each week, unix time], "heroes": {hero_id: [[wins, games], ...]}} (string keys,
    as stored in JSON). The API's weeks start on Sunday, 00:00 UTC. The current week is left out until
    it's over: its first days would make the line jump. The answer is ~335 KB with 24 fields per row;
    only these numbers are kept, on disk for 6 hours."""
    def build():
        today = int(time.time() // 86400) * 86400
        this_week = today - (time.gmtime(today).tm_wday + 1) % 7 * 86400  # tm_wday: Monday = 0
        start = this_week - weeks * WEEK_SECONDS
        rows = get_json("/v1/analytics/hero-stats", {
            "game_mode": game_mode, "bucket": "start_time_week", "min_unix_timestamp": start,
            "max_unix_timestamp": this_week - 1, **badge_range(ranks)}, max_age=0)
        starts = [start + n * WEEK_SECONDS for n in range(weeks)]
        index = {week: n for n, week in enumerate(starts)}
        heroes: Dict[str, List[List[int]]] = {}
        for row in rows:
            if row["bucket"] in index:
                series = heroes.setdefault(str(row["hero_id"]), [[0, 0] for _ in starts])
                series[index[row["bucket"]]] = [row["matches"] - row["losses"], row["matches"]]
        return {"weeks": starts, "heroes": heroes}
    low, high = ranks or (0, 0)
    return disk_cached(f"weekly_{game_mode}_{low}_{high}", build, max_age=6 * 3600)


DAY_SECONDS = 86400


def fetch_daily_item_stats(game_mode: str = "normal", ranks: tuple = None, days: int = 14) -> Dict[str, Any]:
    """Every shop item's [wins, games] on each of the last `days` complete days (UTC), how many
    player-games each day had (to tell how often an item is bought), and each item's average buy
    time: {"days", "items": {item_id: [[wins, games], ...]}, "player_games": [...], "buy_time": {item_id: s}}.
    Two requests (~640 KB; ~7 s when the server hasn't calculated them lately), kept as ~30 KB on
    disk for 3 hours. Today is left out until it's over, like the current week for heroes."""
    def build():
        today = int(time.time() // DAY_SECONDS) * DAY_SECONDS
        start = today - days * DAY_SECONDS
        params = {"game_mode": game_mode, "bucket": "start_time_day", "min_unix_timestamp": start,
                  "max_unix_timestamp": today - 1, **badge_range(ranks)}
        item_rows, hero_rows = parallel(lambda: get_json("/v1/analytics/item-stats", params, max_age=0),
                                        lambda: get_json("/v1/analytics/hero-stats", params, max_age=0))
        starts = [start + n * DAY_SECONDS for n in range(days)]
        index = {day: n for n, day in enumerate(starts)}
        player_games = [0] * days
        for row in hero_rows:
            if row["bucket"] in index:
                player_games[index[row["bucket"]]] += row["matches"]
        items: Dict[str, List[List[int]]] = {}
        buy_time: Dict[str, List[float]] = {}  # item -> [sum of buy time x games, games]
        for row in item_rows:
            if row["bucket"] in index:
                key = str(row["item_id"])
                items.setdefault(key, [[0, 0] for _ in starts])[index[row["bucket"]]] = [row["wins"], row["matches"]]
                if row.get("avg_buy_time_s"):
                    totals = buy_time.setdefault(key, [0.0, 0])
                    totals[0] += row["avg_buy_time_s"] * row["matches"]
                    totals[1] += row["matches"]
        return {"days": starts, "items": items, "player_games": player_games,
                "buy_time": {key: round(total / games) for key, (total, games) in buy_time.items() if games}}
    low, high = ranks or (0, 0)
    return disk_cached(f"items_daily_{game_mode}_{low}_{high}", build, max_age=3 * 3600)


def get_player_metrics(hero_id: int, game_mode: str = "normal", ranks: tuple = None,
                       min_minutes: int = None, max_minutes: int = None) -> Dict[str, Dict[str, float]]:
    """Percentiles (1st-99th), average and spread of each scoreboard stat for players of one hero over
    the last 30 days, optionally at a rank band and in matches of a given length. ~10 KB; the server
    recomputes each answer every 6 hours. The API refuses a rank filter for Street Brawl."""
    params = {"hero_ids": hero_id, "game_mode": game_mode, **badge_range(ranks)}
    if min_minutes is not None:
        params["min_duration_s"] = min_minutes * 60
    if max_minutes is not None:
        params["max_duration_s"] = min(7000, max_minutes * 60)  # the API's maximum
    return get_json("/v1/analytics/player-stats/metrics", params, max_age=3600)


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
    abilities etc.). "image" is the item's current shop artwork, as the game shows it ("shop_image"; the
    API's "image" field is the old white symbol from before the icons were redrawn). An item without
    artwork falls back to that symbol, drawn on its category's colour, and "symbol" is True."""
    def readable(url):
        return url if url and not url.endswith(".svg") else None  # Pillow can't read SVG

    def build():  # stored as a list: JSON object keys can't be numbers
        rows = []
        for item in get_json("/v1/assets/items", max_age=0):
            if item.get("type") != "upgrade" or not item.get("shopable"):
                continue
            art = readable(item.get("shop_image_webp")) or readable(item.get("shop_image"))
            rows.append({"id": item["id"], "name": item["name"], "slot": item.get("item_slot_type"),
                         "tier": item.get("item_tier"), "cost": item.get("cost"),
                         "image": art or readable(item.get("image")), "symbol": not art})
        return rows
    # The file name changes when the fields do, so an older copy without them isn't used
    return {item["id"]: item for item in disk_cached("items_v4", build)}


def get_player_ranks(account_ids: List[int]) -> List[Dict[str, Any]]:
    """Rank after each player's latest ranked match: {"account_id", "rank" (tier), "subrank", ...}.

    Batch endpoint with a tighter rate limit (20 requests/min per IP), so call it once per lobby.
    """
    return get_json("/v1/players/rank", {"account_ids": ",".join(str(a) for a in account_ids)})
