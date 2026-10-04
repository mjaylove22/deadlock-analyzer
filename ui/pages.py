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

import assets
from player_lookup import search_player
from matchups import hero_breakdown
from profiles import API_GAME_MODES, RANK_BANDS, hero_tier_list, player_profile, teammates, when
from report import TEAM_TITLES, team_summary
from settings import get_me, save_settings
from ui import images
from ui.theme import (BADGE_COLORS, COLORS, ITEM_SLOT_COLORS, MATCHUP_COLORS, PARTY_COLORS, button, card, dropdown,
                      label, pill, segmented)
from ui.widgets import bind_click, data_table, hero_label, matchup_strip, player_card, rank_pill

MODES = list(API_GAME_MODES)  # ["Normal", "Street Brawl"]
BANDS = dict(RANK_BANDS)      # label -> (lowest tier, highest tier) or None


def pct(value) -> str:
    return f"{value:.0%}" if value is not None else ""


def clock(unix_time: float) -> str:
    return time.strftime("%I:%M %p", time.localtime(unix_time)).lstrip("0")


def section(parent, title: str) -> tuple:
    """A card with a small upper-case title. Returns (outer, inner)."""
    outer, inner = card(parent, padding=16)
    label(inner, title.upper(), size=9, color="dim", bold=True, bg="card").pack(anchor="w", pady=(0, 8))
    return outer, inner


