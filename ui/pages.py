"""The app's pages: Home, Lobby, Heroes, Search, Player (also used for My Stats) and account setup.

Each page builds its widgets into self.frame. Slow data is loaded with app.run_task(work, on_done):
work runs on a worker thread, on_done runs on the main thread, and only if the user is still on
the same page (otherwise the result is dropped).
"""

import os
import time
import tkinter as tk
import webbrowser
from typing import Any, Dict

from player_lookup import attach_ranks, search_player
from profiles import API_GAME_MODES, hero_tier_list, player_profile, when
from report import TEAM_TITLES, team_summary
from settings import get_me, save_settings
from ui.theme import BADGE_COLORS, COLORS, PARTY_COLORS, button, label, pill
from ui.widgets import data_table, matchup_strip, player_card, toggle

MODES = list(API_GAME_MODES)  # ["Normal", "Street Brawl"]


def pct(value) -> str:
    return f"{value:.0%}" if value is not None else ""


def panel(parent, title: str) -> tk.Frame:
    """A titled card used on the home page."""
    frame = tk.Frame(parent, bg=COLORS["card"], padx=16, pady=12)
    label(frame, title.upper(), size=9, color="dim", bold=True, bg="card").pack(anchor="w", pady=(0, 6))
    return frame


class Page:
    nav = None  # which top-bar tab to highlight

    def __init__(self, app, parent, **options):
        self.app = app
        self.frame = tk.Frame(parent, bg=COLORS["bg"])
        self.build(**options)

    def build(self, **options):
        raise NotImplementedError

    def message(self, text: str, parent=None):
        label(parent or self.frame, text, size=12, color="dim", justify="center").pack(expand=True, pady=40)

    def heading(self, title: str, subtitle: str = "") -> tk.Frame:
        row = tk.Frame(self.frame, bg=COLORS["bg"])
        row.pack(fill="x", pady=(0, 10))
        label(row, title, size=16, bold=True).pack(side="left")
        if subtitle:
            label(row, subtitle, color="dim").pack(side="left", padx=12, pady=(5, 0))
        return row


