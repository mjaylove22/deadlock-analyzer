"""How well someone played their hero: each stat as a percentile among players on the same hero.

The comparison is the same hero, at the match's rank band, in matches of about the same length
(within MATCH_LENGTH_WINDOW_MIN minutes, so totals like deaths are fair), over the last 30 days.
The numbers come from /v1/analytics/player-stats/metrics, which gives the 1st-99th percentiles of
each stat (~10 KB per hero). Its formulas were checked in the API's source so this match's numbers
are computed the same way; e.g. crit rate is crits / (crits + other hero hits), not crits / hits.
"""

from dataclasses import dataclass
from statistics import mean
from typing import Any, Callable, Dict, List, Optional

PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)
MATCH_LENGTH_WINDOW_MIN = 6
HIGH, LOW = "high", "low"  # which way is better


@dataclass(frozen=True)
class Stat:
    key: str
    label: str
    metric: str                                      # field in the API's answer
    better: Optional[str]                            # HIGH, LOW, or None: shown, but not good or bad
    value: Callable[[Dict[str, Any], float], Optional[float]]  # (player summary, minutes) -> value
    fmt: str = "{:,.0f}"
    in_score: bool = False                           # counts towards the overall score


def _per_minute(key):
    return lambda p, minutes: p[key] / minutes if key in p and minutes else None


def _ratio(hit_key, other_key):
    """hit / (hit + other), or None when there's nothing to divide (or an old summary without them)."""
    def value(p, minutes):
        if hit_key not in p or other_key not in p or not p[hit_key] + p[other_key]:
            return None
        return p[hit_key] / (p[hit_key] + p[other_key])
    return value


STATS = [
    Stat("souls", "Souls per minute", "net_worth_per_min", HIGH, _per_minute("net_worth"), in_score=True),
    Stat("damage", "Player damage per minute", "player_damage_per_min", HIGH, _per_minute("damage"), in_score=True),
    Stat("kda", "KDA", "kda", HIGH, lambda p, m: (p["kills"] + p["assists"]) / max(p["deaths"], 1), "{:.1f}", in_score=True),
    Stat("deaths", "Deaths", "deaths", LOW, lambda p, m: p["deaths"], in_score=True),
    Stat("objectives", "Objective damage per minute", "boss_damage_per_min", HIGH, _per_minute("boss_damage"), in_score=True),
    Stat("last_hits", "Last hits", "last_hits", HIGH, lambda p, m: p["last_hits"]),
    Stat("healing", "Healing per minute (self and allies)", "player_healing_per_min", HIGH, _per_minute("healing")),
    Stat("accuracy", "Accuracy", "accuracy", HIGH, _ratio("shots_hit", "shots_missed"), "{:.0%}"),
    Stat("crits", "Crit shot rate", "crit_shot_rate", HIGH, _ratio("crits", "hero_hits"), "{:.0%}"),
    # Tanks are meant to soak damage, so taking a lot isn't good or bad on its own
    Stat("damage_taken", "Damage taken per minute", "player_damage_taken_per_min", None, _per_minute("damage_taken")),
]


def percentile_of(value: float, metric: Dict[str, float]) -> float:
    """Where value falls among the API's percentiles (0-100), by straight lines between them.
    Ties (e.g. most players heal 0) count as the middle of the tied range."""
    points = [(p, metric[f"percentile{p}"]) for p in PERCENTILES]
    tied = [p for p, v in points if v == value]
    if tied:
        return (tied[0] + tied[-1]) / 2
    if value < points[0][1]:
        return 0.5
    if value > points[-1][1]:
        return 99.5
    for (p1, v1), (p2, v2) in zip(points, points[1:]):
        if v1 <= value <= v2:
            return p1 + (p2 - p1) * (value - v1) / (v2 - v1)
    return 50.0  # unreachable with sorted percentiles; a safe middle if the data ever isn't sorted


def grade(good: float) -> str:
    """good: the percentile with "better" pointing up (100 = best)."""
    if good >= 90:
        return "excellent"
    if good >= 70:
        return "good"
    if good > 30:
        return "typical"
    if good > 10:
        return "below par"
    return "poor"


def verdict(score: float) -> str:
    if score >= 75:
        return "Great game"
    if score >= 60:
        return "Good game"
    if score >= 40:
        return "Average game"
    if score >= 25:
        return "Tough game"
    return "Rough game"


def compared_text(row: Dict[str, Any]) -> str:
    """"better than 82%" / "worse than 70%" (or "more than 64%" for a stat that isn't good or bad)."""
    if row["good"] is None:
        return f"more than {min(99, max(1, round(row['percentile'])))}%"
    good = min(99, max(1, round(row["good"])))
    return f"better than {good}%" if good >= 50 else f"worse than {100 - good}%"


def rate_player(player: Dict[str, Any], minutes: float, metrics: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """Every stat with its percentile, the overall score and the stand-out stats.

    A stat is left out when the match data doesn't have it (older saved reviews) or when nobody on
    this hero ever does it (all percentiles 0, e.g. healing on a hero with no heals)."""
    rows = []
    for stat in STATS:
        metric = metrics.get(stat.metric)
        try:
            value = stat.value(player, minutes)
        except (KeyError, TypeError):  # not in this data (e.g. read from the end screen, or an old review)
            value = None
        if value is None or not metric or not metric.get("percentile99"):
            continue
        percentile = percentile_of(value, metric)
        good = None if stat.better is None else percentile if stat.better == HIGH else 100 - percentile
        rows.append({"key": stat.key, "label": stat.label, "value": value, "text": stat.fmt.format(value),
                     "percentile": percentile, "good": good, "grade": grade(good) if good is not None else None,
                     "median": metric["percentile50"], "median_text": stat.fmt.format(metric["percentile50"]),
                     "in_score": stat.in_score})
    scored = [r["good"] for r in rows if r["in_score"]]
    score = round(mean(scored)) if scored else None
    graded = sorted((r for r in rows if r["good"] is not None), key=lambda r: -r["good"])
    return {
        "rows": rows,
        "score": score,
        "verdict": verdict(score) if score is not None else None,
        "strengths": [r for r in graded if r["good"] >= 70][:3],
        "weaknesses": [r for r in reversed(graded) if r["good"] <= 30][:2],
    }


def length_window(minutes: Optional[float]) -> tuple:
    """(shortest, longest) match length in whole minutes to compare with: whole minutes, so the
    12 players of a match (and other matches of a similar length) share the same cached answers.
    (None, None) when the length isn't known: any length."""
    if not minutes:
        return None, None
    middle = int(minutes)
    return max(0, middle - MATCH_LENGTH_WINDOW_MIN), middle + MATCH_LENGTH_WINDOW_MIN


def band_for_badge(badge: Optional[float], bands: List[tuple]) -> tuple:
    """The (label, (lowest tier, highest tier)) rank band holding an average badge (tier * 10 +
    subrank), or the "all ranks" band when the rank isn't known."""
    everyone = next(b for b in bands if b[1] is None)
    if not badge:
        return everyone
    tier = int(badge) // 10
    return next((b for b in bands if b[1] and b[1][0] <= tier <= b[1][1]), everyone)
