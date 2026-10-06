"""How lobby results are described, shared by the terminal report and the app window.

The app lays results out as cards and the terminal prints lines, but both use these functions
for the wording, so the two always say the same thing.
"""

from typing import Any, Dict, List, Optional, Tuple

Line = Tuple[str, str]  # (text, style): style is one of team, party, player, note, link, hero, blank

TEAM_TITLES = {"friendly": "YOUR TEAM", "enemy": "ENEMY TEAM"}


def hero_stats_text(r: Dict[str, Any]) -> str:
    """One line about the player's history on the hero they're playing now."""
    if r["status"] == "skipped":
        return "Bot"
    if r["status"] == "not found":
        return f"No exact Steam name match ({r['note']})"
    if r["status"] == "error":
        return f"Lookup failed: {r['note']}"
    if r.get("totals"):  # manual search result: no current hero, so describe all their games
        t = r["totals"]
        recent = f" · {t['recent']} in the last 30 days" if t.get("recent") is not None else ""
        return f"{t['games']:,} games · {t['win_rate']:.0%} WR overall{recent}"
    s = r["hero_stats"]
    if not s:
        return f"No recorded games on {r['hero']}"
    games = f"{s['games']} game" + ("" if s["games"] == 1 else "s")
    return f"{games} · {s['win_rate']:.0%} WR · {s['kda']:.1f} KDA · {s['damage_per_min']:,.0f} dmg/min"


def most_played_text(r: Dict[str, Any]) -> str:
    if not r["top_heroes"]:
        return ""
    # Most played = most games (wins and losses), with the win rate on each
    return "Most played: " + " · ".join(f"{h['hero']} {h['matches']} ({h['win_rate']:.0%})" for h in r["top_heroes"])


def identity_text(r: Dict[str, Any]) -> str:
    """How the account was identified; empty when there was nothing to decide."""
    if r["status"] != "found" or r.get("is_me"):
        return ""
    fixed = f"OCR read {r['corrected_from']!r}; " if r.get("corrected_from") else ""
    if r["note"] == "unique name" and not fixed:
        return ""
    return f"Identified: {fixed}{r['note']}"