class HomePage(Page):
    nav = "home"

    def build(self):
        label(self.frame, "Deadlock Analyzer", size=22, bold=True).pack(anchor="w")
        watching = ("Auto-detect is on: open the Esc menu on the PLAYERS tab in game and your lobby appears here."
                    if self.app.watching else "Auto-detect is off: press Ctrl+Shift+D in game to capture the lobby.")
        label(self.frame, watching, size=11, color="dim").pack(anchor="w", pady=(2, 16))

        grid = tk.Frame(self.frame, bg=COLORS["bg"])
        grid.pack(fill="x")
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="home")
        self.account_panel(grid).grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.lobby_panel(grid).grid(row=0, column=1, sticky="nsew", padx=8)
        self.explore_panel(grid).grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        lower = tk.Frame(self.frame, bg=COLORS["bg"])
        lower.pack(fill="both", expand=True, pady=(16, 0))
        lower.columnconfigure(0, weight=1, uniform="lower")
        lower.columnconfigure(1, weight=1, uniform="lower")
        self.heroes_box = panel(lower, "Strongest heroes right now (Normal)")
        self.heroes_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.recent_box = panel(lower, "Your recent matches")
        self.recent_box.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.load_lower()

    def load_lower(self):
        """Fill the two lower panels; both load in the background (heroes are cached for the session)."""
        me = get_me()

        def work():
            names = self.app.hero_names_by_id()
            tiers = self.app.cache.get(("tiers", "Normal")) or hero_tier_list(names, "normal")
            recent = player_profile(me["account_id"], names)["recent"][:8] if me else None
            return tiers, recent

        def done(result):
            tiers, recent = result
            self.app.cache[("tiers", "Normal")] = tiers
            for n, r in enumerate(tiers[:8], start=1):
                row = tk.Frame(self.heroes_box, bg=COLORS["card"])
                row.pack(fill="x", pady=1)
                label(row, f"{n}.  {r['hero']}", bg="card", width=22, anchor="w").pack(side="left")
                label(row, f"{r['win_rate']:.1%} WR", bg="card", color="win", width=10, anchor="w").pack(side="left")
                label(row, f"{r['pick_rate']:.0%} pick rate", bg="card", color="dim").pack(side="left")
            button(self.heroes_box, "Full tier list", self.app.open_heroes).pack(anchor="w", pady=(8, 0))
            if recent is None:
                label(self.recent_box, "Set your account to see your recent matches here.", color="dim", bg="card").pack(anchor="w")
                return
            for m in recent:
                row = tk.Frame(self.recent_box, bg=COLORS["card"])
                row.pack(fill="x", pady=1)
                result = pill(row, "WIN" if m["won"] else "LOSS", COLORS["win"] if m["won"] else COLORS["loss"])
                result.config(width=5)  # same width for WIN and LOSS, so the columns line up
                result.pack(side="left")
                label(row, f"  {m['hero']}", bg="card", width=16, anchor="w").pack(side="left")
                label(row, f"{m['kills']}/{m['deaths']}/{m['assists']}", bg="card", width=10, anchor="w").pack(side="left")
                label(row, f"{m['mode']} · {when(m['start_time'])}", bg="card", color="dim").pack(side="left")
            button(self.recent_box, "All my stats", self.app.open_my_stats).pack(anchor="w", pady=(8, 0))

        label(self.heroes_box, "Loading...", color="dim", bg="card").pack(anchor="w")
        label(self.recent_box, "Loading...", color="dim", bg="card").pack(anchor="w")

        def clear_then(result):
            for box in (self.heroes_box, self.recent_box):
                for widget in box.winfo_children()[1:]:  # keep each panel's title
                    widget.destroy()
            done(result)
        self.app.run_task(work, clear_then)

    def account_panel(self, parent):
        frame = panel(parent, "Your account")
        me = get_me()
        if not me:
            label(frame, "Tell the app which Steam account is yours,\nso it can always find you and show your matchup.",
                  color="dim", bg="card", justify="left").pack(anchor="w")
            button(frame, "Find my account", self.app.focus_search, primary=True).pack(anchor="w", pady=(10, 0))
            return frame
        label(frame, me["name"], size=14, bold=True, bg="card").pack(anchor="w")
        row = tk.Frame(frame, bg=COLORS["card"])
        row.pack(anchor="w", pady=(10, 0))
        button(row, "My stats", self.app.open_my_stats, primary=True).pack(side="left")
        button(row, "Change account", self.app.focus_search).pack(side="left", padx=6)
        return frame

    def lobby_panel(self, parent):
        frame = panel(parent, "Last lobby")
        lobby = self.app.lobby
        if not lobby:
            label(frame, "No lobby captured yet.", color="dim", bg="card").pack(anchor="w")
            button(frame, "Analyze latest screenshot", self.app.analyze_latest).pack(anchor="w", pady=(10, 0))
            return frame
        label(frame, f"{len(lobby['results'])} players · {time.strftime('%I:%M %p', time.localtime(lobby['time'])).lstrip('0')}",
              size=14, bold=True, bg="card").pack(anchor="w")
        for team, title in TEAM_TITLES.items():
            members = [r for r in lobby["results"] if r["team"] == team]
            parties = [p for p in lobby["parties"] if lobby["results"][p[0]]["team"] == team]
            label(frame, f"{title.title()}: {team_summary(members, parties)}", size=9, color="dim", bg="card",
                  justify="left", wraplength=320).pack(anchor="w")
        button(frame, "Open lobby", self.app.open_lobby, primary=True).pack(anchor="w", pady=(10, 0))
        return frame

    def explore_panel(self, parent):
        frame = panel(parent, "Explore")
        label(frame, "Hero win rates and pick rates, or look up\nany player by their Steam name.",
              color="dim", bg="card", justify="left").pack(anchor="w")
        row = tk.Frame(frame, bg=COLORS["card"])
        row.pack(anchor="w", pady=(10, 0))
        button(row, "Hero tier list", self.app.open_heroes, primary=True).pack(side="left")
        button(row, "Search a player", self.app.focus_search).pack(side="left", padx=6)
        return frame


