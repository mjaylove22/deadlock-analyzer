"""The Coach tab: patterns across your last COACH_MATCHES normal matches. Where and how you die,
how your laning goes, your strongest and weakest stats against players on the same hero and rank,
and what's changing, with a tip for each finding.

The facts come from each match's full data (match_review.coach_facts), so only matches the API has
stored count: the newest game usually arrives a few hours after it's played. The analysis itself
(coach_report) is plain logic over those summaries, so it's tested without the network.
"""

from collections import Counter
from statistics import fmean
from typing import Any, Dict, List, Optional

import assets
import deadlock_api
from match_review import MatchUnavailable, get_summary
from performance import STATS, band_for_badge, length_window, rate_player
from profiles import RANK_BANDS, compare_windows

COACH_MATCHES = 15  # the author's pick: enough to see patterns, ~20 MB to fetch the first time
# Calibrated on 1,211 deaths in the author's 15 lobbies: nearest-teammate distances fall off smoothly up
# to ~3,000 units and are flat beyond, and 26% of all deaths were that far. "Alone" is judged against
# that lobby-wide rate rather than an absolute standard.
ALONE_UNITS = 3000
QUICK_DEATH_S = 5    # the fastest 10% of those deaths: caught or burst down
PHASES = [(600, "laning"), (1500, "mid game"), (10 ** 9, "late game")]  # (before this game time, name)
NOTABLE_GAP = 0.10   # your rate this far above the lobbies' is worth a tip
MIN_DEATHS = 5       # fewer deaths than this say nothing about patterns
TREND_MIN_MATCHES = 10  # the trend compares two halves: fewer and each half is mostly noise
STRENGTH, WEAKNESS = 60, 50  # average percentile on your hero (100 = best): above 60 a strength, below 50 below average

STAT_TIPS = {  # performance.STATS key -> what to work on when it's a weak spot
    "souls": "Keep farming between fights: clear the wave and nearby camps instead of walking around empty-handed.",
    "damage": "Take more fights, and shoot heroes whenever you're safe to, not only creeps.",
    "deaths": "Play the first seconds of a fight from further back, and leave before you're low rather than after.",
    "objectives": "Hit guardians and walkers whenever your wave is pushing: objectives win games, kills don't.",
    "last_hits": "Last-hit the creeps in your lane: they're your steadiest souls.",
    "accuracy": "Fight at the range your weapon is accurate at, and track the target's body rather than spraying.",
    "crits": "Aim for the head: crits are free damage, especially at range.",
}


MAP_IMAGE_SIDE = 600  # the minimap is 1024 px and shown ~280 px: stored at about twice that


def load_map() -> Optional[Dict[str, Any]]:
    """The minimap and the radius of the world it covers, from the API's assets (worker thread);
    None if either can't be had. World (x, y) maps to ((x + r) / 2r, 1 - (y + r) / 2r) of the image:
    checked by drawing real players' paths, which follow the streets."""
    try:
        meta = deadlock_api.get_json("/v1/assets/map", max_age=7 * 86400)
    except (OSError, ValueError):
        return None
    image = assets.load(meta.get("images", {}).get("minimap"), MAP_IMAGE_SIDE)
    return {"radius": meta["radius"], "image": image} if image and meta.get("radius") else None


def phase(t: int) -> str:
    return next(name for end, name in PHASES if t < end)


def share(part: int, whole: int) -> Optional[float]:
    return part / whole if whole else None


