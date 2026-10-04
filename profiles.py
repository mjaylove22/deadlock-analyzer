"""Data for the player pages and the heroes tab: hero breakdowns, recent matches, tier list.

Match history stores game mode and result as numbers. Meanings from the API's spec:
game_mode 1 = Normal, 4 = Street Brawl; match_mode 1 = Unranked, 4 = Ranked. The result field
(player_match_outcome) is 0 ("invalid") for most matches, so a win is worked out as "the player's
team is the winning team" (match_result == player_team), which agrees with it whenever it is set.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import deadlock_api
from player_lookup import fetch_ranks

GAME_MODES = {1: "Normal", 4: "Street Brawl"}
API_GAME_MODES = {"Normal": "normal", "Street Brawl": "street_brawl"}  # names the stats endpoints expect
PLAYERS_PER_MATCH = {"normal": 12, "street_brawl": 8}
RECENT_MATCHES_SHOWN = 20
TEAMMATES_SHOWN = 4  # what fits on one row of the player page

# Rank bands for the Heroes filter: (label, (lowest tier, highest tier)). Bands rather than single
# ranks keep enough games behind every win rate. Names checked against the API's rank list.
RANK_BANDS = [
    ("All ranks", None),
    ("Initiate - Acolyte", (1, 3)),
    ("Sentinel - Ritualist", (4, 6)),
    ("Emissary - Phantom", (7, 9)),
    ("Ascendant - Eternus", (10, 11)),
]
MIN_TIER_LIST_GAMES = 500  # heroes with fewer games (e.g. just released) are left out of the tier list


def match_won(match: Dict[str, Any]) -> bool:
    return match["match_result"] == match["player_team"]


def describe_match(match: Dict[str, Any], hero_names_by_id: Dict[int, str]) -> Dict[str, Any]:
    """One match-history entry as display-ready values."""
    return {
        "match_id": match["match_id"],
        "hero": hero_names_by_id.get(match["hero_id"], f"hero #{match['hero_id']}"),
        "won": match_won(match),
        "kills": match["player_kills"], "deaths": match["player_deaths"], "assists": match["player_assists"],
        "net_worth": match["net_worth"],
        "minutes": round(match["match_duration_s"] / 60),
        "start_time": match["start_time"],
        "mode": GAME_MODES.get(match["game_mode"], "Other"),
        "ranked": match["match_mode"] == 4,
    }


def mode_breakdown(matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Games and win rate per game mode, most-played mode first."""
    modes = {}
    for m in matches:
        name = GAME_MODES.get(m["game_mode"], "Other")
        entry = modes.setdefault(name, {"mode": name, "games": 0, "wins": 0})
        entry["games"] += 1
        entry["wins"] += match_won(m)
    rows = sorted(modes.values(), key=lambda e: e["games"], reverse=True)
    for row in rows:
        row["win_rate"] = row["wins"] / row["games"]
    return rows


def hero_rows(entries: List[Dict[str, Any]], hero_names_by_id: Dict[int, str]) -> List[Dict[str, Any]]:
    """A player's per-hero stats as table rows, most-played first."""
    rows = []
    for e in entries:
        games = e["matches_played"]
        if not games:
            continue
        rows.append({
            "hero": hero_names_by_id.get(e["hero_id"], f"hero #{e['hero_id']}"),
            "games": games,
            "win_rate": e["wins"] / games,
            "kda": (e.get("kills", 0) + e.get("assists", 0)) / max(e.get("deaths", 0), 1),
            "damage_per_min": e.get("damage_per_min", 0.0),
            "last_played": e.get("last_played"),
        })
    return sorted(rows, key=lambda r: r["games"], reverse=True)


