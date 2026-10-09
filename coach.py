"""The Coach tab: patterns across your last COACH_MATCHES normal matches. Where and how you die,
how your laning goes, your strongest and weakest stats against players on the same hero and rank,
and what's changing. Every finding carries how sure it is (LEVELS), from its sample size and the
size of the gap, and only findings with enough evidence are shown.

The facts come from each match's full data (match_review.coach_facts), so only matches the API has
stored count: the newest game usually arrives a few hours after it's played. The analysis itself
(coach_report) is plain logic over those summaries, so it's tested without the network.
"""

import math
from collections import Counter
from statistics import fmean, stdev
from typing import Any, Dict, List, Optional

import assets
import deadlock_api
from match_review import MatchUnavailable, get_summary
from performance import STATS, band_for_badge, length_window, rate_player
from profiles import RANK_BANDS, compare_windows

COACH_MATCHES = 30  # enough for the newer half to be compared with the older; ~45 MB to fetch the first time
# Calibrated on 1,211 deaths in the author's 15 lobbies: nearest-teammate distances fall off smoothly up
# to ~3,000 units and are flat beyond, and 26% of all deaths were that far. "Alone" is judged against
# that lobby-wide rate rather than an absolute standard.
ALONE_UNITS = 3000
QUICK_DEATH_S = 5    # the fastest 10% of those deaths: caught or burst down
PHASES = [(600, "laning"), (1500, "mid game"), (10 ** 9, "late game")]  # (before this game time, name)
PHASE_NAMES = {"laning": "the laning phase", "mid game": "the mid game", "late game": "the late game"}

# How sure a finding is. Each check compares you with a yardstick match by match, and z is how many
# standard errors of those per-match differences you are away from it: about 1 in 15 chance at 1.5 by
# luck alone, 1 in 40 at 2, 1 in 700 at 3. Matches are the unit (not deaths), because deaths in one match
# aren't independent. A strong z on very few matches still isn't trusted, hence the minimum matches.
LEVELS = [(3.0, 15, "consistent"), (2.0, 8, "likely"), (1.5, 5, "early")]  # (z, matches, level), strongest first
LEVEL_WORDS = {"consistent": "Consistent pattern", "likely": "Likely", "early": "Early sign"}
MIN_KILLER_GAMES = 5  # a hero must have been against you this often before their kills say anything
MIN_TREND_GAMES = 5   # matches on each side of a trend: 3 against 3 is mostly luck, however big the change

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


def share(part: float, whole: float) -> Optional[float]:
    return part / whole if whole else None


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 4th, ... 11th, 12th, 13th, 21st."""
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def is_alone(death: Dict[str, Any]) -> bool:
    return death["mate"] is not None and death["mate"] >= ALONE_UNITS


def level(z: float, matches: int) -> Optional[str]:
    """How strongly z supports a finding resting on this many matches (see LEVELS); None: not enough."""
    return next((name for threshold, minimum, name in LEVELS if abs(z) >= threshold and matches >= minimum), None)


def mean_z(values: List[float], expected: float = 0.0, floor: float = 0.0) -> float:
    """How many standard errors the mean of values is from expected (0 with under 2 values). floor: the
    smallest spread to assume, so a few identical values don't look infinitely certain."""
    if len(values) < 2:
        return 0.0
    error = max(stdev(values), floor) / math.sqrt(len(values))
    return (fmean(values) - expected) / error if error else 0.0


def example(e: Dict[str, Any]) -> Dict[str, Any]:
    """The match a finding or a death links to: the Coach page opens its review."""
    return {"match_id": e["summary"]["match_id"], "start_time": e["summary"].get("start_time"), "hero": e["me"]["hero"]}


