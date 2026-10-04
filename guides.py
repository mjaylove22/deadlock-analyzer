"""A short, readable guide for each hero, built only from data: the game's own hero and ability
descriptions, how the hero compares with the others, and what players actually buy.

Nothing here is written by hand, so it stays right when heroes change.
"""

import statistics
from typing import Any, Dict, List, Optional

import deadlock_api

COMPLEXITY = {1: "easy", 2: "medium", 3: "hard", 4: "very hard"}
# Words in ability descriptions, and what they say about the kit, in the order they're listed
# (Longer phrases first: each match is removed, so "bonus weapon damage" isn't also counted as damage.)
KIT_WORDS = [
    ("bonus weapon damage", "weapon buffs"), ("bonus fire rate", "weapon buffs"),
    ("spirit damage", "spirit damage"), ("weapon damage", "weapon damage"), ("bullet", "weapon damage"),
    ("heal", "healing"), ("barrier", "barriers"), ("stun", "stuns"), ("immobiliz", "immobilize"),
    ("slow", "slows"), ("silence", "silence"), ("disarm", "disarm"), ("sleep", "sleep"),
    ("knock", "knockback"), ("pull", "pulls"), ("invisib", "invisibility"), ("stealth", "invisibility"),
    ("dash", "mobility"), ("leap", "mobility"), ("teleport", "mobility"),
]
WEAPON_NOUNS = {"shotgun", "bow", "crossbow", "pistol", "beam weapon", "heavy artillery"}  # no "gun" after these
RELATIVE = 0.08  # 8% above or below the median hero counts as high or low


def kit(abilities: List[Dict[str, Any]]) -> List[str]:
    """What the abilities do, read from their descriptions: ["spirit damage", "slows", "healing"...]."""
    text = " ".join(a["text"].lower() for a in abilities)
    found: List[str] = []
    for word, meaning in KIT_WORDS:
        if word in text:
            text = text.replace(word, " ")
            if meaning not in found:
                found.append(meaning)
    return found


def relative(value: Optional[float], values: List[float]) -> Optional[str]:
    """"high", "low" or "average" compared with the median hero (None without data)."""
    if not value or not values:
        return None
    median = statistics.median(values)
    return "high" if value >= median * (1 + RELATIVE) else "low" if value <= median * (1 - RELATIVE) else "average"


def build_split(items: Optional[List[Dict[str, Any]]]) -> Dict[str, float]:
    """Share of weapon / vitality / spirit among the most-bought items, weighted by how often they're bought."""
    totals: Dict[str, float] = {}
    for item in items or []:
        if item.get("slot"):
            totals[item["slot"]] = totals.get(item["slot"], 0) + item["matches"]
    whole = sum(totals.values())
    return {slot: count / whole for slot, count in totals.items()} if whole else {}


def hero_guide(hero: str, bought: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """{"summary", "facts": [(label, value)], "kit", "build": {slot: share}, "abilities"} for one hero,
    or None if the hero isn't in the game's data. bought: the hero's most-bought items, if loaded."""
    guides = deadlock_api.fetch_hero_guides()
    guide = guides.get(hero)
    if not guide:
        return None
    health = relative(guide["health"], [g["health"] for g in guides.values() if g["health"]])
    speed = relative(guide["speed"], [g["speed"] for g in guides.values() if g["speed"]])
    complexity = COMPLEXITY.get(guide["complexity"], "")
    kind = (guide["type"] or "hero").title()
    article = "An" if (complexity or kind)[0].lower() in "aeiou" else "A"
    weapon = (guide["gun"] or "").lower()
    gun = f" with a {weapon}" + ("" if weapon in WEAPON_NOUNS else " gun") if weapon else ""
    sentences = [f"{article} {complexity} {kind}{gun}".replace("  ", " ") + "."]
    body = [f"{word} health" for word in [health] if word and word != "average"]
    body += [f"{word} speed" for word in [speed] if word and word != "average"]
    if body:
        sentences.append(f"{' and '.join(body).capitalize()}.")
    abilities_do = kit(guide["abilities"])
    if abilities_do:
        sentences.append(f"Abilities bring {', '.join(abilities_do[:-1]) + ' and ' + abilities_do[-1] if len(abilities_do) > 1 else abilities_do[0]}.")
    split = build_split(bought)
    if split:
        main, share = max(split.items(), key=lambda kv: kv[1])
        if share >= 0.45:
            sentences.append(f"Players mostly build {hero} with {main} items ({share:.0%} of the most-bought).")
        else:
            sentences.append(f"Players build {hero} with a mix of item types.")
    facts = [("Type", kind), ("Complexity", f"{complexity.capitalize()} ({guide['complexity']} of 4)" if complexity else "?"),
             ("Gun", guide["gun"] or "?"), ("Health", f"{guide['health']:,.0f}" + (f" ({health})" if health else "")),
             ("Speed", f"{guide['speed']:.1f} m/s" + (f" ({speed})" if speed else ""))]
    if guide["tags"]:
        facts.append(("Tags", ", ".join(guide["tags"])))
    return {"summary": " ".join(sentences), "facts": facts, "kit": abilities_do, "build": split,
            "abilities": guide["abilities"]}