def coach_report(matches: List[Dict[str, Any]], hero: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """What the Coach tab shows for load_matches()["matches"] (newest first), optionally only the matches on
    one hero. Lobby baselines always use every loaded match: the other 11 players are the yardstick.
    None when there are no matches."""
    mine = [e for e in matches if hero is None or e["me"]["hero"] == hero]
    if not mine:
        return None
    everyone = [p for e in matches for p in e["summary"]["players"]]
    lobby_deaths = [d for p in everyone for d in p["death_list"] if d["mate"] is not None]
    lobby_rates = sorted(p["lane"]["last_hits"] / p["lane"]["possible"] for p in everyone if p["lane"] and p["lane"]["possible"])

    deaths = [dict(d, alone=d["mate"] is not None and d["mate"] >= ALONE_UNITS) for e in mine for d in e["me"]["death_list"]]
    measured = [d for d in deaths if d["mate"] is not None]
    alone = [d for d in measured if d["alone"]]
    report = {
        "games": len(mine), "hero": hero,
        "deaths": {
            "count": len(deaths), "per_game": len(deaths) / len(mine),
            "alone": len(alone), "alone_share": share(len(alone), len(measured)),
            "lobby_alone_share": share(sum(d["mate"] >= ALONE_UNITS for d in lobby_deaths), len(lobby_deaths)),
            "quick": sum(d["fight_s"] <= QUICK_DEATH_S for d in deaths),
            "quick_share": share(sum(d["fight_s"] <= QUICK_DEATH_S for d in deaths), len(deaths)),
            "lobby_quick_share": share(sum(d["fight_s"] <= QUICK_DEATH_S for p in everyone for d in p["death_list"]),
                                       sum(len(p["death_list"]) for p in everyone)),
            "phases": {name: sum(phase(d["t"]) == name for d in deaths) / len(mine) for _, name in PHASES},
            "lobby_phases": {name: sum(phase(d["t"]) == name for p in everyone for d in p["death_list"]) / len(everyone)
                             for _, name in PHASES},
            "alone_phase": Counter(phase(d["t"]) for d in alone).most_common(1)[0][0] if alone else None,
            "killers": Counter(d["killer"] for d in deaths if d["killer"]).most_common(3),
            "souls_lost_per_game": fmean(e["me"]["souls_lost"] for e in mine),
            "points": [(d["x"], d["y"], d["alone"]) for d in deaths],
        },
    }

    lanes = [e["me"]["lane"] for e in mine if e["me"]["lane"] and e["me"]["lane"]["possible"]]
    if lanes:
        rate = sum(l["last_hits"] for l in lanes) / sum(l["possible"] for l in lanes)
        report["laning"] = {
            "rate": rate, "better_than": share(sum(r < rate for r in lobby_rates), len(lobby_rates)),
            "denies": fmean(l["denies"] for l in lanes), "net_worth": fmean(l["net_worth"] for l in lanes),
            "minute": lanes[0]["minute"],
        }

    totals, lobby_totals = Counter(), Counter()
    for e in mine:
        totals.update(e["me"]["sources"])
    for p in everyone:
        lobby_totals.update(p["sources"])
    report["sources"] = {key: (share(totals[key], sum(totals.values())), share(lobby_totals[key], sum(lobby_totals.values())))
                         for key in lobby_totals}  # key -> (your share, the lobbies' share)

    # Strengths and weak spots: your average percentile on your hero, at the match's rank and length
    rated = [e["rating"] for e in mine if e.get("rating")]
    by_stat: Dict[str, List[float]] = {}
    for rating in rated:
        for row in rating["rows"]:
            if row["good"] is not None:
                by_stat.setdefault(row["key"], []).append(row["good"])
    labels = {s.key: s.label for s in STATS}
    averages = sorted(((key, fmean(values)) for key, values in by_stat.items() if len(values) >= len(rated) / 2),
                      key=lambda kv: -kv[1])
    report["stats"] = [{"key": k, "label": labels[k], "good": v} for k, v in averages]  # best first, for the bars
    report["strengths"] = [{"key": k, "label": labels[k], "good": v} for k, v in averages if v >= STRENGTH][:3]
    report["weaknesses"] = [{"key": k, "label": labels[k], "good": v} for k, v in reversed(averages) if v <= WEAKNESS][:2]
    report["rated"] = len(rated)

    # What's changing: the newer half of these matches against the older half
    halves = len(mine) // 2
    report["trend"] = compare_windows(mine, halves, {
        "souls_per_min": lambda e: e["me"]["net_worth"] / e["summary"]["minutes"],
        "deaths": lambda e: e["me"]["deaths"],
        "alone_deaths": lambda e: sum(d["mate"] is not None and d["mate"] >= ALONE_UNITS for d in e["me"]["death_list"]),
    }) if len(mine) >= TREND_MIN_MATCHES else None
    report["trend_games"] = halves
    report["tips"] = tips(report)
    return report


TREND_WORDS = {"souls_per_min": ("souls per minute", "{:,.0f}", True), "deaths": ("deaths a game", "{:.1f}", False),
               "alone_deaths": ("deaths alone a game", "{:.1f}", False)}


def tips(report: Dict[str, Any]) -> List[Dict[str, str]]:
    """Findings worth acting on, most important first: [{"kind": "work" | "good", "text"}]. Each one only
    when the numbers clearly say so, measured against the same lobbies or players on the same hero."""
    found = []
    d = report["deaths"]
    if d["count"] >= MIN_DEATHS and d["alone_share"] is not None and d["lobby_alone_share"] is not None \
            and d["alone_share"] >= d["lobby_alone_share"] + NOTABLE_GAP:
        found.append({"kind": "work", "text":
                      f"{d['alone']} of your {d['count']} deaths came with no teammate nearby ({d['alone_share']:.0%}, against "
                      f"{d['lobby_alone_share']:.0%} for everyone in your lobbies), mostly in the {d['alone_phase']}. Before farming "
                      "or pushing on your own, check the map for enemies you can't see, and head back toward your team when "
                      "two or more are missing."})
    if d["killers"] and d["count"] >= MIN_DEATHS:
        killer, times = d["killers"][0]
        if times >= 3 and times / d["count"] >= 0.25:
            found.append({"kind": "work", "text":
                          f"{killer} killed you {times} times, {times / d['count']:.0%} of your deaths. {killer}'s page under "
                          "Heroes shows their abilities and how players build them: knowing what's coming is half the counter."})
    if d["count"] >= MIN_DEATHS and d["quick_share"] is not None and d["lobby_quick_share"] is not None \
            and d["quick_share"] >= d["lobby_quick_share"] + NOTABLE_GAP:
        found.append({"kind": "work", "text":
                      f"{d['quick']} of your deaths took {QUICK_DEATH_S} seconds or less, so you were caught or burst down "
                      f"({d['quick_share']:.0%}, against {d['lobby_quick_share']:.0%} in your lobbies). Keep an escape ready "
                      "when you walk into the open, and consider more health or a defensive item."})
    lane = report.get("laning")
    if lane and lane["better_than"] is not None:
        if lane["better_than"] < 0.35:
            found.append({"kind": "work", "text":
                          f"In lane you last-hit {lane['rate']:.0%} of the creeps you could have by minute {lane['minute']}, "
                          f"fewer than {1 - lane['better_than']:.0%} of the players in your lobbies. {STAT_TIPS['last_hits']}"})
        elif lane["better_than"] >= 0.70:
            found.append({"kind": "good", "text":
                          f"Strong laning: you last-hit {lane['rate']:.0%} of the creeps by minute {lane['minute']}, more than "
                          f"{lane['better_than']:.0%} of the players in your lobbies."})
    for w in report["weaknesses"]:
        if w["key"] in STAT_TIPS:
            found.append({"kind": "work", "text":
                          f"{w['label']}: {'slightly ' if w['good'] > WEAKNESS - 10 else ''}worse than average, behind "
                          f"{100 - w['good']:.0f}% of players on the same hero, rank and "
                          f"match length. {STAT_TIPS[w['key']]}"})
    objectives, lobby_objectives = report["sources"].get("objectives", (None, None))
    if objectives is not None and lobby_objectives and objectives <= lobby_objectives * 0.6:
        found.append({"kind": "work", "text":
                      f"Only {objectives:.0%} of your souls come from objectives, against {lobby_objectives:.0%} for your lobbies. "
                      f"{STAT_TIPS['objectives']}"})
    late, lobby_late = d["phases"]["late game"], d["lobby_phases"]["late game"]
    if d["count"] >= MIN_DEATHS and lobby_late and late >= lobby_late * 1.3:
        found.append({"kind": "work", "text":
                      f"You die {late:.1f} times a game after minute {PHASES[1][0] // 60}, against {lobby_late:.1f} for your lobbies. "
                      "Late deaths cost the most (long respawns, lost objectives): stick with your team and don't chase."})
    for s in report["strengths"][:1]:
        found.append({"kind": "good", "text":
                      f"{s['label']} is your strongest stat: ahead of {s['good']:.0f}% of players on the same hero, rank and "
                      "match length."})
    for key, change in (report["trend"] or {}).items():
        if change["clear"]:
            words, fmt, higher_is_better = TREND_WORDS[key]
            better = (change["change"] > 0) == higher_is_better
            found.append({"kind": "good" if better else "work", "text":
                          f"Your {words} went {'up' if change['change'] > 0 else 'down'} from {fmt.format(change['before'])} to "
                          f"{fmt.format(change['recent'])} over your last {report['trend_games']} matches."})
    return found


def load_matches(account_id: int, hero_names_by_id: Dict[int, str]) -> Dict[str, Any]:
    """Your newest COACH_MATCHES normal matches, condensed, each with your rating on your hero
    (worker thread): {"matches": [{"summary", "me", "rating"}] newest first, "waiting": matches not stored yet}.
    Only the API's stored copies are used: the 3 Steam fetches an hour stay free for the post-game review."""
    history = sorted(deadlock_api.get_match_history(account_id), key=lambda m: m["start_time"], reverse=True)
    wanted = [m for m in history if m["game_mode"] == 1 and m["match_duration_s"] > 0][:COACH_MATCHES]

    def summary(match_id):
        try:
            return get_summary(match_id, hero_names_by_id, allow_steam=False)
        except MatchUnavailable:
            return None
    summaries = deadlock_api.parallel(*[lambda m=m: summary(m["match_id"]) for m in wanted], allow_failures=True)
    found = []
    for s in summaries:
        me = next((p for p in (s or {}).get("players", []) if p["account_id"] == account_id), None)
        if me and "death_list" in me:
            found.append({"summary": s, "me": me})

    # Your rating on your hero in each match: one ~10 KB request per hero, rank band and length
    ids_by_name = {name: hero_id for hero_id, name in hero_names_by_id.items()}

    def metrics(entry):
        s, hero = entry["summary"], entry["me"]["hero"]
        badges = [b for b in s["team_badges"] if b]
        band = band_for_badge(sum(badges) / len(badges) if badges else None, RANK_BANDS)
        low, high = length_window(s["minutes"])
        return deadlock_api.get_player_metrics(ids_by_name[hero], "normal", band[1], low, high) if hero in ids_by_name else None
    answers = deadlock_api.parallel(*[lambda e=e: metrics(e) for e in found], allow_failures=True)
    for entry, answer in zip(found, answers):
        entry["rating"] = rate_player(entry["me"], entry["summary"]["minutes"], answer) if answer else None
    return {"matches": found, "waiting": len(wanted) - len(found)}