def finding(kind: str, z: float, matches: int, topic: str, title: str, text: str, tip: str = "",
            shown_in: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """A finding worth showing, or None when the evidence is too thin. kind: "work" or "good"; title: a few
    words for the summary; text: the observation with its numbers; tip: what to do about it; shown_in: the
    match where it showed most (example()), to open and see it happen."""
    strength = level(z, matches)
    if not strength:
        return None
    return {"kind": kind, "z": abs(z), "matches": matches, "level": strength, "topic": topic, "title": title,
            "text": text, "tip": tip, "example": shown_in}


PHASE_TIPS = {
    "laning": "Trade only when their key ability is down, and step back when the wave is on their side.",
    "mid game": "Mid-game fights start around objectives: arrive with your team rather than one at a time.",
    "late game": "Late deaths cost the most (long respawns, lost objectives): stay with your team and don't chase.",
}


def death_findings(mine: List[Dict[str, Any]], everyone: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Patterns in how you die, each judged match by match against everyone in the loaded lobbies."""
    found = []
    n = len(mine)
    lobby_measured = [d for p in everyone for d in p["death_list"] if d["mate"] is not None]
    lobby_alone = share(sum(map(is_alone, lobby_measured)), len(lobby_measured))
    with_deaths = [e for e in mine if any(d["mate"] is not None for d in e["me"]["death_list"])]
    if lobby_alone is not None and with_deaths:
        values = [share(sum(map(is_alone, e["me"]["death_list"])), sum(d["mate"] is not None for d in e["me"]["death_list"]))
                  for e in with_deaths]
        z = mean_z(values, lobby_alone, floor=0.15)
        deaths = [d for e in with_deaths for d in e["me"]["death_list"] if d["mate"] is not None]
        alone = [d for d in deaths if is_alone(d)]
        when = Counter(phase(d["t"]) for d in alone).most_common(1)
        text = (f"{len(alone)} of your {len(deaths)} deaths came with no teammate within {ALONE_UNITS:,} units "
                f"({share(len(alone), len(deaths)):.0%}, against {lobby_alone:.0%} for everyone in your lobbies)")
        by_alone = sorted(with_deaths, key=lambda e: sum(map(is_alone, e["me"]["death_list"])))
        if z > 0:
            found.append(finding("work", z, len(with_deaths), "deaths", "Dying away from your team",
                                 text + (f", mostly in {PHASE_NAMES[when[0][0]]}." if when else "."),
                                 "Before farming or pushing on your own, check the map for enemies you can't see, and head "
                                 "back toward your team when two or more are missing.", example(by_alone[-1])))
        else:
            found.append(finding("good", z, len(with_deaths), "deaths", "Staying near your team", text + ".",
                                 shown_in=example(by_alone[0])))

    lobby_rate = {name: sum(phase(d["t"]) == name for p in everyone for d in p["death_list"]) / max(len(everyone), 1)
                  for _, name in PHASES}
    phase_z = {name: mean_z([sum(phase(d["t"]) == name for d in e["me"]["death_list"]) for e in mine], lobby_rate[name], floor=0.5)
               for _, name in PHASES}
    worst = max(phase_z, key=phase_z.get)
    if phase_z[worst] > 0:
        in_phase = lambda e: sum(phase(d["t"]) == worst for d in e["me"]["death_list"])  # noqa: E731
        mine_rate = sum(map(in_phase, mine)) / n
        found.append(finding("work", phase_z[worst], n, "deaths", f"Deaths in {PHASE_NAMES[worst]}",
                             f"You die most in {PHASE_NAMES[worst]}: {mine_rate:.1f} times a game, against {lobby_rate[worst]:.1f} "
                             "for the players in your lobbies.", PHASE_TIPS[worst], example(max(mine, key=in_phase))))
    # Killers, fairly: deaths to a hero against what you'd expect if every enemy were equally likely to kill you
    killers = []
    for hero in {p["hero"] for e in mine for p in e["summary"]["players"] if p["team"] != e["me"]["team"]}:
        games = [e for e in mine if any(p["hero"] == hero and p["team"] != e["me"]["team"] for p in e["summary"]["players"])]
        if len(games) < MIN_KILLER_GAMES:
            continue
        enemies = lambda e: sum(p["team"] != e["me"]["team"] for p in e["summary"]["players"])  # noqa: E731
        by_hero = [sum(d["killer"] == hero for d in e["me"]["death_list"]) for e in games]
        expected = [len(e["me"]["death_list"]) / max(enemies(e), 1) for e in games]
        z = mean_z([b - x for b, x in zip(by_hero, expected)], 0.0, floor=0.5)
        killers.append((z, hero, len(games), sum(by_hero), sum(expected), example(games[by_hero.index(max(by_hero))])))
    if killers:
        z, hero, games, kills, expected, worst_game = max(killers, key=lambda k: k[:5])
        if z > 0:
            found.append(finding("work", z, games, "deaths", f"{hero} kills you a lot",
                                 f"{hero} killed you {kills} times in the {games} games they were against you; about "
                                 f"{expected:.0f} would be expected if every enemy were equally likely to kill you (an "
                                 "assumption: some heroes are built to get kills).",
                                 f"{hero}'s page under Heroes shows their abilities and how players build them: knowing "
                                 "what's coming is half the counter.", worst_game))

    lobby_quick = share(sum(d["fight_s"] <= QUICK_DEATH_S for p in everyone for d in p["death_list"]),
                        sum(len(p["death_list"]) for p in everyone))
    died = [e for e in mine if e["me"]["death_list"]]
    if lobby_quick is not None and died:
        z = mean_z([share(sum(d["fight_s"] <= QUICK_DEATH_S for d in e["me"]["death_list"]), len(e["me"]["death_list"]))
                    for e in died], lobby_quick, floor=0.15)
        caught = lambda e: sum(d["fight_s"] <= QUICK_DEATH_S for d in e["me"]["death_list"])  # noqa: E731
        quick = sum(map(caught, died))
        total = sum(len(e["me"]["death_list"]) for e in died)
        if z > 0:
            found.append(finding("work", z, len(died), "deaths", "Getting caught",
                                 f"{quick} of your {total} deaths took {QUICK_DEATH_S} seconds or less, so you were caught or "
                                 f"burst down ({share(quick, total):.0%}, against {lobby_quick:.0%} in your lobbies).",
                                 "Keep an escape ready when you walk into the open, and consider more health or a defensive item.",
                                 example(max(died, key=caught))))
    return [f for f in found if f]


def lobby_share(e: Dict[str, Any], value) -> Optional[float]:
    """The average of value(player) over the other players in a match (None-valued players left out)."""
    others = [v for p in e["summary"]["players"] if p is not e["me"] for v in [value(p)] if v is not None]
    return fmean(others) if others else None


def lane_rate(p: Dict[str, Any]) -> Optional[float]:
    return p["lane"]["last_hits"] / p["lane"]["possible"] if p.get("lane") and p["lane"]["possible"] else None


def objective_share(p: Dict[str, Any]) -> Optional[float]:
    return share(p["sources"]["objectives"], sum(p["sources"].values()))


def play_findings(mine: List[Dict[str, Any]], labels: Dict[str, str]) -> List[Dict[str, Any]]:
    """Your stats on your hero (against players on the same hero, rank and length) and your laning and
    objectives (against the rest of the same lobbies)."""
    found = []
    by_stat: Dict[str, List[tuple]] = {}  # key -> [(percentile, match)]
    for e in mine:
        for row in (e.get("rating") or {}).get("rows", []):
            if row["good"] is not None:
                by_stat.setdefault(row["key"], []).append((row["good"], e))
    for key, rated in by_stat.items():
        if key == "kda":  # made of kills, deaths and assists, which are judged on their own
            continue
        values = [v for v, _ in rated]
        z, average = mean_z(values, 50, floor=10), fmean(values)
        if z < 0 and key in STAT_TIPS:  # a weak spot with nothing to act on (e.g. healing, mostly the build) isn't a finding
            found.append(finding("work", z, len(values), "stats", f"{labels[key]}",
                                 f"{labels[key]}: on average behind {100 - average:.0f}% of players on the same hero, rank "
                                 "and match length.", STAT_TIPS.get(key, ""), example(min(rated, key=lambda r: r[0])[1])))
        elif z > 0:
            found.append(finding("good", z, len(values), "stats", f"{labels[key]}",
                                 f"{labels[key]}: on average ahead of {average:.0f}% of players on the same hero, rank and "
                                 "match length.", shown_in=example(max(rated, key=lambda r: r[0])[1])))
    for title, value, floor, words, tip in (
            ("Last-hitting in lane", lane_rate, 0.05, "of the creeps you could have by minute 9", STAT_TIPS["last_hits"]),
            ("Souls from objectives", objective_share, 0.03, "of your souls from objectives", STAT_TIPS["objectives"])):
        pairs = [(value(e["me"]), lobby_share(e, value), e) for e in mine]
        pairs = [(a, b, e) for a, b, e in pairs if a is not None and b is not None]
        if len(pairs) < 2:
            continue
        z = mean_z([a - b for a, b, _ in pairs], 0.0, floor=floor)
        mine_avg, theirs = fmean(a for a, _, _ in pairs), fmean(b for _, b, _ in pairs)
        text = f"{title}: {mine_avg:.0%} {words}, against {theirs:.0%} for the rest of your lobbies."
        gap = lambda p: p[0] - p[1]  # noqa: E731
        found.append(finding("work", z, len(pairs), "play", title, text, tip, example(min(pairs, key=gap)[2])) if z < 0 else
                     finding("good", z, len(pairs), "play", title, text, shown_in=example(max(pairs, key=gap)[2])))
    return [f for f in found if f]


# What a trend tracks: key -> (label, value in one match or None, format, higher is better)
TREND_STATS = {
    "deaths": ("Deaths a game", lambda e: e["me"]["deaths"], "{:.1f}", False),
    "alone": ("Deaths away from your team", lambda e: sum(map(is_alone, e["me"]["death_list"])), "{:.1f}", False),
    "lane": ("Lane last-hit rate", lambda e: lane_rate(e["me"]), "{:.0%}", True),
}


def trends(mine: List[Dict[str, Any]], labels: Dict[str, str]) -> List[Dict[str, Any]]:
    """Each tracked stat in your newer matches against the older ones (halves of the matches that have it),
    with how sure the change is: [{"key", "label", "before", "recent", "z", "better", "level", "games"}].
    Stats on your hero are compared as percentiles, so switching heroes doesn't fake a trend."""
    tracked = dict(TREND_STATS)
    for key, label in labels.items():
        if key != "kda":
            tracked[key] = (label, lambda e, key=key: next((r["good"] for r in (e.get("rating") or {}).get("rows", [])
                                                             if r["key"] == key and r["good"] is not None), None),
                            "percentile", True)
    result = []
    for key, (label, value, fmt, higher_is_better) in tracked.items():
        values = [v for v in (value(e) for e in mine) if v is not None]  # newest first
        half = len(values) // 2
        change = compare_windows(values, half, {"v": lambda v: v}) if half >= MIN_TREND_GAMES else None
        if not change:
            continue
        change = change["v"]
        show = (lambda v: ordinal(round(v)) + " percentile") if fmt == "percentile" else fmt.format
        result.append({"key": key, "label": label, "before": show(change["before"]), "recent": show(change["recent"]),
                       "z": change["z"], "better": (change["change"] > 0) == higher_is_better, "games": half,
                       "level": level(change["z"], half)})  # judged on one side's matches: 5 values make a shaky spread
    return sorted(result, key=lambda t: -abs(t["z"]))


def coach_report(matches: List[Dict[str, Any]], hero: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """What the Coach tab shows for load_matches()["matches"] (newest first), optionally only the matches on
    one hero. Lobby yardsticks always use every loaded match: the other 11 players in each. None when
    there are no matches."""
    mine = [e for e in matches if hero is None or e["me"]["hero"] == hero]
    if not mine:
        return None
    everyone = [p for e in matches for p in e["summary"]["players"]]
    labels = {s.key: s.label.replace(" (self and allies)", "") for s in STATS if s.better is not None}

    deaths = [dict(d, alone=is_alone(d), match=example(e)) for e in mine for d in e["me"]["death_list"]]
    measured = [d for d in deaths if d["mate"] is not None]
    lobby_measured = [d for p in everyone for d in p["death_list"] if d["mate"] is not None]
    lobby_deaths = [d for p in everyone for d in p["death_list"]]
    report = {
        "games": len(mine), "hero": hero, "rated": sum(1 for e in mine if e.get("rating")),
        "deaths": {
            "count": len(deaths), "per_game": len(deaths) / len(mine),
            "alone_share": share(sum(d["alone"] for d in measured), len(measured)),
            "lobby_alone_share": share(sum(map(is_alone, lobby_measured)), len(lobby_measured)),
            "quick_share": share(sum(d["fight_s"] <= QUICK_DEATH_S for d in deaths), len(deaths)),
            "lobby_quick_share": share(sum(d["fight_s"] <= QUICK_DEATH_S for d in lobby_deaths), len(lobby_deaths)),
            "phases": {name: sum(phase(d["t"]) == name for d in deaths) / len(mine) for _, name in PHASES},
            "lobby_phases": {name: sum(phase(d["t"]) == name for d in lobby_deaths) / len(everyone) for _, name in PHASES},
            "killers": Counter(d["killer"] for d in deaths if d["killer"]).most_common(3),
            "souls_lost_per_game": fmean(e["me"]["souls_lost"] for e in mine),
            "points": deaths,  # each with x, y, alone, and the match it happened in
        },
    }
    lanes = [e["me"]["lane"] for e in mine if lane_rate(e["me"]) is not None]
    if lanes:
        report["laning"] = {"rate": sum(l["last_hits"] for l in lanes) / sum(l["possible"] for l in lanes),
                            "lobby_rate": fmean(r for p in everyone for r in [lane_rate(p)] if r is not None),
                            "denies": fmean(l["denies"] for l in lanes), "net_worth": fmean(l["net_worth"] for l in lanes),
                            "minute": lanes[0]["minute"]}
    totals, lobby_totals = Counter(), Counter()
    for e in mine:
        totals.update(e["me"]["sources"])
    for p in everyone:
        lobby_totals.update(p["sources"])
    report["sources"] = {key: (share(totals[key], sum(totals.values())), share(lobby_totals[key], sum(lobby_totals.values())))
                         for key in lobby_totals}  # key -> (your share, the lobbies' share)
    by_stat: Dict[str, List[float]] = {}
    for e in mine:
        for row in (e.get("rating") or {}).get("rows", []):
            if row["good"] is not None:
                by_stat.setdefault(row["key"], []).append(row["good"])
    report["stats"] = sorted(({"key": k, "label": labels.get(k, k), "good": fmean(v), "games": len(v)} for k, v in by_stat.items()),
                             key=lambda s: -s["good"])  # best first, for the bars

    report["death_findings"] = sorted(death_findings(mine, everyone), key=lambda f: -f["z"])
    report["findings"] = sorted(report["death_findings"] + play_findings(mine, labels), key=lambda f: -f["z"])
    report["trends"] = trends(mine, labels)
    for t in report["trends"]:
        if t["level"]:
            report["findings"].append(
                {"kind": "good" if t["better"] else "work", "z": abs(t["z"]), "matches": 2 * t["games"], "level": t["level"],
                 "topic": "trend", "title": f"{t['label']} {'improving' if t['better'] else 'slipping'}",
                 "text": f"{t['label']}: {t['recent']} in your last {t['games']} matches, against {t['before']} in the "
                         f"{t['games']} before.", "tip": ""})
    report["findings"].sort(key=lambda f: -f["z"])
    report["weakness"] = next((f for f in report["findings"] if f["kind"] == "work" and f["topic"] != "trend"), None)
    report["improvement"] = next((f for f in report["findings"] if f["kind"] == "good" and f["topic"] == "trend"), None)
    return report


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