class LobbyPage(Page):
    nav = "lobby"

    def build(self):
        lobby = self.app.lobby
        when_text = (f"captured {time.strftime('%I:%M %p', time.localtime(lobby['time'])).lstrip('0')} · "
                     f"{os.path.basename(lobby['path'])}") if lobby else ""
        row = self.heading("Lobby", when_text)
        button(row, "Open screenshot...", self.app.open_screenshot).pack(side="right")
        button(row, "Analyze latest", self.app.analyze_latest).pack(side="right", padx=6)
        if not lobby:
            self.message("No lobby yet.\n\nOpen the Esc menu on the PLAYERS tab in game, or analyze a saved screenshot.")
            return
        if not lobby["results"]:
            self.message("No players found in that screenshot.\n\nMake sure the Esc menu is open on the PLAYERS tab.")
            return

        if lobby.get("matchup"):
            matchup_strip(self.frame, lobby["matchup"]).pack(side="bottom", fill="x", pady=(8, 0))
        results, parties = lobby["results"], lobby["parties"]
        party_of = {i: (PARTY_COLORS[n % len(PARTY_COLORS)], f"PARTY {chr(65 + n)}")
                    for n, party in enumerate(parties) for i in party}
        columns = tk.Frame(self.frame, bg=COLORS["bg"])
        columns.pack(fill="both", expand=True)
        for c, (team, title) in enumerate(TEAM_TITLES.items()):
            columns.columnconfigure(c, weight=1, uniform="team")
            frame = tk.Frame(columns, bg=COLORS["bg"])
            frame.grid(row=0, column=c, sticky="nsew", padx=(0, 8) if c == 0 else (8, 0))
            members = [i for i, r in enumerate(results) if r["team"] == team]
            team_parties = [p for p in parties if results[p[0]]["team"] == team]
            head = tk.Frame(frame, bg=COLORS["bg"])
            head.pack(fill="x", pady=(0, 6))
            label(head, title, size=13, color=team, bold=True).pack(side="left")
            label(head, team_summary([results[i] for i in members], team_parties), color="dim").pack(side="left", padx=10)
            for i in members:
                r = results[i]
                player_card(frame, r, COLORS[team], self.app.avatars, party=party_of.get(i),
                            on_open=lambda r=r: self.app.open_player(r["account_id"]), on_search=self.app.search)


class SearchPage(Page):
    nav = None

    def build(self, query: str):
        self.heading(f"Search: {query}")
        self.body = tk.Frame(self.frame, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True)
        self.message(f"Searching for {query}...", self.body)

        def work():
            results = search_player(query, self.app.hero_names_by_id(), progress=self.app.progress)
            return results, self.app.avatars.download(r.get("avatar_url") for r in results)
        self.app.run_task(work, lambda result: self.show(query, *result))

    def show(self, query, results, images):
        self.app.avatars.store(images)
        for widget in self.body.winfo_children():
            widget.destroy()
        if not results:
            self.message(f"No Steam profiles found for {query!r}.", self.body)
            return
        me = get_me()
        for r in results:
            r["is_me"] = bool(me and r["account_id"] == me["account_id"])
        exact = results[0]["note"] == "exact name"
        count = f"{len(results)} account" + ("" if len(results) == 1 else "s")
        label(self.body, f"{count} with this exact name. Click one to see their stats." if exact
              else "No exact match. Closest names:", color="dim").pack(anchor="w", pady=(0, 6))
        columns = tk.Frame(self.body, bg=COLORS["bg"])
        columns.pack(fill="both", expand=True)
        frames = []
        for c in range(2):
            columns.columnconfigure(c, weight=1, uniform="search")
            frame = tk.Frame(columns, bg=COLORS["bg"])
            frame.grid(row=0, column=c, sticky="nsew", padx=(0, 8) if c == 0 else (8, 0))
            frames.append(frame)
        for n, r in enumerate(results):
            player_card(frames[n % 2], r, COLORS["accent"], self.app.avatars,
                        on_open=lambda r=r: self.app.open_player(r["account_id"]))
        self.app.set_status(f"{count} found")