def badge_labels(r: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The player's badges, plus how their account was identified when it wasn't a unique name."""
    labels = list(r["badges"])
    if r.get("is_me"):
        labels.insert(0, ("YOU", "you"))
    if r.get("corrected_from"):
        labels.append(("NAME FIXED", "info"))
    if r["status"] == "found":
        if not r["confident"]:
            labels.append(("ID UNSURE", "warn"))
        elif r["note"].startswith("friends with"):
            labels.append(("ID VIA FRIENDS", "info"))
    return labels


def history_labels(record: Dict[str, int]) -> List[str]:
    """Short pills for your record with a player, your wins first: e.g. "FACED 3× · 2-1", "ALLY 4× · 1-3"."""
    if not record:
        return []
    labels = []
    for count, wins, title in ((record["faced"], record["won_against"], "FACED"),
                               (record["teamed"], record["won_with"], "ALLY")):
        if count:
            labels.append(f"{title} {count}× · {wins}-{count - wins}")
    return labels


def history_text(record: Dict[str, int]) -> str:
    """e.g. "You've faced them 3 times (you won 2) and played with them once (won 0)"."""
    if not record:
        return "You haven't played with or against them in a recorded match."
    times = lambda n: "once" if n == 1 else "twice" if n == 2 else f"{n} times"  # noqa: E731
    parts = []
    if record["faced"]:
        parts.append(f"faced them {times(record['faced'])} (you won {record['won_against']})")
    if record["teamed"]:
        parts.append(f"played with them {times(record['teamed'])} (won {record['won_with']})")
    return "You've " + " and ".join(parts)


PROGRESS_LABELS = {"souls_per_min": ("souls/min", "{:,.0f}"), "kda": ("KDA", "{:.1f}"), "win_rate": ("win rate", "{:.0%}")}
CLEAR_CHANGES_SHOWN = 4  # what fits on one line


def change_amount(stat: str, v: Dict[str, Any]) -> str:
    """Win rate changes in points ("12 pts"), the others in percent ("10%")."""
    if stat == "win_rate":
        return f"{abs(v['change']) * 100:.0f} pts"
    return f"{abs(v['change']) / v['before']:.0%}" if v["before"] else ""


def progress_texts(progress: Dict[str, Any]) -> Tuple[str, List[Tuple[str, bool]]]:
    """(the overall comparison, [(a clear change, whether it's an improvement)]), e.g.
    "2,310 → 2,540 souls/min · KDA 3.1 → 3.4 · win rate 45% → 55%" and [("Haze souls/min up 14%", True)]."""
    overall = progress["overall"]
    if not overall:
        return "", []
    summary = " · ".join(f"{name} {fmt.format(overall[stat]['before'])} → {fmt.format(overall[stat]['recent'])}"
                         for stat, (name, fmt) in PROGRESS_LABELS.items())
    clear = [(scope, stat, v) for scope, comparison in [("Overall", overall)] + list(progress["heroes"].items())
             for stat, v in comparison.items() if v["clear"]]
    clear.sort(key=lambda c: -abs(c[2]["change"] / (c[2]["before"] or 1)))  # biggest relative changes first
    return summary, [(f"{scope} {PROGRESS_LABELS[stat][0]} {'up' if v['change'] > 0 else 'down'} {change_amount(stat, v)}",
                      v["change"] > 0) for scope, stat, v in clear[:CLEAR_CHANGES_SHOWN]]


THREAT_HERO_GAMES = 100  # this many games on their current hero counts as a lot of experience on it
MIN_THREAT_REASONS = 2   # one reason alone (e.g. being in a party) doesn't make a threat
THREATS_SHOWN = 3


def threat_reasons(r: Dict[str, Any], top_badge: int, party_size: int) -> List[str]:
    """Why an enemy is worth watching, from facts the lobby already shows."""
    labels = {label for label, _ in r["badges"]}
    s = r["hero_stats"] or {}
    reasons = []
    if "ONE-TRICK" in labels:
        reasons.append("one-trick")
    elif "ON MAIN" in labels:
        reasons.append("main hero")
    if "HIGH WR" in labels:
        reasons.append(f"{s['win_rate']:.0%} WR on {r['hero']}")
    if s.get("games", 0) >= THREAT_HERO_GAMES:
        reasons.append(f"{s['games']:,} games on {r['hero']}")
    if r["rank"] and top_badge and r["rank"]["badge"] == top_badge:
        reasons.append(f"top rank here ({r['rank']['name']})")
    if party_size > 1:
        reasons.append(f"party of {party_size}")
    return reasons


def threats(results: List[Dict[str, Any]], parties: List[List[int]]) -> List[Tuple[int, List[str]]]:
    """The enemies with the most reasons to watch them (at least MIN_THREAT_REASONS), most first:
    [(index into results, reasons)]. Players whose account isn't certain are left out: their stats
    may be someone else's."""
    top_badge = max((r["rank"]["badge"] for r in results if r.get("rank")), default=0)
    party_size = {i: len(party) for party in parties for i in party}
    found = [(i, threat_reasons(r, top_badge, party_size.get(i, 1))) for i, r in enumerate(results)
             if r["team"] == "enemy" and r["status"] == "found" and r["confident"]]
    found = [(i, reasons) for i, reasons in found if len(reasons) >= MIN_THREAT_REASONS]
    found.sort(key=lambda t: (-len(t[1]), -((results[t[0]]["hero_stats"] or {}).get("games", 0))))
    return found[:THREATS_SHOWN]


def party_text(party: List[int], together: Optional[Dict[str, int]] = None) -> str:
    """e.g. "party of 2 (741 games together, 51% WR)"; a bigger party gives its closest pair's games."""
    if not together:
        return f"party of {len(party)}"
    if len(party) == 2:
        return f"party of 2 ({together['games']:,} games together, {together['wins'] / together['games']:.0%} WR)"
    return f"party of {len(party)} (up to {together['games']:,} games together)"


def team_summary(team_results: List[Dict[str, Any]], team_parties: List[List[int]],
                 party_games: Optional[Dict[tuple, Dict[str, int]]] = None) -> str:
    """One line about a team, e.g. "4 players · party of 3 · 3 new on hero · best rank Oracle 4".
    party_games: profiles.party_games by tuple(party), once loaded."""
    parts = [f"{len(team_results)} player" + ("" if len(team_results) == 1 else "s")]
    parts += [party_text(p, (party_games or {}).get(tuple(p))) for p in team_parties]
    labels = [label for r in team_results for label, _ in r["badges"]]
    one_tricks = labels.count("ONE-TRICK")
    new = labels.count("NEW ON HERO") + labels.count("FIRST GAME ON HERO")
    if one_tricks:
        parts.append(f"{one_tricks} one-trick" + ("s" if one_tricks > 1 else ""))
    if new:
        parts.append(f"{new} new on hero")
    ranked = [r["rank"] for r in team_results if r["rank"] and r["rank"]["badge"] > 0]
    if ranked:
        parts.append("best rank " + max(ranked, key=lambda rank: rank["badge"])["name"])
    return " · ".join(parts)


MATCHUP_NOTABLE = 0.03  # 3 points above/below the hero's average counts as a good/bad matchup


def matchup_kind(vs_average: float) -> str:
    """"good", "bad" or "even" for colouring a matchup."""
    if vs_average >= MATCHUP_NOTABLE:
        return "good"
    if vs_average <= -MATCHUP_NOTABLE:
        return "bad"
    return "even"


def matchup_text(m: Dict[str, Any]) -> str:
    """e.g. "Victor 43% (-7)": win rate, and points above/below the hero's average."""
    return f"{m['enemy_hero']} {m['win_rate']:.0%} ({m['vs_average'] * 100:+.0f})"


def items_text(matchup: Dict[str, Any]) -> str:
    return "Popular vs this team: " + " · ".join(f"{i['name']} ({i['win_rate']:.0%})" for i in matchup["items"])


def build_report(results: List[Dict[str, Any]], parties: List[List[int]]) -> List[Line]:
    """Terminal version of the report, as (text, style) lines."""
    if not results:
        return [("No players found in the screenshot.", "note")]

    lines = []
    for team, title in TEAM_TITLES.items():
        members = [r for r in results if r["team"] == team]
        team_parties = [p for p in parties if results[p[0]]["team"] == team]
        lines.append((f"{title}  ({team_summary(members, team_parties)})", "team"))
        for party in parties:
            if results[party[0]]["team"] == team:
                lines.append((f"Party of {len(party)}: {' + '.join(results[i]['player'] for i in party)}", "party"))
        for r in results:
            if r["team"] != team:
                continue
            rank = f"  {r['rank']['name']}" if r["rank"] else ""
            lines.append((f"{r['player']}  ({r['hero']}){rank}", "player"))
            lines.append((f"    {hero_stats_text(r)}", "hero"))
            badges = badge_labels(r)
            if badges:
                lines.append(("    " + " ".join(f"[{label}]" for label, _ in badges), "note"))
            if r.get("history"):
                lines.append(("    Met before: " + " · ".join(history_labels(r["history"])), "note"))
            if r.get("my_note"):
                lines.append((f"    Your note: {r['my_note']}", "note"))
            for extra in (most_played_text(r), identity_text(r)):
                if extra:
                    lines.append((f"    {extra}", "note"))
            if r["profile_url"]:
                lines.append((f"    {r['profile_url']}", "link"))
        lines.append(("", "blank"))
    return lines
