"""Block a commit that would publish a real player's name or ID.

Run by git as the pre-commit hook (python tools/privacy_scan.py --install sets it up), so a commit
with a hit fails however it was started; printing a warning wasn't enough once (a friend's name
went into a public commit because the scan's result was ignored).

What counts as private is collected on this PC each time, never from the repo:
  - your account in settings.json (name, account ID, Steam64 ID)
  - the people you play with (cache/api/mates_*.json): names and account IDs
  - players in saved match reviews (cache/matches): account IDs and the match ID
  - hand-checked screenshot answers (screenshots/*.expected.json) and names in logs/app.log
  - anything listed in .git/info/private-terms.txt (one per line), which git never commits
Only added lines of the staged diff are checked. Names are compared after Unicode NFKC folding,
ignoring case and spaces, so "ｍｏｏｎｄｏｇ" and "m o o n d o g" both match "moondog".
"""

import glob
import json
import os
import re
import subprocess
import sys
import unicodedata
from typing import Dict, Iterable, List, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEAM64_BASE = 76561197960265728
MIN_NAME_LENGTH = 4      # shorter names ("Zed") would match ordinary words
MIN_ID_DIGITS = 6
INVISIBLE = dict.fromkeys(map(ord, chr(0x200b) + chr(0x200c) + chr(0x200d) + chr(0xfeff)))
HOOK = "#!/bin/sh\n# Installed by tools/privacy_scan.py --install\nexec python tools/privacy_scan.py\n"


SQUASHED_MIN_LENGTH = 7  # names this long also match with their spaces removed ("m o o n d o g")


def plain(text: str) -> str:
    """Lower case, full-width and styled letters made plain, invisible characters removed."""
    return unicodedata.normalize("NFKC", text).translate(INVISIBLE).casefold()


def fold(text: str) -> str:
    """plain() without any spaces."""
    return re.sub(r"\s+", "", plain(text))


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def collect_terms(root: str = ROOT) -> Tuple[Set[str], Set[str]]:
    """(names, ids) that must never be committed."""
    names: Set[str] = set()
    ids: Set[str] = set()
    me = (_read_json(os.path.join(root, "settings.json")) or {}).get("me") or {}
    if me.get("name"):
        names.add(me["name"])
    if me.get("account_id"):
        ids.update({str(me["account_id"]), str(int(me["account_id"]) + STEAM64_BASE)})
    for path in glob.glob(os.path.join(root, "cache", "api", "mates_*.json")):
        for mate in _read_json(path) or []:
            names.add(mate.get("name") or "")
            ids.add(str(mate.get("account_id") or ""))
    for path in glob.glob(os.path.join(root, "cache", "matches", "*.json")):
        summary = _read_json(path) or {}
        ids.add(str(summary.get("match_id") or ""))
        ids.update(str(p.get("account_id") or "") for p in summary.get("players", []))
    for path in glob.glob(os.path.join(root, "screenshots", "*.expected.json")):
        names.update(row.get("player") or "" for row in _read_json(path) or [])
    try:
        with open(os.path.join(root, "logs", "app.log"), encoding="utf-8", errors="replace") as f:
            for line in f:
                for found in re.findall(r"not found: (.*)", line):
                    for part in found.split("; "):
                        names.add(re.sub(r" \(closest name: .*\)$", "", part))
    except OSError:
        pass
    try:
        with open(os.path.join(root, ".git", "info", "private-terms.txt"), encoding="utf-8") as f:
            for line in f:
                term = line.strip()
                if term and not term.startswith("#"):
                    (ids if term.isdigit() else names).add(term)
    except OSError:
        pass
    heroes = {fold(h.get("name", "")) for h in _read_json(os.path.join(root, "cache", "api", "heroes.json")) or []}
    names = {n for n in names if len(fold(n)) >= MIN_NAME_LENGTH and fold(n) not in heroes}  # bots use hero names
    ids = {i for i in ids if i.isdigit() and len(i) >= MIN_ID_DIGITS}
    return names, ids


def added_lines(diff: str) -> Iterable[Tuple[str, str]]:
    """(file, line) for every line the staged diff adds."""
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            yield current, line[1:]


def find_leaks(lines: Iterable[Tuple[str, str]], names: Set[str], ids: Set[str]) -> List[Tuple[str, str, str]]:
    """(file, term, line) for each added line containing a private name or ID. A name must stand as a
    whole word ("Star" isn't found in "Start"); long names are also found with spaces removed."""
    patterns: Dict[str, re.Pattern] = {n: re.compile(r"(?<!\w)" + re.escape(plain(n).strip()) + r"(?!\w)") for n in names}
    squashed = {n: fold(n) for n in names if len(fold(n)) >= SQUASHED_MIN_LENGTH}
    hits = []
    for path, line in lines:
        text, folded = plain(line), fold(line)
        digits = set(re.findall(r"\d+", line))
        found = [name for name, pattern in patterns.items()
                 if pattern.search(text) or (name in squashed and squashed[name] in folded)] + sorted(ids & digits)
        if found:
            hits.append((path, found[0], line.strip()))  # one report per line is enough to fix it
    return hits


def install(root: str = ROOT) -> None:
    path = os.path.join(root, ".git", "hooks", "pre-commit")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK)
    print(f"Installed {path}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # names can be full-width or emoji; the Windows console can't show all
        stream.reconfigure(encoding="utf-8", errors="replace")
    if "--install" in sys.argv:
        install()
        return 0
    diff = subprocess.run(["git", "diff", "--cached", "-U0", "--no-color"], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=True).stdout
    names, ids = collect_terms()
    hits = find_leaks(added_lines(diff), names, ids)
    for path, term, line in hits:
        print(f"PRIVATE: {term!r} in {path}: {line[:120]}", file=sys.stderr)
    if hits:
        print(f"Commit blocked: {len(hits)} line(s) contain a real player's name or ID. "
              "Use made-up names and IDs instead.", file=sys.stderr)
        return 1
    print(f"Privacy scan passed ({len(names)} names, {len(ids)} IDs checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