class HeroesPage(Page):
    nav = "heroes"

    def build(self, mode: str = "Normal"):
        row = self.heading("Heroes", "win rate and pick rate across all recorded matches")
        toggle(row, MODES, mode, lambda m: self.app.open_heroes(m, push=False)).pack(side="right")
        self.body = tk.Frame(self.frame, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True)
        cached = self.app.cache.get(("tiers", mode))
        if cached:
            self.show(cached)
            return
        self.message("Loading hero stats...", self.body)

        def work():
            return hero_tier_list(self.app.hero_names_by_id(), API_GAME_MODES[mode])

        def done(rows):
            self.app.cache[("tiers", mode)] = rows
            self.show(rows)
        self.app.run_task(work, done)

    def show(self, rows):
        for widget in self.body.winfo_children():
            widget.destroy()
        for n, r in enumerate(rows, start=1):
            r["position"] = n  # position by win rate, kept when re-sorting by another column
        data_table(self.body, [
            ("position", "#", 40, str, "center"),
            ("hero", "Hero", 160, str, "w"),
            ("win_rate", "Win rate", 90, lambda v: f"{v:.1%}", "center"),
            ("pick_rate", "Pick rate", 90, pct, "center"),
            ("games", "Games", 100, lambda v: f"{v:,}", "center"),
            ("kda", "KDA", 70, lambda v: f"{v:.2f}", "center"),
        ], list(rows), height=24)
        label(self.body, "Click a column heading to sort. Heroes with under 500 games are left out.",
              size=9, color="dim").pack(anchor="w", pady=(6, 0))
        self.app.set_status(f"{len(rows)} heroes")


