"""What a player's hero stats say about this match: stats on the current hero, and badges.

Pure logic with no network calls. Input is the hero-stats entries for one account (one entry
per hero, as returned by the API). Stats cover normal matches only; bot matches aren't recorded.
"""

from typing import Dict, List, Optional, Tuple

Badge = Tuple[str, str]  # (label, kind): kind is "strong", "good", "warn" or "info" (sets the colour)

# Thresholds, chosen so a badge means something rather than firing on tiny samples
FEW_GAMES_TOTAL = 20          # below this, there isn't enough history to say much
ONE_TRICK_MIN_GAMES = 50      # one-trick: their most-played hero, with many games...
ONE_TRICK_MIN_SHARE = 0.40    # ...making up a big share of everything they play
MAIN_MIN_GAMES = 10           # "on main" / "comfort pick" need a real number of games
NEW_ON_HERO_MAX_GAMES = 5     # fewer games than this on the current hero = still learning it
WIN_RATE_MIN_GAMES = 20       # win rates on fewer games are mostly noise
HIGH_WIN_RATE = 0.60
LOW_WIN_RATE = 0.40
VETERAN_GAMES = 1000


def hero_summary(entries: List[Dict], hero_id: int) -> Optional[Dict]:
    """Stats on one hero, or None if they have no recorded games on it."""
    entry = next((e for e in entries if e["hero_id"] == hero_id and e["matches_played"] > 0), None)
    if not entry:
        return None
    games = entry["matches_played"]
    return {
        "games": games,
        "win_rate": entry["wins"] / games,
        # .get(): a missing field from the API should cost one number, not crash the report
        "kda": (entry.get("kills", 0) + entry.get("assists", 0)) / max(entry.get("deaths", 0), 1),
        "damage_per_min": entry.get("damage_per_min", 0.0),
        "networth_per_min": entry.get("networth_per_min", 0.0),
    }


def compute_badges(entries: List[Dict], hero_id: int) -> List[Badge]:
    """Badges describing the player's history with the hero they're on right now."""
    played = sorted((e for e in entries if e["matches_played"] > 0), key=lambda e: e["matches_played"], reverse=True)
    total = sum(e["matches_played"] for e in played)
    if total < FEW_GAMES_TOTAL:
        return [("FEW RECORDED GAMES", "info")]

    badges = []
    position = next((n for n, e in enumerate(played, start=1) if e["hero_id"] == hero_id), None)
    current = played[position - 1] if position else None
    games = current["matches_played"] if current else 0

    # How familiar is this pick?
    if position == 1 and games >= ONE_TRICK_MIN_GAMES and games / total >= ONE_TRICK_MIN_SHARE:
        badges.append(("ONE-TRICK", "strong"))
    elif position == 1 and games >= MAIN_MIN_GAMES:
        badges.append(("ON MAIN", "strong"))
    elif position in (2, 3) and games >= MAIN_MIN_GAMES:
        badges.append(("COMFORT PICK", "good"))
    if games == 0:
        badges.append(("FIRST GAME ON HERO", "warn"))
    elif games < NEW_ON_HERO_MAX_GAMES:
        badges.append(("NEW ON HERO", "warn"))

    # How well do they do on it?
    if games >= WIN_RATE_MIN_GAMES:
        win_rate = current["wins"] / games
        if win_rate >= HIGH_WIN_RATE:
            badges.append(("HIGH WR", "good"))
        elif win_rate <= LOW_WIN_RATE:
            badges.append(("LOW WR", "warn"))

    if total >= VETERAN_GAMES:
        badges.append(("VETERAN", "info"))
    return badges
