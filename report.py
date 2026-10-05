"""How lobby results are described, shared by the terminal report and the app window.

The app lays results out as cards and the terminal prints lines, but both use these functions
for the wording, so the two always say the same thing.
"""

from typing import Any, Dict, List, Tuple

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


def team_summary(team_results: List[Dict[str, Any]], team_parties: List[List[int]]) -> str:
    """One line about a team, e.g. "4 players · party of 3 · 3 new on hero · best rank Oracle 4"."""
    parts = [f"{len(team_results)} player" + ("" if len(team_results) == 1 else "s")]
    parts += [f"party of {len(p)}" for p in team_parties]
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