def tier_rows(stats: List[Dict[str, Any]], hero_names_by_id: Dict[int, str],
              game_mode: str = "normal", bans: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Global hero stats as tier-list rows, highest win rate first.

    Pick rate = share of matches the hero appears in. Every match has 12 heroes (8 in Street
    Brawl), so the number of matches is the sum of all heroes' games divided by that.
    """
    total_matches = sum(s["matches"] for s in stats) / PLAYERS_PER_MATCH.get(game_mode, 12)
    # Ban share = the hero's part of all recorded bans. The API gives ban counts but not how many
    # matches they come from, so this is used instead of a ban rate; the ranking is the same.
    ban_counts = {b["hero_id"]: b["bans"] for b in bans or []}
    total_bans = sum(ban_counts.values())
    rows = []
    for s in stats:
        games = s["matches"]
        if games < MIN_TIER_LIST_GAMES or s["hero_id"] not in hero_names_by_id:
            continue
        rows.append({
            "hero": hero_names_by_id[s["hero_id"]],
            "games": games,
            "win_rate": (games - s["losses"]) / games,
            "pick_rate": games / total_matches if total_matches else 0.0,
            "kda": (s.get("total_kills", 0) + s.get("total_assists", 0)) / max(s.get("total_deaths", 0), 1),
            # None (shown as "-") when the hero isn't in the ban data at all, e.g. a brand-new hero:
            # 0% would wrongly claim nobody bans it
            "ban_share": ban_counts[s["hero_id"]] / total_bans if total_bans and s["hero_id"] in ban_counts else None,
        })
    return sorted(rows, key=lambda r: r["win_rate"], reverse=True)


def player_profile(account_id: int, hero_names_by_id: Dict[int, str], game_mode: str = "normal") -> Dict[str, Any]:
    """Everything the player page shows, in one call (run it on a worker thread)."""
    profiles, history, heroes, ranks = deadlock_api.parallel(
        lambda: deadlock_api.get_profiles([account_id]),
        lambda: deadlock_api.get_match_history(account_id),
        lambda: deadlock_api.get_hero_stats([account_id], game_mode),
        lambda: fetch_ranks([account_id]))
    profile = profiles[0] if profiles else {}
    matches = sorted(history, key=lambda m: m["start_time"], reverse=True)
    return {
        "account_id": account_id,
        "name": profile.get("personaname", f"Account {account_id}"),
        "profile_url": profile.get("profileurl"),
        "avatar_url": profile.get("avatarfull") or profile.get("avatarmedium"),
        "recent_30d": profile.get("matches_played_last_30d"),
        "game_mode": game_mode,
        "rank": ranks.get(account_id),
        "heroes": hero_rows(heroes, hero_names_by_id),
        "modes": mode_breakdown(matches),
        "recent": [describe_match(m, hero_names_by_id) for m in matches[:RECENT_MATCHES_SHOWN]],
        "total_matches": len(matches),
    }


def hero_tier_list(hero_names_by_id: Dict[int, str], game_mode: str = "normal", ranks: tuple = None) -> List[Dict[str, Any]]:
    """Tier list rows, optionally only from matches in a rank band (see RANK_BANDS).
    Bans exist in normal matches only, so Street Brawl rows have no ban share."""
    if game_mode != "normal":
        return tier_rows(deadlock_api.get_global_hero_stats(game_mode, ranks), hero_names_by_id, game_mode)
    stats, bans = deadlock_api.parallel(lambda: deadlock_api.get_global_hero_stats(game_mode, ranks),
                                        lambda: deadlock_api.get_hero_bans(ranks))
    return tier_rows(stats, hero_names_by_id, game_mode, bans)


def top_mates(mates: List[Dict[str, Any]], count: int = TEAMMATES_SHOWN) -> List[Dict[str, Any]]:
    """The teammates a player has the most games with: {"account_id", "games", "win_rate"}."""
    best = sorted(mates, key=lambda m: m["matches_played"], reverse=True)[:count]
    return [{"account_id": m["mate_id"], "games": m["matches_played"],
             "win_rate": m["wins"] / m["matches_played"] if m["matches_played"] else 0.0} for m in best]


def teammates(account_id: int) -> List[Dict[str, Any]]:
    """Frequent teammates with names and avatars (two requests; the second needs the first's ids)."""
    mates = top_mates(deadlock_api.get_mate_stats(account_id))
    if not mates:
        return []
    profiles = {p["account_id"]: p for p in deadlock_api.get_profiles([m["account_id"] for m in mates])}
    for m in mates:
        profile = profiles.get(m["account_id"], {})
        m["name"] = profile.get("personaname", f"Account {m['account_id']}")
        m["avatar_url"] = profile.get("avatarmedium") or profile.get("avatar")
    return mates


def when(unix_time: Optional[int], now: Optional[float] = None) -> str:
    """'3h ago', '2d ago', or a date for anything older than a week."""
    if not unix_time:
        return ""
    seconds = (now or time.time()) - unix_time
    if seconds < 3600:
        return f"{max(int(seconds // 60), 1)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 7 * 86400:
        return f"{int(seconds // 86400)}d ago"
    return datetime.fromtimestamp(unix_time).strftime("%b %d").replace(" 0", " ")