class Page:
    nav = None  # which top-bar tab to highlight

    def __init__(self, app, parent, **options):
        self.app = app
        self.frame = tk.Frame(parent, bg=COLORS["bg"])
        self.build(**options)

    def build(self, **options):
        raise NotImplementedError

    def message(self, text: str, parent=None, bg: str = "bg"):
        label(parent or self.frame, text, size=12, color="dim", bg=bg, justify="center").pack(expand=True, pady=40)

    def heading(self, title: str, subtitle: str = "", size: int = 20, gap: int = 12) -> tk.Frame:
        row = tk.Frame(self.frame, bg=COLORS["bg"])
        row.pack(fill="x", pady=(0, gap))
        label(row, title, size=size, heading=True).pack(side="left")
        if subtitle:
            label(row, subtitle, color="dim").pack(side="left", padx=14, pady=(size // 3, 0))
        return row

    def clear(self, widget):
        for child in widget.winfo_children():
            child.destroy()


class HomePage(Page):
    nav = "home"

    def build(self):
        label(self.frame, "Deadlock Analyzer", size=26, heading=True).pack(anchor="w")
        watching = ("Auto-detect is on: open the Esc menu on the PLAYERS tab in game and your lobby appears here."
                    if self.app.watching else "Auto-detect is off: press Ctrl+Shift+D in game to capture the lobby.")
        label(self.frame, watching, size=11, color="dim").pack(anchor="w", pady=(0, 18))

        grid = tk.Frame(self.frame, bg=COLORS["bg"])
        grid.pack(fill="x")
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="home")
        self.account_section(grid)[0].grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.lobby_section(grid)[0].grid(row=0, column=1, sticky="nsew", padx=8)
        self.explore_section(grid)[0].grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        lower = tk.Frame(self.frame, bg=COLORS["bg"])
        lower.pack(fill="both", expand=True, pady=(16, 0))
        lower.columnconfigure(0, weight=1, uniform="lower")
        lower.columnconfigure(1, weight=1, uniform="lower")
        heroes_outer, self.heroes_box = section(lower, "Strongest heroes right now · Normal")
        heroes_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        recent_outer, self.recent_box = section(lower, "Your recent matches")
        recent_outer.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.load_lower()

    def account_section(self, parent):
        outer, inner = section(parent, "Your account")
        me = get_me()
        if not me:
            label(inner, "Tell the app which Steam account is yours,\nso it can always find you and show your matchup.",
                  color="dim", bg="card", justify="left").pack(anchor="w")
            button(inner, "Find my account", self.app.focus_search, primary=True).pack(anchor="w", pady=(12, 0))
            return outer, inner
        label(inner, me["name"], size=16, heading=True, bg="card").pack(anchor="w")
        row = tk.Frame(inner, bg=COLORS["card"])
        row.pack(anchor="w", pady=(12, 0))
        button(row, "My stats", self.app.open_my_stats, primary=True).pack(side="left")
        button(row, "Change account", self.app.focus_search).pack(side="left", padx=8)
        return outer, inner

    def lobby_section(self, parent):
        outer, inner = section(parent, "Last lobby")
        lobby = self.app.lobby
        if not lobby:
            label(inner, "No lobby captured yet.", color="dim", bg="card").pack(anchor="w")
            button(inner, "Analyze latest screenshot", self.app.analyze_latest).pack(anchor="w", pady=(12, 0))
            return outer, inner
        label(inner, f"{len(lobby['results'])} players · {clock(lobby['time'])}", size=16, heading=True, bg="card").pack(anchor="w")
        for team, title in TEAM_TITLES.items():
            members = [r for r in lobby["results"] if r["team"] == team]
            parties = [p for p in lobby["parties"] if lobby["results"][p[0]]["team"] == team]
            label(inner, f"{title.title()}: {team_summary(members, parties)}", size=9, color="dim", bg="card",
                  justify="left", wraplength=320).pack(anchor="w")
        button(inner, "Open lobby", self.app.open_lobby, primary=True).pack(anchor="w", pady=(12, 0))
        return outer, inner

    def explore_section(self, parent):
        outer, inner = section(parent, "Explore")
        label(inner, "Hero win rates and pick rates, or look up\nany player by their Steam name.",
              color="dim", bg="card", justify="left").pack(anchor="w")
        row = tk.Frame(inner, bg=COLORS["card"])
        row.pack(anchor="w", pady=(12, 0))
        button(row, "Hero tier list", self.app.open_heroes, primary=True).pack(side="left")
        button(row, "Search a player", self.app.focus_search).pack(side="left", padx=8)
        return outer, inner

    def load_lower(self):
        """Fill the two lower sections in the background (the tier list is cached for the session)."""
        me = get_me()
        for box in (self.heroes_box, self.recent_box):
            label(box, "Loading...", color="dim", bg="card").pack(anchor="w")

        def work():
            names = self.app.hero_names_by_id()
            tiers = self.app.cache.get(("tiers", "Normal", "All ranks")) or hero_tier_list(names, "normal")
            recent = player_profile(me["account_id"], names)["recent"][:8] if me else None
            return tiers, recent

        def done(result):
            tiers, recent = result
            self.app.cache[("tiers", "Normal", "All ranks")] = tiers
            for box in (self.heroes_box, self.recent_box):
                for widget in box.winfo_children()[1:]:  # keep each section's title
                    widget.destroy()
            for n, r in enumerate(tiers[:8], start=1):
                row = tk.Frame(self.heroes_box, bg=COLORS["card"])
                row.pack(fill="x", pady=2)
                label(row, f"{n}", color="faint", bg="card", width=2, anchor="w").pack(side="left")
                hero_label(row, r["hero"], "card", size=24, color=COLORS["text"]).pack(side="left")
                label(row, f"{r['pick_rate']:.0%} picked", bg="card", color="dim").pack(side="right")
                label(row, f"{r['win_rate']:.1%}", bg="card", color="win", bold=True).pack(side="right", padx=14)
                bind_click(row, lambda hero=r["hero"]: self.app.open_hero(hero))
            button(self.heroes_box, "Full tier list", self.app.open_heroes).pack(anchor="w", pady=(10, 0))
            if recent is None:
                label(self.recent_box, "Set your account to see your recent matches here.", color="dim", bg="card").pack(anchor="w")
                return
            for m in recent:
                row = tk.Frame(self.recent_box, bg=COLORS["card"])
                row.pack(fill="x", pady=2)
                result = pill(row, "WIN" if m["won"] else "LOSS", COLORS["win"] if m["won"] else COLORS["loss"])
                result.configure(width=46)  # same width for WIN and LOSS, so the columns line up
                result.pack(side="left", padx=(0, 10))
                hero_label(row, m["hero"], "card", size=24, color=COLORS["text"]).pack(side="left")
                label(row, f"{m['mode']} · {when(m['start_time'])}", bg="card", color="dim").pack(side="right")
                label(row, f"{m['kills']}/{m['deaths']}/{m['assists']}", bg="card").pack(side="right", padx=14)
            button(self.recent_box, "All my stats", self.app.open_my_stats).pack(anchor="w", pady=(10, 0))
        self.app.run_task(work, done)


class LobbyPage(Page):
    nav = "lobby"

    def build(self):
        lobby = self.app.lobby
        subtitle = f"captured {clock(lobby['time'])} · {os.path.basename(lobby['path'])}" if lobby else ""
        row = self.heading("Lobby", subtitle, size=16, gap=6)
        button(row, "Open screenshot...", self.app.open_screenshot).pack(side="right")
        button(row, "Analyze latest", self.app.analyze_latest).pack(side="right", padx=8)
        if not lobby:
            self.message("No lobby yet.\n\nOpen the Esc menu on the PLAYERS tab in game, or analyze a saved screenshot.")
            return
        if not lobby["results"]:
            self.message("No players found in that screenshot.\n\nMake sure the Esc menu is open on the PLAYERS tab.")
            return

        if lobby.get("matchup"):
            matchup_strip(self.frame, lobby["matchup"]).pack(side="bottom", fill="x", pady=(6, 0))
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
            head.pack(fill="x", pady=(0, 2))
            tk.Frame(head, bg=COLORS[team], width=4, height=20).pack(side="left", padx=(0, 10))
            label(head, title, size=13, color=team, heading=True).pack(side="left")
            label(head, team_summary([results[i] for i in members], team_parties), color="dim").pack(side="left", padx=12)
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

    def show(self, query, results, downloaded):
        self.app.avatars.store(downloaded)
        self.clear(self.body)
        if not results:
            self.message(f"No Steam profiles found for {query!r}.", self.body)
            return
        me = get_me()
        for r in results:
            r["is_me"] = bool(me and r["account_id"] == me["account_id"])
        exact = results[0]["note"] == "exact name"
        count = f"{len(results)} account" + ("" if len(results) == 1 else "s")
        label(self.body, f"{count} with this exact name. Click one to see their stats." if exact
              else "No exact match. Closest names:", color="dim").pack(anchor="w", pady=(0, 8))
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

    def build(self, mode: str = "Normal", band: str = "All ranks"):
        self.mode, self.band = mode, band
        row = self.heading("Heroes", "win rate and pick rate · click a hero for matchups and items")
        segmented(row, MODES, mode, lambda m: self.app.open_heroes(m, band, push=False)).pack(side="right")
        dropdown(row, list(BANDS), band, lambda b: self.app.open_heroes(mode, b, push=False)).pack(side="right", padx=10)
        self.body = tk.Frame(self.frame, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True)
        key = ("tiers", mode, band)
        cached = self.app.cache.get(key)
        if cached:
            self.show(cached)
            return
        self.message("Loading hero stats...", self.body)

        def work():
            return hero_tier_list(self.app.hero_names_by_id(), API_GAME_MODES[mode], BANDS[band])

        def done(rows):
            self.app.cache[key] = rows
            self.show(rows)
        self.app.run_task(work, done)

    def show(self, rows):
        self.clear(self.body)
        for n, r in enumerate(rows, start=1):
            r["position"] = n  # position by win rate, kept when re-sorting by another column
        data_table(self.body, [
            ("position", "#", 50, str, "center"),
            ("win_rate", "Win rate", 100, lambda v: f"{v:.1%}", "center"),
            ("pick_rate", "Pick rate", 100, pct, "center"),
            ("games", "Games", 110, lambda v: f"{v:,}", "center"),
            ("kda", "KDA", 80, lambda v: f"{v:.2f}", "center"),
        ], list(rows), height=17, hero_key="hero",
            on_click=lambda r: self.app.open_hero(r["hero"], self.mode, self.band))
        label(self.body, "Click a column heading to sort, or a hero for details. Heroes with under 500 games are left out.",
              size=9, color="dim").pack(anchor="w", pady=(8, 0))
        self.app.set_status(f"{len(rows)} heroes")


class HeroPage(Page):
    nav = "heroes"

    def build(self, hero: str, mode: str = "Normal", band: str = "All ranks"):
        self.hero, self.mode, self.band = hero, mode, band
        self.message(f"Loading {hero}...")

        def work():
            names = self.app.hero_names_by_id()
            hero_id = next(i for i, n in names.items() if n == hero)
            tiers = self.app.cache.get(("tiers", mode, band)) or hero_tier_list(names, API_GAME_MODES[mode], BANDS[band])
            breakdown = hero_breakdown(hero_id, names, API_GAME_MODES[mode], BANDS[band])
            assets.load(images.hero_card_url(hero), assets.PORTRAIT_MAX_SIDE)
            return tiers, breakdown
        self.app.run_task(work, lambda result: self.show(*result))

    def show(self, tiers, b):
        self.app.cache[("tiers", self.mode, self.band)] = tiers
        self.clear(self.frame)
        stats = next((r for r in tiers if r["hero"] == self.hero), None)

        outer, header = card(self.frame, padding=16)
        outer.pack(fill="x")
        portrait = images.hero_card(self.hero, 120)
        if portrait:
            tk.Label(header, image=portrait, bg=COLORS["card"]).pack(side="left", padx=(0, 18))
        info = tk.Frame(header, bg=COLORS["card"])
        info.pack(side="left", fill="both", expand=True)
        label(info, self.hero, size=24, heading=True, bg="card", color=images.readable_on_dark(images.hero_color(self.hero))).pack(anchor="w")
        label(info, f"{self.mode} · {self.band}", color="dim", bg="card").pack(anchor="w", pady=(0, 10))
        chips = tk.Frame(info, bg=COLORS["card"])
        chips.pack(anchor="w")
        if stats:
            for text in (f"#{tiers.index(stats) + 1} by win rate",  # tiers are sorted by win rate
                         f"{stats['win_rate']:.1%} win rate", f"{stats['pick_rate']:.0%} pick rate",
                         f"{stats['games']:,} games", f"{stats['kda']:.2f} KDA"):
                pill(chips, text, COLORS["button"], size=10, text_color=COLORS["text"]).pack(side="left", padx=(0, 6))
        controls = tk.Frame(header, bg=COLORS["card"])
        controls.pack(side="right", anchor="n")
        segmented(controls, MODES, self.mode, lambda m: self.app.open_hero(self.hero, m, self.band, push=False)).pack(anchor="e")
        dropdown(controls, list(BANDS), self.band, lambda band: self.app.open_hero(self.hero, self.mode, band, push=False)).pack(anchor="e", pady=(8, 0))

        columns = tk.Frame(self.frame, bg=COLORS["bg"])
        columns.pack(fill="both", expand=True, pady=(14, 0))
        for c in range(3):
            columns.columnconfigure(c, weight=1, uniform="hero")
        average = f"vs its {b['average_win_rate']:.1%} average"
        for c, (title, matchups) in enumerate((("Best matchups", b["best"]), ("Toughest matchups", b["toughest"]))):
            box_outer, box = card(columns, padding=14)
            box_outer.grid(row=0, column=c, sticky="nsew", padx=(0, 8) if c == 0 else 8)
            label(box, title.upper(), size=9, color="dim", bold=True, bg="card").pack(anchor="w")
            label(box, average, size=9, color="faint", bg="card").pack(anchor="w", pady=(0, 8))
            for m in matchups:
                row = tk.Frame(box, bg=COLORS["card"])
                row.pack(fill="x", pady=3)
                hero_label(row, m["enemy_hero"], "card", size=26, color=COLORS["text"]).pack(side="left")
                kind = "good" if m["vs_average"] > 0 else "bad"
                pill(row, f"{m['vs_average'] * 100:+.1f}", MATCHUP_COLORS[kind], size=9).pack(side="right")
                label(row, f"{m['win_rate']:.1%}", bg="card").pack(side="right", padx=10)
                bind_click(row, lambda h=m["enemy_hero"]: self.app.open_hero(h, self.mode, self.band))

        items_outer, items = card(columns, padding=14)
        items_outer.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        label(items, "MOST-BOUGHT ITEMS", size=9, color="dim", bold=True, bg="card").pack(anchor="w")
        label(items, "win rates run high for expensive late items", size=9, color="faint", bg="card").pack(anchor="w", pady=(0, 8))
        for item in b["items"]:
            row = tk.Frame(items, bg=COLORS["card"])
            row.pack(fill="x", pady=3)
            swatch = pill(row, "", ITEM_SLOT_COLORS.get(item["slot"], COLORS["button"]))  # shop category colour
            swatch.configure(width=8)
            swatch.pack(side="left", padx=(0, 8))
            label(row, item["name"], bg="card").pack(side="left")
            label(row, f"{item['win_rate']:.0%}", bg="card", color="dim").pack(side="right")
        legend = tk.Frame(items, bg=COLORS["card"])
        legend.pack(anchor="w", pady=(10, 0))
        for slot, color in ITEM_SLOT_COLORS.items():
            pill(legend, slot.title(), color, size=8).pack(side="left", padx=(0, 4))
        self.app.set_status(f"{self.hero} · click a matchup to open that hero")


class PlayerPage(Page):
    nav = None

    def build(self, account_id: int, mode: str = "Normal", nav: str = None):
        self.nav = nav
        self.account_id, self.mode = account_id, mode
        self.message("Loading player...")

        def work():
            profile = player_profile(account_id, self.app.hero_names_by_id(), API_GAME_MODES[mode])
            if profile["heroes"]:
                assets.load(images.hero_card_url(profile["heroes"][0]["hero"]), assets.PORTRAIT_MAX_SIDE)  # main hero's portrait
            return profile, self.app.avatars.download([profile["avatar_url"]])
        self.app.run_task(work, lambda result: self.show(*result))

    def show(self, p: Dict[str, Any], downloaded):
        self.app.avatars.store(downloaded)
        self.clear(self.frame)
        me = get_me()
        is_me = bool(me and me["account_id"] == p["account_id"])

        # Header: who they are, with their most-played hero's portrait on the right
        outer, header = card(self.frame, padding=16)
        outer.pack(fill="x")
        tk.Label(header, image=self.app.avatars.get(p["avatar_url"], 92, ring=COLORS["accent"]),
                 bg=COLORS["card"]).pack(side="left")
        main = p["heroes"][0]["hero"] if p["heroes"] else None
        portrait = images.hero_card(main, 110) if main else None
        if portrait:
            showcase = tk.Frame(header, bg=COLORS["card"])
            showcase.pack(side="right", padx=(16, 0))
            tk.Label(showcase, image=portrait, bg=COLORS["card"]).pack()
            label(showcase, f"Main: {main}", size=9, color="dim", bg="card").pack()
        actions = tk.Frame(header, bg=COLORS["card"])
        actions.pack(side="right", anchor="n")
        if p.get("profile_url"):
            button(actions, "Steam profile", lambda: webbrowser.open(p["profile_url"])).pack(anchor="e")
        if is_me:
            label(actions, "This is your account", size=9, color="accent", bg="card").pack(anchor="e", pady=(10, 0))
        else:
            button(actions, "Set as my account", lambda: self.set_as_me(p)).pack(anchor="e", pady=(8, 0))

        info = tk.Frame(header, bg=COLORS["card"], padx=18)
        info.pack(side="left", fill="both", expand=True)
        name_row = tk.Frame(info, bg=COLORS["card"])
        name_row.pack(anchor="w")
        label(name_row, p["name"], size=22, heading=True, bg="card").pack(side="left")
        if is_me:
            pill(name_row, "YOU", BADGE_COLORS["you"], size=9).pack(side="left", padx=(12, 0), pady=(8, 0))
        rp = rank_pill(name_row, p.get("rank"), size=10)
        if rp:
            rp.pack(side="left", padx=(8, 0), pady=(8, 0))
        recent = f" · {p['recent_30d']} in the last 30 days" if p.get("recent_30d") is not None else ""
        label(info, f"{p['total_matches']:,} recorded matches{recent}", color="dim", bg="card").pack(anchor="w", pady=(2, 8))
        chips = tk.Frame(info, bg=COLORS["card"])
        chips.pack(anchor="w")
        for m in p["modes"]:
            pill(chips, f"{m['mode']}  {m['games']:,} games · {m['win_rate']:.0%} WR", COLORS["button"],
                 size=9, text_color=COLORS["text"]).pack(side="left", padx=(0, 6))

        # Frequent teammates: loaded after the page is on screen, so they never delay it
        self.mates_row = tk.Frame(self.frame, bg=COLORS["bg"])
        self.mates_row.pack(fill="x", pady=(10, 0))
        label(self.mates_row, "Plays most with", size=10, color="dim", bold=True).pack(side="left", padx=(2, 10))
        self.mates_loading = label(self.mates_row, "loading...", color="faint")
        self.mates_loading.pack(side="left")

        def load_mates():
            mates = teammates(p["account_id"])
            return mates, self.app.avatars.download(m.get("avatar_url") for m in mates)
        self.app.run_task(load_mates, lambda result: self.show_mates(*result))

        # Mode switch, then the two tables side by side
        row = tk.Frame(self.frame, bg=COLORS["bg"])
        row.pack(fill="x", pady=(12, 8))
        label(row, "Hero stats", size=14, heading=True).pack(side="left")
        segmented(row, MODES, self.mode, self.switch_mode).pack(side="left", padx=14)
        label(row, "Recent matches", size=14, heading=True).pack(side="right")

        tables = tk.Frame(self.frame, bg=COLORS["bg"])
        tables.pack(fill="both", expand=True)
        tables.columnconfigure(0, weight=11, uniform="tables")
        tables.columnconfigure(1, weight=10, uniform="tables")
        left, right = tk.Frame(tables, bg=COLORS["bg"]), tk.Frame(tables, bg=COLORS["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        if p["heroes"]:
            data_table(left, [
                ("games", "Games", 60, str, "center"),
                ("win_rate", "Win rate", 70, pct, "center"),
                ("kda", "KDA", 55, lambda v: f"{v:.1f}", "center"),
                ("damage_per_min", "Dmg/min", 70, lambda v: f"{v:,.0f}", "center"),
                ("last_played", "Last played", 85, when, "center"),
            ], list(p["heroes"]), height=14, hero_key="hero")
        else:
            self.message(f"No recorded {self.mode} matches.", left)

        if p["recent"]:
            for m in p["recent"]:
                m["kda_text"] = f"{m['kills']}/{m['deaths']}/{m['assists']}"
                m["result"] = "Win" if m["won"] else "Loss"
            data_table(right, [
                ("start_time", "When", 70, when, "center"),
                ("result", "Result", 55, str, "center"),
                ("kda_text", "K/D/A", 70, str, "center"),
                ("mode", "Mode", 90, str, "center"),
            ], list(p["recent"]), height=14, tag=lambda m: "win" if m["won"] else "loss", hero_key="hero")
        else:
            self.message("No recorded matches.", right)
        self.app.set_status(f"{p['name']} · click a column heading to sort")

    def show_mates(self, mates, downloaded):
        self.app.avatars.store(downloaded)
        self.mates_loading.destroy()
        if not mates:
            label(self.mates_row, "no frequent teammates on record", color="faint").pack(side="left")
            return
        for m in mates:
            outer, inner = card(self.mates_row, padding=5, hoverable=True)
            outer.pack(side="left", padx=(0, 8))
            tk.Label(inner, image=self.app.avatars.get(m.get("avatar_url"), 26), bg=COLORS["card"]).pack(side="left")
            name = m["name"] if len(m["name"]) <= 16 else m["name"][:15] + "…"  # long names would push cards off-screen
            label(inner, f" {name}", size=10, bold=True, bg="card").pack(side="left")
            label(inner, f"  {m['games']} games · {m['win_rate']:.0%}", size=9, color="dim", bg="card").pack(side="left", padx=(0, 4))
            outer.after_idle(lambda outer=outer, m=m: bind_click(outer, lambda: self.app.open_player(m["account_id"])))

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
        outer, inner = card(self.frame, padding=20)
        outer.pack(fill="x")
        label(inner, "The app doesn't know which Steam account is yours yet.", size=13, heading=True, bg="card").pack(anchor="w")
        label(inner, "Search your Steam name in the box at the top, open your account, and click "
                     "\"Set as my account\".\nYou'll then be identified exactly in every lobby, "
                     "your matchup will show, and this tab will open your stats.",
              color="dim", bg="card", justify="left").pack(anchor="w", pady=(6, 14))
        button(inner, "Find my account", self.app.focus_search, primary=True).pack(anchor="w")
