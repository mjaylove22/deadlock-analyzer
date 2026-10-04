"""Work out which Steam account each scoreboard name belongs to, and who is partied up.

Steam display names aren't unique, so each name can have several candidate accounts.
Evidence is used in order of strength:
  1. A unique name settles a player outright.
  2. Friend links: a candidate who is Steam friends with an already-settled player in the
     same lobby is almost certainly the right account. Settling one player can unlock the next.
  3. Hero history: most matches on the hero being played right now (weakest; a player on an
     unfamiliar hero can lose to a same-named stranger).

Pure logic with no network calls, so it can be tested with plain data. Each candidate is a dict:
    {"account_id": int, "friends": set of account ids, "current_hero_matches": int, ...}
"""

from typing import Dict, List, Tuple

Candidate = Dict  # see module docstring


def are_friends(a: Candidate, b: Candidate) -> bool:
    """Friend lists can be private, so a link counts if either side lists the other."""
    return a["account_id"] in b["friends"] or b["account_id"] in a["friends"]


def pick_by_hero_history(candidates: List[Candidate]) -> Tuple[Candidate, str]:
    """Fallback: the candidate with the most matches on the current hero.

    Ties keep the API's own ranking (name similarity + recent activity), because candidates
    arrive in that order and max() keeps the first of equal items.
    """
    best = max(candidates, key=lambda c: c["current_hero_matches"])
    runner_up = max(c["current_hero_matches"] for c in candidates if c is not best)
    if best["current_hero_matches"] > 0:
        # Show the runner-up so a close call (e.g. 8 vs 3) is visible, not just the winner
        return best, (f"{len(candidates)} accounts share this name; picked the one with "
                      f"{best['current_hero_matches']} matches on this hero (next best: {runner_up})")
    return best, f"{len(candidates)} accounts share this name; none have played this hero, so this pick is a guess"


def resolve_lobby(candidates_by_player: Dict[int, List[Candidate]],
                  names: Dict[int, str]) -> Dict[int, Tuple[Candidate, str]]:
    """Choose one account per player. Returns {player index: (candidate, reason)}.

    Players are keyed by their index in the scoreboard, because two players can share a name.
    Players with no candidates are left out of the result.
    """
    resolved = {}
    for i, candidates in candidates_by_player.items():
        if len(candidates) == 1:
            resolved[i] = (candidates[0], "unique name")

    # Friend links to settled players. Repeat, because each newly settled player can be the
    # link that settles another (e.g. A is unique, B is A's friend, C is friends with B).
    changed = True
    while changed:
        changed = False
        for i, candidates in candidates_by_player.items():
            if i in resolved or not candidates:
                continue
            # links[k] = names of settled players that candidate k is friends with
            links = [
                [names[j] for j, (settled, _) in resolved.items() if are_friends(c, settled)]
                for c in candidates
            ]
            most = max(len(found) for found in links)
            winners = [k for k, found in enumerate(links) if len(found) == most]
            # Only decide when exactly one candidate has the most links; a tie isn't evidence
            if most > 0 and len(winners) == 1:
                k = winners[0]
                resolved[i] = (candidates[k], f"friends with {', '.join(links[k])} in this lobby")
                changed = True

    for i, candidates in candidates_by_player.items():
        if i not in resolved and candidates:
            resolved[i] = pick_by_hero_history(candidates)
    return resolved


def find_parties(resolved: Dict[int, Tuple[Candidate, str]], teams: Dict[int, str]) -> List[List[int]]:
    """Groups of 2+ players on the same team who are connected by friend links.

    Friends on the same team almost always queued together. Returns lists of player indexes.
    """
    players = sorted(resolved)
    group_of = {i: {i} for i in players}  # each player starts in their own group
    for a in players:
        for b in players:
            if a < b and teams[a] == teams[b] and are_friends(resolved[a][0], resolved[b][0]):
                merged = group_of[a] | group_of[b]
                for member in merged:
                    group_of[member] = merged

    parties = {frozenset(group) for group in group_of.values() if len(group) > 1}
    return sorted((sorted(party) for party in parties), key=lambda party: party[0])
