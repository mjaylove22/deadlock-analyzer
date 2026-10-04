"""Turn lobby lookup results into display lines.

Shared by the terminal report (player_lookup.py) and the app window (app.py), so both always
show the same thing. Each line is (text, style); the window colours lines by style and makes
"link" lines clickable, the terminal just prints the text.
"""

from typing import Any, Dict, List, Tuple

Line = Tuple[str, str]  # (text, style): style is one of team, party, player, note, link, hero, blank

TEAM_TITLES = {"friendly": "YOUR TEAM", "enemy": "ENEMY TEAM"}


def build_report(results: List[Dict[str, Any]], parties: List[List[int]]) -> List[Line]:
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
            lines.append((f"{r['player']}  ({r['hero']})  [{r['status']}]", "player"))
            if r["note"]:
                lines.append((f"    {r['note']}", "note"))
            if r["profile_url"]:
                lines.append((f"    {r['profile_url']}", "link"))
            for h in r["top_heroes"]:
                lines.append((f"    {h['hero']:<12} {h['matches']:>4} matches  {h['win_rate']:.0%} wins", "hero"))
        lines.append(("", "blank"))
    return lines
