"""The items page: every shop item's win rate, how often it's bought, and how it moved over two weeks."""

from typing import Any, Dict, List

import deadlock_api
from profiles import trend_change

TREND_DAYS = 14
COMPARE_DAYS = 7          # the change: the last 7 days against the 7 before
MIN_DAY_GAMES = 100       # days with fewer purchases are left off an item's line
MIN_ITEM_GAMES = 1000     # items bought less than this in two weeks aren't listed (too few to judge)


def item_trends(game_mode: str = "normal", ranks: tuple = None) -> Dict[str, Any]:
    """{"days": [day starts], "rows": [item fields + "games", "win_rate", "bought", "buy_time", "trend"]},
    most bought first. "bought" = the share of player-games that bought it."""
    data = deadlock_api.fetch_daily_item_stats(game_mode, ranks, TREND_DAYS)
    items = deadlock_api.fetch_items()
    days, player_games = data["days"], data["player_games"]
    total = sum(player_games)
    rows: List[Dict[str, Any]] = []
    for item_id, series in data["items"].items():
        item = items.get(int(item_id))
        games = sum(g for _, g in series)
        if not item or games < MIN_ITEM_GAMES or not total:
            continue
        points = [{"index": n, "start": day, "games": g, "win_rate": w / g, "pick_rate": g / player_games[n]}
                  for n, (day, (w, g)) in enumerate(zip(days, series)) if g >= MIN_DAY_GAMES and player_games[n]]
        rows.append(dict(item, games=games, win_rate=sum(w for w, _ in series) / games, bought=games / total,
                         buy_time=data["buy_time"].get(item_id), trend={"weeks": points, **trend_change(series, COMPARE_DAYS)}))
    return {"days": days, "rows": sorted(rows, key=lambda r: r["bought"], reverse=True)}
