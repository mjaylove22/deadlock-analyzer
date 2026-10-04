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
    s = r["hero_stats"]
    if not s:
        return f"No recorded games on {r['hero']}"
    games = f"{s['games']} game" + ("" if s["games"] == 1 else "s")
    return f"{games} · {s['win_rate']:.0%} WR · {s['kda']:.1f} KDA · {s['damage_per_min']:,.0f} dmg/min"


def most_played_text(r: Dict[str, Any]) -> str:
    if not r["top_heroes"]:
        return ""
    return "Most played: " + " · ".join(f"{h['hero']} {h['matches']}" for h in r["top_heroes"])


def identity_text(r: Dict[str, Any]) -> str:
    """How the account was identified; empty when there was nothing to decide."""
    if r["status"] != "found" or r["note"] == "unique name":
        return ""
    return f"Identified: {r['note']}"


def badge_labels(r: Dict[str, Any]) -> List[Tuple[str, str]]:
    """The player's badges, plus how their account was identified when it wasn't a unique name."""
    labels = list(r["badges"])
    if r["status"] == "found":
        if not r["confident"]:
            labels.append(("ID UNSURE", "warn"))
        elif r["note"].startswith("friends with"):
            labels.append(("ID VIA FRIENDS", "info"))
    return labels


def build_report(results: List[Dict[str, Any]], parties: List[List[int]]) -> List[Line]:
    """Terminal version of the report, as (text, style) lines."""
    if not results:
        return [("No players found in the screenshot.", "note")]

    lines = []
    for team, title in TEAM_TITLES.items():
        lines.append((title, "team"))
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
            for extra in (most_played_text(r), identity_text(r)):
                if extra:
                    lines.append((f"    {extra}", "note"))
            if r["profile_url"]:
                lines.append((f"    {r['profile_url']}", "link"))
        lines.append(("", "blank"))
    return lines
