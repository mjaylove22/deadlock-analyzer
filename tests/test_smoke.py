"""Smoke test: open every page of the real app in a hidden window, with live API data, and fail if any page
raises while loading or drawing. Runs in CI (CI is set there) or with SMOKE=1; it takes a minute or two.

The app runs in a child process whose data folder (settings, cache, logs, notes) is a new temp folder, so
your own files are never touched, and the global hotkey isn't installed. The account it pretends to be is
picked at run time from a public live match, so no real player ID is written here."""

import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A page failing because the API or Steam didn't answer isn't a bug in the page
NETWORK_ERRORS = re.compile(r"URLError|HTTPError|TimeoutError|timed out|ConnectionError|RemoteDisconnected|"
                            r"Connection(Reset|Aborted)Error|ProtocolError|ReadTimeout")


@unittest.skipUnless(os.environ.get("CI") or os.environ.get("SMOKE"), "slow, uses the network: set SMOKE=1")
class SmokeTest(unittest.TestCase):
    def test_every_page_loads(self):
        data = tempfile.mkdtemp(prefix="smoke_")
        run = subprocess.run([sys.executable, os.path.abspath(__file__), "--run", data], cwd=ROOT,
                             capture_output=True, text=True, timeout=900)
        print(run.stdout[-3000:])
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])
        self.assertNotIn("Traceback", run.stderr, run.stderr[-3000:])
        with open(os.path.join(data, "logs", "app.log"), encoding="utf-8") as f:
            entries = re.split(r"\n(?=\d{4}-\d\d-\d\d )", f.read())  # one entry per log record, traceback included
        errors = [e for e in entries if " - ERROR - " in e]
        bugs = [e for e in errors if not NETWORK_ERRORS.search(e)]
        for e in errors:
            if e not in bugs:
                print("network trouble, not counted:", e.splitlines()[0])
        self.assertEqual(bugs, [], "\n\n".join(bugs))


def drive(data_dir):
    """The child process: point every file the app writes at data_dir, then visit each page."""
    import json
    sys.path.insert(0, ROOT)
    import paths
    paths.DATA_DIR = data_dir  # before anything else imports paths.data()
    import keyboard
    keyboard.add_hotkey = lambda *a, **k: None  # no global hook in a test

    import deadlock_api
    active = deadlock_api.get_json("/v1/matches/active")
    me = next(p["account_id"] for m in active for p in m.get("players") or [] if p.get("account_id"))
    history = deadlock_api.get_match_history(me)
    with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"auto_detect": False, "me": {"name": "Grey Mirage", "account_id": me},
                   "preferences": {"sound": False}}, f)

    import customtkinter as ctk
    import app as app_module
    from ui import pages
    app_module.setup_logger()
    root = ctk.CTk()
    root.withdraw()
    a = app_module.AnalyzerApp(root)
    pending = [0]
    run_task = a.run_task

    def counted(work, on_done, on_error=None):  # so settle() knows when a page has finished loading
        pending[0] += 1

        def work_then_count():
            try:
                return work()
            finally:
                pending[0] -= 1
        run_task(work_then_count, on_done, on_error)
    a.run_task = counted

    def settle(name, limit=120):
        start, quiet = time.time(), 0
        while quiet < 10:  # 10 idle checks in a row: tasks done and their results drawn
            root.update()
            busy = pending[0] or not a.events.empty()
            quiet = 0 if busy else quiet + 1
            if time.time() - start > limit:
                raise SystemExit(f"{name}: still loading after {limit} s")
            time.sleep(0.05)
        print(f"{name}: {time.time() - start:.1f} s", flush=True)

    settle("start")
    steps = [
        ("home", lambda: a.navigate(pages.HomePage)),
        ("lobby (empty)", lambda: a.open_tab("lobby")),
        ("heroes", lambda: a.open_tab("heroes")),
        ("items", lambda: a.open_tab("items")),
        ("hero page", lambda: a.open_hero("Haze")),
        ("hero guide", lambda: a.open_hero("Haze", view="Guide")),
        ("patches", lambda: a.open_tab("patches")),
        ("settings", lambda: a.open_tab("settings")),
        ("settings: Simple", lambda: a.page.apply_preset("Simple")),
        ("settings: Full", lambda: a.page.apply_preset("Full")),
        ("my stats", lambda: a.open_tab("mystats")),
        ("my stats: wins", lambda: a.page.set_result("Wins")),
        ("my stats: one hero", lambda: a.page.set_hero(a.page.history[0]["hero"]) if a.page.history else None),
        ("coach", lambda: a.open_tab("coach")),
        ("coach: deaths", lambda: a.navigate(pages.CoachPage, view="Deaths")),
    ]
    if history:
        newest = max(history, key=lambda m: m["start_time"])["match_id"]
        steps += [("match review", lambda: a.open_match(newest)),
                  ("match performance", lambda: a.open_match(newest, view="Performance"))]
    for name, step in steps:
        step()
        settle(name)
    a.close()


if __name__ == "__main__" and sys.argv[1:2] == ["--run"]:
    drive(sys.argv[2])
