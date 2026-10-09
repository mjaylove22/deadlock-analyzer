"""A short report for when something goes wrong on someone else's PC: the app's version and setup, the
screen, Tesseract, whether the servers answer, and which kinds of problems the log recorded lately.

Settings copies it to the clipboard for the user to paste into a message. Unlike app.log it holds no
player names or IDs: problems are summed up by kind (quoted text, numbers and anything after a colon are
dropped), and the user's Windows folder is written as %USERPROFILE%.
"""

import os
import platform
import re
import time
import urllib.request
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

import paths
from version import LATEST_RELEASE_API, __version__

LOG_DAYS = 7
CHECKS = [  # (name, URL): small answers from each server the app depends on
    ("Stats API", "https://api.deadlock-api.com/v1/info/health"),
    ("Steam (patch notes)", "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=1422450&count=1&maxlength=1"),
    ("GitHub (updates)", LATEST_RELEASE_API),
]


def problem_kinds(log_text: str, now: float, days: int = LOG_DAYS) -> List[str]:
    """Warnings and errors of the last days, counted by kind, most frequent first: "3× app WARNING:
    Keyboard focus moved while reading the screenshot", with an error's exception type in brackets."""
    kinds: Counter = Counter()
    for entry in re.split(r"\n(?=\d{4}-\d\d-\d\d )", log_text):
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ - (\S+) - (WARNING|ERROR) - (.*)", entry)
        if not m or now - time.mktime(time.strptime(m[1], "%Y-%m-%d %H:%M:%S")) > days * 86400:
            continue
        what = re.split(r": |\(|\s[\"']", m[4], maxsplit=1)[0]  # the kind of problem, not its details (names, paths)
        what = re.sub(r"\d+", "N", what).strip(" .")
        exception = re.findall(r"^(\w+(?:\.\w+)*(?:Error|Exception|Unavailable))\b", entry, re.M)
        kinds[f"{m[2]} {m[3]}: {what}" + (f" ({exception[-1].split('.')[-1]})" if exception else "")] += 1
    return [f"{n}× {kind}" for kind, n in kinds.most_common()]


def server_checks(get: Callable[[str], Tuple[int, int]] = None) -> List[str]:
    """Whether each server answers, and how fast."""
    def fetch(url):
        start = time.time()
        request = urllib.request.Request(url, headers={"User-Agent": "deadlock-analyzer (learning project)"})
        with urllib.request.urlopen(request, timeout=8) as response:
            response.read()
            return response.status, round((time.time() - start) * 1000)
    lines = []
    for name, url in CHECKS:
        try:
            status, ms = (get or fetch)(url)
            lines.append(f"{name}: OK ({status}, {ms} ms)")
        except Exception as e:  # a diagnostic report says what went wrong rather than stopping
            lines.append(f"{name}: FAILED ({type(e).__name__}: {str(e)[:80]})")
    return lines


def private(text: str) -> str:
    home = os.path.expanduser("~")
    return text.replace(home, "%USERPROFILE%") if home and home != "~" else text


def folder_size_mb(folder: str) -> float:
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(folder) for f in files) / 1e6


def report(setup: Dict[str, str], log_path: Optional[str] = None, now: Optional[float] = None) -> str:
    """The whole report (worker thread: it asks each server once). setup: the app's own facts, gathered on
    the main thread (screen, scaling, theme, switches, Tesseract, the game window)."""
    now = time.time() if now is None else now
    lines = [f"Deadlock Analyzer {__version__} diagnostic report ({time.strftime('%Y-%m-%d %H:%M', time.localtime(now))})",
             "No player names or IDs are included.", "",
             f"App: {'installed' if paths.INSTALLED else 'from source'} · Windows {platform.release()} ({platform.version()})"]
    lines += [f"{key}: {value}" for key, value in setup.items()]
    lines.append(f"Data folder: {paths.DATA_DIR} · cache {folder_size_mb(paths.data('cache')):.1f} MB")
    lines += ["", "Servers:"] + ["  " + line for line in server_checks()]
    log_text = ""
    if log_path and os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log_text = f.read()
    kinds = problem_kinds(log_text, now)
    lines += ["", f"Problems in the log, last {LOG_DAYS} days:" if kinds else f"No warnings or errors in the log in the last {LOG_DAYS} days."]
    lines += ["  " + kind for kind in kinds[:15]]
    return private("\n".join(lines))