class PlayerPage(Page):
    nav = None

    def build(self, account_id: int, mode: str = "Normal", nav: str = None):
        self.nav = nav
        self.account_id, self.mode = account_id, mode
        self.message("Loading player...")

        def work():
            profile = player_profile(account_id, self.app.hero_names_by_id(), API_GAME_MODES[mode])
            attach_ranks([profile])
            return profile, self.app.avatars.download([profile["avatar_url"]])
        self.app.run_task(work, lambda result: self.show(*result))

    def show(self, p: Dict[str, Any], images):
        self.app.avatars.store(images)
        for widget in self.frame.winfo_children():
            widget.destroy()
        me = get_me()
        is_me = bool(me and me["account_id"] == p["account_id"])

        # Header: who they are
        header = tk.Frame(self.frame, bg=COLORS["card"], padx=16, pady=14)
        header.pack(fill="x")
        tk.Label(header, image=self.app.avatars.get(p["avatar_url"], 84), bg=COLORS["card"]).pack(side="left")
        info = tk.Frame(header, bg=COLORS["card"], padx=16)
        info.pack(side="left", fill="both", expand=True)
        name_row = tk.Frame(info, bg=COLORS["card"])
        name_row.pack(anchor="w")
        label(name_row, p["name"], size=20, bold=True, bg="card").pack(side="left")
        if is_me:
            pill(name_row, "YOU", BADGE_COLORS["you"], size=9).pack(side="left", padx=(10, 0), pady=(6, 0))
        if p.get("rank"):
            pill(name_row, p["rank"]["name"], p["rank"]["color"], size=10).pack(side="left", padx=(8, 0), pady=(6, 0))
        recent = f" · {p['recent_30d']} in the last 30 days" if p.get("recent_30d") is not None else ""
        label(info, f"{p['total_matches']:,} recorded matches{recent}", color="dim", bg="card").pack(anchor="w", pady=(2, 6))
        chips = tk.Frame(info, bg=COLORS["card"])
        chips.pack(anchor="w")
        for m in p["modes"]:
            pill(chips, f"{m['mode']}: {m['games']:,} games · {m['win_rate']:.0%} WR", COLORS["button"], size=9).pack(side="left", padx=(0, 6))

        actions = tk.Frame(header, bg=COLORS["card"])
        actions.pack(side="right", anchor="n")
        if p.get("profile_url"):
            button(actions, "Steam profile", lambda: webbrowser.open(p["profile_url"])).pack(anchor="e")
        if is_me:
            label(actions, "This is your account", size=9, color="friendly", bg="card").pack(anchor="e", pady=(8, 0))
        else:
            button(actions, "Set as my account", lambda: self.set_as_me(p)).pack(anchor="e", pady=(6, 0))

        # Mode switch, then the two tables side by side
        row = tk.Frame(self.frame, bg=COLORS["bg"])
        row.pack(fill="x", pady=(14, 6))
        label(row, "Hero stats", size=13, bold=True).pack(side="left")
        toggle(row, MODES, self.mode, self.switch_mode).pack(side="left", padx=12)
        label(row, "Recent matches", size=13, bold=True).pack(side="right")

        tables = tk.Frame(self.frame, bg=COLORS["bg"])
        tables.pack(fill="both", expand=True)
        tables.columnconfigure(0, weight=11, uniform="tables")
        tables.columnconfigure(1, weight=10, uniform="tables")
        left, right = tk.Frame(tables, bg=COLORS["bg"]), tk.Frame(tables, bg=COLORS["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        if p["heroes"]:
            data_table(left, [
                ("hero", "Hero", 130, str, "w"),
                ("games", "Games", 65, str, "center"),
                ("win_rate", "Win rate", 75, pct, "center"),
                ("kda", "KDA", 60, lambda v: f"{v:.1f}", "center"),
                ("damage_per_min", "Dmg/min", 75, lambda v: f"{v:,.0f}", "center"),
                ("last_played", "Last played", 95, when, "center"),
            ], list(p["heroes"]), height=15)
        else:
            self.message(f"No recorded {self.mode} matches.", left)

        if p["recent"]:
            for m in p["recent"]:
                m["kda_text"] = f"{m['kills']}/{m['deaths']}/{m['assists']}"
                m["result"] = "Win" if m["won"] else "Loss"
            data_table(right, [
                ("start_time", "When", 75, when, "center"),
                ("hero", "Hero", 110, str, "w"),
                ("result", "Result", 60, str, "center"),
                ("kda_text", "K/D/A", 75, str, "center"),
                ("minutes", "Min", 45, str, "center"),
                ("mode", "Mode", 95, str, "center"),
            ], list(p["recent"]), height=15, tag=lambda m: "win" if m["won"] else "loss")
        else:
            self.message("No recorded matches.", right)
        self.app.set_status(f"{p['name']} · click a column heading to sort")

    def switch_mode(self, mode: str):
        self.app.open_player(self.account_id, mode=mode, nav=self.nav, push=False)

    def set_as_me(self, p):
        save_settings({"me": {"name": p["name"], "account_id": p["account_id"]}})
        self.app.set_status(f"Saved: you are {p['name']}. You'll be identified exactly in every lobby.")
        self.app.open_player(p["account_id"], mode=self.mode, nav=self.nav, push=False)


class SetupPage(Page):
    nav = "mystats"

    def build(self):
        self.heading("My Stats")
        label(self.frame, "The app doesn't know which Steam account is yours yet.", size=12).pack(anchor="w")
        label(self.frame, "Search your Steam name in the box at the top, open your account, and click "
                          "\"Set as my account\".\nYou'll then be identified exactly in every lobby, "
                          "your matchup will show, and this tab will open your stats.",
              color="dim", justify="left").pack(anchor="w", pady=(6, 12))
        button(self.frame, "Find my account", self.app.focus_search, primary=True).pack(anchor="w")
