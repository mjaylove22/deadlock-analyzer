"""The app's pages: Home, Lobby, Heroes, Search, Player (also used for My Stats) and account setup.

Each page builds its widgets into self.frame. Slow data is loaded with app.run_task(work, on_done):
work runs on a worker thread, on_done runs on the main thread, and only if the user is still on
the same page (otherwise the result is dropped).
"""

import logging
import os
import time
import tkinter as tk
import webbrowser
from typing import Any, Dict

import assets
import deadlock_api
from match_review import REVIEW_STATS, MatchUnavailable, match_review, rate_match
from performance import compared_text
from guides import hero_guide
from item_trends import item_trends
from matchups import hero_breakdown, matchup_details
from player_lookup import search_player
from profiles import (API_GAME_MODES, LOW_SAMPLE_GAMES, MATCH_TYPES, RANK_BANDS, hero_rank_curve, hero_tier_list, hero_trends,
                      player_profile, teammates, when)
from report import TEAM_TITLES, team_summary
from scoreboard_ocr import find_tesseract
from settings import get_me, get_preferences, save_settings, set_preference
from ui import images
from ui.theme import (BADGE_COLORS, COLORS, ITEM_SLOT_COLORS, MATCHUP_COLORS, PARTY_COLORS, button, card, dropdown,
                      label, pill, segmented, switch)
from version import __version__
from ui.charts import (ITEM_DAYS, ITEM_TREND_SPAN, ChartTable, Column, advantage_bar, change_text, change_tip, hero_cell, item_cell,
                       percentile_bar, score_color, trend_cell, trend_chart, trend_color, trend_tip, verdict)
from ui.widgets import item_tile, item_tooltip_text, tooltip
from ui.widgets import bind_click, data_table, hero_label, matchup_strip, player_card, rank_pill

MODES = list(API_GAME_MODES)  # ["Normal", "Street Brawl"]
HOTKEY_TEXT = "Ctrl+Shift+D"
logger = logging.getLogger(__name__)
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
        self.options = options
        self.frame = tk.Frame(parent, bg=COLORS["bg"])
        self.build(**options)

    def reload(self):
        self.app.navigate(type(self), push=False, **self.options)

    def unavailable(self, parent, what: str, bg: str = "card"):
        """In place of a part of the page that didn't load: says so, with a button to try again."""
        label(parent, f"Couldn't load {what}: the stats server is slow or busy right now.",
              color="dim", bg=bg, justify="left", wraplength=280).pack(anchor="w", pady=(4, 10))
        button(parent, "Try again", self.reload).pack(anchor="w")

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
        title = tk.Frame(self.frame, bg=COLORS["bg"])
        title.pack(anchor="w")
        label(title, "Deadlock Analyzer", size=26, heading=True).pack(side="left")
        label(title, f"v{__version__}", size=11, color="dim").pack(side="left", padx=(10, 0), pady=(10, 0))
        watching = ("Auto-detect is on: open the Esc menu on the PLAYERS tab in game and your lobby appears here."
                    if self.app.watching else "Auto-detect is off: press Ctrl+Shift+D in game to capture the lobby.")
        label(self.frame, watching, size=11, color="dim").pack(anchor="w", pady=(0, 18))
        if not find_tesseract():
            outer, inner = card(self.frame, padding=14)
            outer.pack(fill="x", pady=(0, 16))
            label(inner, "One more thing to install: the OCR engine", size=14, heading=True, bg="card").pack(anchor="w")
            label(inner, "The app reads the scoreboard with Tesseract OCR, a free program that isn't installed yet. "
                         "Click below to install it with Windows' own installer (winget); allow it if Windows asks.",
                  color="dim", bg="card", justify="left", wraplength=900).pack(anchor="w", pady=(4, 10))
            button(inner, "Install Tesseract", self.app.install_tesseract, primary=True).pack(anchor="w")

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
                bind_click(row, lambda match_id=m["match_id"]: self.app.open_match(match_id))
            button(self.recent_box, "All my stats", self.app.open_my_stats).pack(anchor="w", pady=(10, 0))
        self.app.run_task(work, done)


class LobbyPage(Page):
    nav = "lobby"

    def build(self):
        lobby = self.app.lobby
        subtitle = f"captured {clock(lobby['time'])} · {os.path.basename(lobby['path'])}" if lobby else ""
        if lobby and lobby.get("match_id"):
            subtitle += f" · match {lobby['match_id']}"
        row = self.heading("Lobby", subtitle, size=16, gap=6)
        button(row, "Open screenshot...", self.app.open_screenshot).pack(side="right")
        button(row, "Analyze latest", self.app.analyze_latest).pack(side="right", padx=8)
        if lobby and lobby.get("match_id"):
            review = button(row, "Review this match", lambda: self.app.open_match(lobby["match_id"]), primary=True)
            review.pack(side="right")
            tooltip(review, "The full match: scoreboards, your build and how you played on your hero.\n"
                            "Available once the match is over.")
        if not lobby:
            self.message("No lobby yet.\n\nOpen the Esc menu on the PLAYERS tab in game, or analyze a saved screenshot.")
            return
        if not lobby["results"]:
            self.message("No players found in that screenshot.\n\nMake sure the Esc menu is open on the PLAYERS tab.")
            return

        show = get_preferences()
        if lobby.get("matchup") and show["show_matchup"]:
            matchup_strip(self.frame, lobby["matchup"], on_open=lambda: self.app.navigate(MatchupPage)).pack(
                side="bottom", fill="x", pady=(6, 0))
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
                player_card(frame, r, COLORS[team], self.app.avatars, party=party_of.get(i), show=show,
                            on_open=lambda r=r: self.app.open_player(r["account_id"]), on_search=self.app.search)


def counter_item_tip(item: Dict[str, Any], against: str) -> str:
    lines = [item["name"], f"{item['win_rate']:.0%} win rate against {against}, {item['usual_win_rate']:.0%} usually",
             f"About {item['lift'] * 100:+.1f} points beyond what the matchup itself does"]
    if item.get("bought_share"):
        lines.append(f"Bought in {item['bought_share']:.0%} of these games")
    return "\n".join(lines)


class MatchupPage(Page):
    """Your hero against this lobby: the overall read, each enemy (hero and player), and what to buy."""
    nav = "lobby"

    def build(self):
        lobby = self.app.lobby
        matchup = lobby.get("matchup") if lobby else None
        if not matchup:
            self.heading("Your matchup")
            self.message("Your matchup appears here once you're in a lobby and the app knows your account\n"
                         "(search your Steam name, open your page, and click \"Set as my account\").")
            return
        results = lobby["results"]
        me = next(r for r in results if r.get("is_me"))
        ids = {name: hero_id for hero_id, name in self.app.hero_names_by_id().items()}
        enemies = [r for r in results if r["team"] != me["team"] and r["hero"] in ids]
        allies = [r for r in results if r["team"] == me["team"] and not r.get("is_me") and r["hero"] in ids]
        key = ("matchup", lobby["time"])
        if key in self.app.cache:
            self.show(self.app.cache[key], matchup, me, enemies, allies, ids)
            return
        self.message(f"Loading your {me['hero']} matchup...")

        def work():
            details = matchup_details(ids[me["hero"]], [ids[r["hero"]] for r in enemies],
                                      [ids[r["hero"]] for r in allies], matchup.get("game_mode", "normal"))
            shown = details["team_items"] + [i for e in details["enemies"] for i in e.get("counter_items", [])]
            deadlock_api.parallel(lambda: assets.load_many((i["image"] for i in shown), assets.ITEM_MAX_SIDE),
                                  lambda: assets.load(images.hero_card_url(me["hero"]), assets.PORTRAIT_MAX_SIDE))
            return details

        def done(details):
            self.app.cache[key] = details
            self.show(details, matchup, me, enemies, allies, ids)

        def failed(error):
            self.clear(self.frame)
            self.heading("Your matchup")
            self.unavailable(self.frame, "your matchup", bg="bg")
        self.app.run_task(work, done, failed)

    def show(self, d, matchup, me, enemy_results, ally_results, ids):
        self.clear(self.frame)
        hero = me["hero"]
        names = {hero_id: name for name, hero_id in ids.items()}
        players = {ids[r["hero"]]: r for r in enemy_results + ally_results}  # each hero is in a match once
        mode = "Street Brawl" if d["game_mode"] == "street_brawl" else "normal"
        shift = d["expected"] - d["average_win_rate"]
        word, kind = verdict(shift)

        # The overall read
        outer, top = card(self.frame, padding=14)
        outer.pack(fill="x")
        portrait = images.hero_card(hero, 96)
        if portrait:
            tk.Label(top, image=portrait, bg=COLORS["card"]).pack(side="left", padx=(0, 16))
        summary = tk.Frame(top, bg=COLORS["card"])
        summary.pack(side="left", fill="y")
        label(summary, "YOUR MATCHUP", size=9, color="dim", bold=True, bg="card").pack(anchor="w")
        label(summary, f"{hero} vs this team", size=20, heading=True, bg="card",
              color=images.readable_on_dark(images.hero_color(hero))).pack(anchor="w")
        line = tk.Frame(summary, bg=COLORS["card"])
        line.pack(anchor="w", pady=(4, 4))
        label(line, f"≈ {d['expected']:.1%}", size=18, bold=True, bg="card",
              color={"good": "win", "bad": "loss", "even": "text"}[kind]).pack(side="left")
        pill(line, f"{word} {shift * 100:+.1f}", MATCHUP_COLORS[kind], size=10).pack(side="left", padx=10)
        label(summary, f"Your {hero} wins {d['average_win_rate']:.1%} of {mode} games; against these heroes, about "
                       f"{shift * 100:+.1f} points. A rough read: the players matter more than the heroes.",
              size=9, color="dim", bg="card", justify="left", wraplength=470).pack(anchor="w")
        if d["allies"]:
            team = tk.Frame(top, bg=COLORS["card"])
            team.pack(side="right", anchor="n")
            label(team, "WITH YOUR TEAM", size=9, color="dim", bold=True, bg="card").pack(anchor="e", pady=(0, 6))
            for a in d["allies"]:
                row = tk.Frame(team, bg=COLORS["card"])
                row.pack(anchor="e", pady=2)
                ally = names.get(a["hero_id"], "?")
                hero_label(row, ally, "card", size=22, color=COLORS["text"]).pack(side="left", padx=(0, 8))
                faded = a["games"] < LOW_SAMPLE_GAMES
                advantage_bar(row, a["vs_average"], width=90, faded=faded).pack(side="left", padx=(0, 6))
                label(row, f"{a['vs_average'] * 100:+.1f}", size=9, bold=True, bg="card",
                      color="faint" if faded else "win" if a["vs_average"] >= 0 else "loss", width=5, anchor="e").pack(side="left")
                tooltip(row, f"{hero} with {ally}: {a['win_rate']:.1%} win rate in {a['games']:,} games\n"
                             f"({a['vs_average'] * 100:+.1f} vs your {hero}'s average)" + ("\nFew games: take it lightly" if faded else ""))

        # Each enemy, toughest first
        label(self.frame, "AGAINST EACH ENEMY  ·  toughest first  ·  bars: your win rate against their hero, "
                          "from your usual (the middle line)", size=9, color="dim", bold=True).pack(anchor="w", pady=(12, 4))
        grid = tk.Frame(self.frame, bg=COLORS["bg"])
        grid.pack(fill="x")
        per_row = 3 if len(d["enemies"]) > 4 else 2  # 6v6: two rows of three, so the whole page fits
        grid.columnconfigure(tuple(range(per_row)), weight=1, uniform="enemies")
        for n, e in enumerate(d["enemies"]):
            box_outer, box = card(grid, padding=10)
            column = n % per_row
            box_outer.grid(row=n // per_row, column=column, sticky="nsew", pady=4,
                           padx=(0 if column == 0 else 5, 0 if column == per_row - 1 else 5))
            self.enemy_card(box, e, players.get(e["hero_id"]), names.get(e["hero_id"], "?"), hero, d)

        # What to buy against the whole team
        if d["team_items"]:
            shelf_outer, shelf = card(self.frame, padding=12)
            shelf_outer.pack(fill="x", pady=(8, 0))
            label(shelf, "BUY AGAINST THIS TEAM", size=9, color="dim", bold=True, bg="card").pack(anchor="w")
            label(shelf, "items that win more than they usually do against these heroes (hover for details)",
                  size=9, color="faint", bg="card").pack(anchor="w", pady=(0, 8))
            row = tk.Frame(shelf, bg=COLORS["card"])
            row.pack(fill="x")
            for item in d["team_items"]:
                cell = tk.Frame(row, bg=COLORS["card"])
                cell.pack(side="left", padx=(0, 18))
                tk.Label(cell, image=images.item_icon(item, 34), bg=COLORS["card"]).pack(side="left", padx=(0, 8))
                text = tk.Frame(cell, bg=COLORS["card"])
                text.pack(side="left")
                label(text, item["name"], size=10, bg="card").pack(anchor="w")
                label(text, f"{item['lift'] * 100:+.1f} vs usual", size=9, bold=True, color="win", bg="card").pack(anchor="w")
                tooltip(cell, counter_item_tip(item, "this team"))
        self.app.set_status(f"{hero} vs this team · hover bars and items for the numbers")

    def enemy_card(self, box, e, player, enemy, hero, d):
        head = tk.Frame(box, bg=COLORS["card"])
        head.pack(fill="x")
        hero_label(head, enemy, "card", size=30, font_size=12).pack(side="left")
        if player:
            label(head, f"  {player['player']}", color="dim", bg="card").pack(side="left")
            rp = rank_pill(head, player.get("rank"))
            if rp:
                rp.pack(side="right")
        # The player on this hero: how dangerous are they?
        threat = tk.Frame(box, bg=COLORS["card"])
        threat.pack(fill="x", pady=(4, 6))
        stats = (player or {}).get("hero_stats")
        if player and player["status"] == "found" and stats:
            danger = stats["games"] >= 20 and stats["win_rate"] >= 0.55
            label(threat, f"{stats['games']} game{'' if stats['games'] == 1 else 's'} on {enemy} · {stats['win_rate']:.0%} WR · {stats['kda']:.1f} KDA",
                  size=9, color="loss" if danger else "text", bold=danger, bg="card").pack(side="left")
            for text, kind in player["badges"][:2]:
                pill(threat, text, BADGE_COLORS[kind], size=8).pack(side="left", padx=(6, 0))
        else:
            label(threat, f"First recorded game on {enemy}" if player and player["status"] == "found" else "Player not identified",
                  size=9, color="faint", bg="card").pack(side="left")

        if e["win_rate"] is None:
            label(box, f"Too few {hero} vs {enemy} games to judge", size=9, color="faint", bg="card").pack(anchor="w")
            return
        rows = [("Matchup", e["win_rate"], e["vs_average"], e["games"])]
        if d["game_mode"] == "normal" and e["lane_win_rate"] is not None:
            rows.append(("In lane", e["lane_win_rate"], e["lane_vs_average"], e["lane_games"]))
        for title, win_rate, shift, games in rows:
            row = tk.Frame(box, bg=COLORS["card"])
            row.pack(fill="x", pady=1)
            label(row, title, size=9, color="dim", bg="card", width=7, anchor="w").pack(side="left")
            faded = games < LOW_SAMPLE_GAMES
            advantage_bar(row, shift, width=120, faded=faded).pack(side="left", padx=(0, 8))
            label(row, f"{win_rate:.1%}", size=10, bold=True, bg="card").pack(side="left")
            label(row, f"  {shift * 100:+.1f}", size=9, bold=True, bg="card",
                  color="faint" if faded else "win" if shift >= 0 else "loss").pack(side="left")
            where = "when laning against them" if title == "In lane" else "in all games together"
            tooltip(row, f"{hero} vs {enemy} {where}: {win_rate:.1%} in {games:,} games\n"
                         f"{shift * 100:+.1f} vs your {hero}'s average of {d['average_win_rate']:.1%}")
        if e["kda"] and d["usual_kda"]:
            kills, deaths, assists = e["kda"]
            usual = d["usual_kda"]
            label(box, f"Your K/D/A vs them {kills:.1f}/{deaths:.1f}/{assists:.1f}  ·  usually {usual[0]:.1f}/{usual[1]:.1f}/{usual[2]:.1f}",
                  size=9, color="dim", bg="card").pack(anchor="w", pady=(4, 0))
        if e["counter_items"]:
            row = tk.Frame(box, bg=COLORS["card"])
            row.pack(fill="x", pady=(6, 0))
            label(row, "Buy vs them", size=9, color="dim", bg="card").pack(side="left", padx=(0, 8))
            for item in e["counter_items"]:
                cell = tk.Frame(row, bg=COLORS["card"])
                cell.pack(side="left", padx=(0, 10))
                tk.Label(cell, image=images.item_icon(item, 24), bg=COLORS["card"]).pack(side="left", padx=(0, 4))
                label(cell, f"{item['lift'] * 100:+.1f}", size=9, bold=True, color="win", bg="card").pack(side="left")
                tooltip(cell, counter_item_tip(item, enemy))


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


def band_for(mode: str, band: str) -> str:
    """Street Brawl isn't ranked (the API refuses a rank filter for it), so it's always all ranks."""
    return band if mode == "Normal" else "All ranks"


def rank_dropdown(parent, mode: str, band: str, command):
    menu = dropdown(parent, list(BANDS), band, command)
    if mode != "Normal":
        menu.configure(state="disabled")
        tooltip(menu, "Street Brawl isn't ranked, so it can't be split by rank")
    return menu


def win_rate_color(row) -> str:
    """Green or red only when clearly above or below even, so the colour means something."""
    return COLORS["win"] if row["win_rate"] >= 0.515 else COLORS["loss"] if row["win_rate"] <= 0.485 else COLORS["text"]


class HeroesPage(Page):
    nav = "heroes"

    def build(self, mode: str = "Normal", band: str = "All ranks"):
        band = band_for(mode, band)
        self.mode, self.band = mode, band
        row = self.heading("Heroes", "win rate, pick rate and how they've moved · click a hero for matchups and items")
        segmented(row, MODES, mode, lambda m: self.app.open_heroes(m, band, push=False)).pack(side="right")
        rank_dropdown(row, mode, band, lambda b: self.app.open_heroes(mode, b, push=False)).pack(side="right", padx=10)
        self.body = tk.Frame(self.frame, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True)
        key, trends_key = ("tiers", mode, band), ("trends", mode, band)
        if key in self.app.cache and trends_key in self.app.cache:
            self.show(self.app.cache[key], self.app.cache[trends_key])
            return
        self.message("Loading hero stats...", self.body)

        def work():
            names = self.app.hero_names_by_id()
            rows, trends = deadlock_api.parallel(
                lambda: hero_tier_list(names, API_GAME_MODES[mode], BANDS[band]),
                lambda: hero_trends(names, API_GAME_MODES[mode], BANDS[band]), allow_failures=True)
            if rows is None:
                raise OSError("the stats server didn't answer")
            return rows, trends

        def done(result):
            rows, trends = result
            if trends:  # cached only when complete, so a missing trend is retried next visit
                self.app.cache[key], self.app.cache[trends_key] = rows, trends
            self.show(rows, trends)

        def failed(error):
            self.clear(self.body)
            self.unavailable(self.body, "hero stats", bg="bg")
            self.app.set_status(f"Hero stats didn't load ({error})")
        self.app.run_task(work, done, failed)

    def show(self, rows, trends=None):
        self.clear(self.body)
        weeks = len(trends["weeks"]) if trends else 0
        for n, r in enumerate(rows, start=1):
            r["position"] = n  # position by win rate, kept when re-sorting by another column
            r["trend"] = trends["heroes"].get(r["hero"]) if trends else None
        by_change = lambda r: (r["trend"] or {}).get("change")  # noqa: E731
        columns = [Column("position", "#", 40), Column("hero", "Hero", 150, draw=hero_cell(), align="w")]
        if trends:
            columns += [Column("trend", "Last 12 weeks", 130, draw=trend_cell(weeks), tip=trend_tip(weeks), sort=by_change),
                        Column("change", "Change", 80, text=lambda r: change_text(r["trend"]), sort=by_change,
                               color=lambda r: trend_color(r["trend"]), tip=lambda r, x, box: change_tip(r["trend"]))]
        columns += [
            Column("win_rate", "Win rate", 90, text=lambda r: f"{r['win_rate']:.1%}", color=win_rate_color),
            Column("pick_rate", "Pick rate", 90, text=lambda r: pct(r["pick_rate"])),
            Column("games", "Games", 100, text=lambda r: f"{r['games']:,}"),
            Column("kda", "KDA", 70, text=lambda r: f"{r['kda']:.2f}"),
            Column("ban_share", "Ban share", 90, text=lambda r: f"{r['ban_share']:.1%}" if r["ban_share"] is not None else "-"),
        ]
        ChartTable(self.body, columns, list(rows), height_rows=17,
                   on_click=lambda r: self.app.open_hero(r["hero"], self.mode, self.band)).pack(fill="both", expand=True)
        notes = ("Last 12 weeks: each hero's weekly win rate, every line on the same scale around its own average "
                 "(hover for each week). Change: the last 4 weeks against 9-12 weeks ago; steady = within chance, "
                 "or under half a point. " if trends else "Trends didn't load this time. ")
        label(self.body, notes + "Heroes with under 500 games are left out. Ban share = the hero's part of all "
                                 "recorded bans (ranked games only). Click a heading to sort.",
              size=9, color="dim", justify="left", wraplength=1080).pack(anchor="w", pady=(8, 0))
        self.app.set_status(f"{len(rows)} heroes")


def buy_time_text(seconds) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else "-"


class ItemsPage(Page):
    """Every shop item, like the heroes page: how often it's bought, win rate, and how it moved."""
    nav = "items"
    CATEGORIES = ["All", "Weapon", "Vitality", "Spirit"]

    def build(self, mode: str = "Normal", band: str = "All ranks", category: str = "All"):
        band = band_for(mode, band)
        self.mode, self.band, self.category = mode, band, category
        row = self.heading("Items", "how often they're bought, win rate, and how they've moved over two weeks")
        segmented(row, MODES, mode, lambda m: self.app.open_items(m, band, category, push=False)).pack(side="right")
        rank_dropdown(row, mode, band, lambda b: self.app.open_items(mode, b, category, push=False)).pack(side="right", padx=10)
        segmented(row, self.CATEGORIES, category, lambda c: self.app.open_items(mode, band, c, push=False)).pack(side="right")
        self.body = tk.Frame(self.frame, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True)
        key = ("items", mode, band)
        if key in self.app.cache:
            self.show(self.app.cache[key])
            return
        self.message("Loading item stats (the first time takes a few seconds)...", self.body)

        def work():
            data = item_trends(API_GAME_MODES[mode], BANDS[band])
            # All icons once (then they're on disk); gently, so the image server doesn't drop any
            assets.load_many((r["image"] for r in data["rows"]), assets.ITEM_MAX_SIDE, workers=4)
            return data

        def done(data):
            self.app.cache[key] = data
            self.show(data)

        def failed(error):
            self.clear(self.body)
            self.unavailable(self.body, "item stats", bg="bg")
        self.app.run_task(work, done, failed)

    def show(self, data):
        self.clear(self.body)
        rows = [r for r in data["rows"] if self.category == "All" or r.get("slot") == self.category.lower()]
        for n, r in enumerate(rows, start=1):
            r["position"] = n  # by how often it's bought, kept when re-sorting
        days = len(data["days"])
        by_change = lambda r: r["trend"].get("change")  # noqa: E731
        item_color = lambda r: images.readable_on_dark(ITEM_SLOT_COLORS.get(r.get("slot"), COLORS["text"]))  # noqa: E731
        ChartTable(self.body, [
            Column("position", "#", 36),
            Column("name", "Item", 190, draw=item_cell(), align="w"),
            Column("trend", "Last 14 days", 130, draw=trend_cell(days, ITEM_TREND_SPAN),
                   tip=trend_tip(days, "name", ITEM_DAYS, ITEM_TREND_SPAN), sort=by_change),
            Column("change", "Change", 80, text=lambda r: change_text(r["trend"]), color=lambda r: trend_color(r["trend"]),
                   tip=lambda r, x, box: change_tip(r["trend"], ITEM_DAYS), sort=by_change),
            Column("bought", "Bought", 80, text=lambda r: f"{r['bought']:.1%}"),
            Column("win_rate", "Win rate", 80, text=lambda r: f"{r['win_rate']:.1%}", color=win_rate_color),
            Column("tier", "Tier", 50, text=lambda r: str(r.get("tier") or "-"), color=item_color),
            Column("cost", "Cost", 70, text=lambda r: f"{r['cost']:,}" if r.get("cost") else "-"),
            Column("buy_time", "Bought at", 80, text=lambda r: buy_time_text(r.get("buy_time"))),
        ], rows, height_rows=17).pack(fill="both", expand=True)
        label(self.body, "Bought = the share of players who bought it. Win rate when bought favours expensive late items "
                         "(only longer games get to buy them), so compare items of the same tier. Last 14 days: daily win rate, "
                         "every line on the same scale (hover for each day). Change: the last 7 days against the 7 before; "
                         "steady = within chance, or under half a point. Bought at = average game time of purchase.",
              size=9, color="dim", justify="left", wraplength=1080).pack(anchor="w", pady=(8, 0))
        self.app.set_status(f"{len(rows)} items")


class HeroPage(Page):
    nav = "heroes"

    def build(self, hero: str, mode: str = "Normal", band: str = "All ranks", view: str = "Stats"):
        band = band_for(mode, band)
        self.hero, self.mode, self.band, self.view = hero, mode, band, view
        self.message(f"Loading {hero}...")

        def work():
            names = self.app.hero_names_by_id()
            hero_id = next(i for i, n in names.items() if n == hero)
            cached_tiers = self.app.cache.get(("tiers", mode, band))
            cached_trends = self.app.cache.get(("trends", mode, band))
            tiers, breakdown, by_rank, trends, _ = deadlock_api.parallel(
                lambda: cached_tiers or hero_tier_list(names, API_GAME_MODES[mode], BANDS[band]),
                lambda: hero_breakdown(hero_id, names, API_GAME_MODES[mode], BANDS[band]),
                # Win rate at each rank: normal matches only (Street Brawl isn't ranked)
                lambda: hero_rank_curve(hero_id) if mode == "Normal" else [],
                lambda: cached_trends or hero_trends(names, API_GAME_MODES[mode], BANDS[band]),
                lambda: assets.load(images.hero_card_url(hero), assets.PORTRAIT_MAX_SIDE),
                allow_failures=True)
            breakdown = breakdown or {"toughest": None, "best": None, "items": None}
            breakdown["by_rank"] = by_rank
            try:
                guide = hero_guide(hero, breakdown["items"])
            except (OSError, ValueError) as e:
                logger.warning(f"No guide for {hero} ({e})")
                guide = None
            deadlock_api.parallel(
                lambda: assets.load_many((item.get("image") for item in breakdown["items"] or []), assets.ITEM_MAX_SIDE),
                lambda: assets.load_many(a["image"] for a in (guide or {}).get("abilities", [])))
            return tiers, breakdown, trends, guide
        self.app.run_task(work, lambda result: self.show(*result))

    def show(self, tiers, b, trends, guide):
        if tiers:
            self.app.cache[("tiers", self.mode, self.band)] = tiers
        if trends:
            self.app.cache[("trends", self.mode, self.band)] = trends
        self.clear(self.frame)
        stats = next((r for r in tiers or [] if r["hero"] == self.hero), None)

        outer, header = card(self.frame, padding=16)
        outer.pack(fill="x")
        portrait = images.hero_card(self.hero, 120)
        if portrait:
            tk.Label(header, image=portrait, bg=COLORS["card"]).pack(side="left", padx=(0, 18))
        info = tk.Frame(header, bg=COLORS["card"])
        info.pack(side="left", fill="y")
        label(info, self.hero, size=24, heading=True, bg="card", color=images.readable_on_dark(images.hero_color(self.hero))).pack(anchor="w")
        label(info, f"{self.mode} · {self.band}", color="dim", bg="card").pack(anchor="w", pady=(0, 10))
        chips = tk.Frame(info, bg=COLORS["card"])
        chips.pack(anchor="w")
        if stats:
            for text in (f"#{tiers.index(stats) + 1} by win rate",  # tiers are sorted by win rate
                         f"{stats['win_rate']:.1%} win rate", f"{stats['pick_rate']:.0%} pick rate",
                         f"{stats['games']:,} games", f"{stats['kda']:.2f} KDA",
                         *([f"{stats['ban_share']:.1%} of bans"] if stats.get("ban_share") else [])):
                pill(chips, text, COLORS["button"], size=10, text_color=COLORS["text"]).pack(side="left", padx=(0, 6))
        controls = tk.Frame(header, bg=COLORS["card"])
        controls.pack(side="right", anchor="n")
        segmented(controls, MODES, self.mode, lambda m: self.app.open_hero(self.hero, m, self.band, push=False)).pack(anchor="e")
        rank_dropdown(controls, self.mode, self.band, lambda band: self.app.open_hero(self.hero, self.mode, band, push=False)).pack(anchor="e", pady=(8, 0))
        trend = trends["heroes"].get(self.hero) if trends else None
        if trend and len(trend["weeks"]) >= 2:
            chart = tk.Frame(header, bg=COLORS["card"])
            chart.pack(side="left", fill="both", expand=True, padx=(28, 20))
            label(chart, "WIN RATE · LAST 12 WEEKS", size=9, color="dim", bold=True, bg="card").pack(anchor="w")
            change = change_text(trend)
            change_label = label(chart, change + (" pts vs 2 months ago" if change[0] in "▲▼" else ""), size=9,
                                 bold=True, color=trend_color(trend), bg="card")
            change_label.pack(anchor="w")
            if change_tip(trend):
                tooltip(change_label, change_tip(trend))
            trend_chart(chart, self.hero, trend, len(trends["weeks"]), height=84).pack(fill="both", expand=True, pady=(2, 0))

        tabs = tk.Frame(self.frame, bg=COLORS["bg"])
        tabs.pack(fill="x", pady=(12, 0))
        segmented(tabs, ["Stats", "Guide"], self.view,
                  lambda view: self.app.open_hero(self.hero, self.mode, self.band, push=False, view=view)).pack(side="left")
        if self.view == "Guide":
            self.show_guide(guide, b)
        else:
            self.show_stats(b, stats)

    def show_guide(self, guide, b):
        """A simple overview: what kind of hero this is, what players build, and the four abilities."""
        if not guide:
            self.message("No guide for this hero yet: it isn't in the game's hero data.")
            return
        color = images.hero_color(self.hero)
        top = tk.Frame(self.frame, bg=COLORS["bg"])
        top.pack(fill="x", pady=(10, 0))
        top.columnconfigure(0, weight=3, uniform="guide")
        top.columnconfigure(1, weight=2, uniform="guide")
        play_outer, play = section(top, "Playstyle")
        play_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        label(play, guide["summary"], size=11, bg="card", justify="left", wraplength=620).pack(anchor="w")
        facts = tk.Frame(play, bg=COLORS["card"])
        facts.pack(anchor="w", pady=(12, 0))
        for n, (name, value) in enumerate(guide["facts"]):
            row, column = divmod(n, 3)
            label(facts, name.upper(), size=8, color="dim", bold=True, bg="card").grid(row=row * 2, column=column, sticky="w", padx=(0, 34))
            label(facts, value, size=10, bg="card").grid(row=row * 2 + 1, column=column, sticky="w", padx=(0, 34), pady=(0, 8))

        build_outer, build = section(top, "What players build")
        build_outer.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        if guide["build"]:
            bar = tk.Canvas(build, height=14, bg=COLORS["card"], highlightthickness=0)
            bar.pack(fill="x")

            def draw_split(event=None):
                bar.delete("all")
                x, width = 0.0, bar.winfo_width()
                for slot in ("weapon", "vitality", "spirit"):
                    share = guide["build"].get(slot, 0)
                    if share:
                        bar.create_rectangle(x, 0, x + share * width, 14, fill=ITEM_SLOT_COLORS[slot], outline=COLORS["card"])
                        x += share * width
            bar.bind("<Configure>", draw_split)
            legend = tk.Frame(build, bg=COLORS["card"])
            legend.pack(anchor="w", pady=(6, 10))
            for slot in ("weapon", "vitality", "spirit"):
                if guide["build"].get(slot):
                    label(legend, f"{slot.title()} {guide['build'][slot]:.0%}", size=9, bold=True, bg="card",
                          color=ITEM_SLOT_COLORS[slot]).pack(side="left", padx=(0, 12))
        if b.get("items"):
            label(build, "Most bought (hover for details)", size=9, color="dim", bg="card").pack(anchor="w", pady=(0, 6))
            shelf = tk.Frame(build, bg=COLORS["card"])
            shelf.pack(anchor="w")
            for n, item in enumerate(b["items"][:10]):
                tile = tk.Label(shelf, image=images.item_icon(item, 30), bg=COLORS["card"])
                tile.grid(row=n // 5, column=n % 5, padx=(0, 6), pady=(0, 6))
                tooltip(tile, item_tooltip_text(item, None, self.hero))

        abilities_outer, abilities = section(self.frame, "Abilities")
        abilities_outer.pack(fill="x", pady=(12, 0))
        grid = tk.Frame(abilities, bg=COLORS["card"])
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1, uniform="abilities")
        for n, a in enumerate(guide["abilities"]):
            cell = tk.Frame(grid, bg=COLORS["card"])
            cell.grid(row=n // 2, column=n % 2, sticky="nsew", padx=(0, 20) if n % 2 == 0 else (0, 0), pady=(0, 14))
            tk.Label(cell, image=images.ability_icon(a["image"], 46, color), bg=COLORS["card"]).pack(side="left", anchor="n", padx=(0, 12))
            text = tk.Frame(cell, bg=COLORS["card"])
            text.pack(side="left", fill="x", expand=True)
            head = tk.Frame(text, bg=COLORS["card"])
            head.pack(anchor="w")
            label(head, f"{n + 1}  {a['name']}", size=11, bold=True, bg="card",
                  color=images.readable_on_dark(color)).pack(side="left")
            chips = (["ULTIMATE"] if n == 3 else []) + ([f"{a['cooldown']:.0f}s cooldown"] if a["cooldown"] else []) \
                + ([f"{a['charges']:.0f} charges"] if a["charges"] and a["charges"] > 1 else [])
            for chip in chips:
                pill(head, chip, COLORS["button"], size=8, text_color=COLORS["text"]).pack(side="left", padx=(8, 0))
            label(text, a["text"], size=9, color="dim", bg="card", justify="left", wraplength=440).pack(anchor="w", pady=(2, 0))
        label(self.frame, "Built from the game's own hero and ability data and what players buy; nothing here is written by hand.",
              size=9, color="faint").pack(anchor="w", pady=(8, 0))
        self.app.set_status(f"{self.hero} · guide")

    def show_stats(self, b, stats):
        columns = tk.Frame(self.frame, bg=COLORS["bg"])
        columns.pack(fill="x", pady=(10, 0))  # not expand: the rank chart sits right below, not at the bottom
        for c in range(3):
            columns.columnconfigure(c, weight=1, uniform="hero")
        for c, (title, matchups) in enumerate((("Best matchups", b["best"]), ("Toughest matchups", b["toughest"]))):
            box_outer, box = card(columns, padding=14)
            box_outer.grid(row=0, column=c, sticky="nsew", padx=(0, 8) if c == 0 else 8)
            label(box, title.upper(), size=9, color="dim", bold=True, bg="card").pack(anchor="w")
            if matchups is None:
                self.unavailable(box, "matchups")
                continue
            label(box, f"vs its {b['average_win_rate']:.1%} average", size=9, color="faint", bg="card").pack(anchor="w", pady=(0, 8))
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
        if b["items"] is None:
            self.unavailable(items, "items")
        else:
            label(items, "win rates run high for expensive late items", size=9, color="faint", bg="card").pack(anchor="w", pady=(0, 8))
        for item in b["items"] or []:
            row = tk.Frame(items, bg=COLORS["card"])
            row.pack(fill="x", pady=2)
            tk.Label(row, image=images.item_icon(item, 22), bg=COLORS["card"]).pack(side="left", padx=(0, 9))
            label(row, item["name"], bg="card").pack(side="left")
            label(row, f"{item['win_rate']:.0%}", bg="card", color="dim").pack(side="right")
            tooltip(row, item_tooltip_text(item, stats["games"] if stats else None, self.hero))
        if b["items"]:
            legend = tk.Frame(items, bg=COLORS["card"])
            legend.pack(anchor="w", pady=(10, 0))
            for slot, color in ITEM_SLOT_COLORS.items():
                pill(legend, slot.title(), color, size=8).pack(side="left", padx=(0, 4))

        if b["by_rank"] is None and self.mode == "Normal":
            ranks_outer, ranks = card(self.frame, padding=12)
            ranks_outer.pack(fill="x", pady=(12, 0))
            label(ranks, "WIN RATE BY RANK", size=9, color="dim", bold=True, bg="card").pack(anchor="w")
            self.unavailable(ranks, "win rate by rank")
        if b.get("by_rank"):
            ranks_outer, ranks = card(self.frame, padding=12)
            ranks_outer.pack(fill="x", pady=(12, 0))
            label(ranks, "WIN RATE BY RANK", size=9, color="dim", bold=True, bg="card").pack(anchor="w")
            label(ranks, f"normal matches at each rank · faded bars have under {LOW_SAMPLE_GAMES:,} games",
                  size=9, color="faint", bg="card").pack(anchor="w")
            rank_bars(ranks, b["by_rank"]).pack(fill="x", pady=(6, 0))
        self.app.set_status(f"{self.hero} · click a matchup to open that hero")


class MatchPage(Page):
    """A finished match. Overview: result, your game, the lead chart and both scoreboards.
    Performance: how each player's stats compare with others on the same hero, with the stand-outs."""
    nav = None

    def build(self, match_id: int, view: str = "Overview"):
        self.match_id, self.view = match_id, view
        game = self.app.post_game
        just_ended = game is not None and (game.match_id or 0) == match_id
        if just_ended and game.screen and not game.ready:
            self.show_screen_review(game)  # the end screen's numbers, until the full data arrives
            return
        if just_ended and not game.done:
            self.show_waiting(game)  # the app checks for it every minute and redraws this page
            return
        self.message(f"Loading match {match_id}...")

        def work():
            review = match_review(match_id, self.app.hero_names_by_id(), get_me())
            tiers = deadlock_api.fetch_rank_tiers()
            review["team_ranks"] = [f"{tiers[b // 10]['name']} {b % 10}" if b and b // 10 in tiers else None
                                    for b in review["team_badges"]]
            me = review.get("me")
            if me:  # only your build is shown, so only its icons are needed
                items = deadlock_api.fetch_items()
                by_name = {item["name"]: item for item in items.values()}  # reviews saved before icons have no ids
                me["items"] = [dict(items.get(i.get("id")) or by_name.get(i["name"], {}), **i) for i in me["items"]]
                assets.load_many((i.get("image") for i in me["items"]), assets.ITEM_MAX_SIDE)
            return review, self.app.avatars.download(p.get("avatar_url") for p in review["players"])

        def failed(error):
            self.clear(self.frame)
            self.heading(f"Match {match_id}")
            self.message(str(error) + "\n\nOpen this match in game (its end screen, or from your match history) and the app "
                         "reads how everyone played straight from the screen." if isinstance(error, MatchUnavailable)
                         else f"Couldn't load this match ({error}).")
            self.app.set_status(f"Match {match_id} couldn't be loaded")
        self.app.run_task(work, lambda result: self.show(*result), failed)

    def switch_view(self, view: str):
        self.app.navigate(MatchPage, push=False, match_id=self.match_id, view=view)

    def show_screen_review(self, game):
        """Straight after a match: how everyone played, from the numbers on the end screen. The full
        review (build, net-worth chart, accuracy) replaces it once the API has the match."""
        r = self.review = game.screen
        me = r.get("me")
        self.my_team = me["team"] if me else 0
        self.view, self.score_slot = "Performance", None
        outer, header = card(self.frame, padding=14)
        outer.pack(fill="x")
        won = None if not me or r["winning_team"] is None else r["winning_team"] == me["team"]
        result, color = {True: ("VICTORY", "win"), False: ("DEFEAT", "loss"), None: ("MATCH OVER", "text")}[won]
        label(header, result, size=24, heading=True, color=color, bg="card").pack(side="left")
        if me:
            hero_label(header, me["hero"], "card", size=34, font_size=13).pack(side="left", padx=18)
        facts = [r["mode"]] + ([f"{r['minutes']:.0f} min"] if r["minutes"] else []) + ["read from the end-of-match screen"]
        label(header, " · ".join(facts), color="dim", bg="card").pack(side="left", padx=(6, 0))
        if self.match_id:
            label(header, f"match {self.match_id}", size=9, color="faint", bg="card").pack(side="right")
        if not game.match_id:
            note = "The match ID couldn't be read, so this is what the end screen shows."
        elif not game.done:
            note = (f"Your build, the net-worth chart and accuracy are added here when the full match data is ready "
                    f"(checked {game.checks} time{'s' if game.checks != 1 else ''}).")
        else:
            note = "The full match data isn't available yet: try Review this match later."
        label(self.frame, note, size=9, color="faint").pack(anchor="w", pady=(8, 0))
        self.body = tk.Frame(self.frame, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True)
        self.message("Comparing everyone's stats with other players on the same heroes...", self.body)
        self.load_ratings()
        self.app.set_status("Match over · how everyone played, from the end screen")

    def show_waiting(self, game):
        """Just after a match: its data isn't ready yet. Shows the lobby from the scoreboard meanwhile."""
        outer, header = card(self.frame, padding=14)
        outer.pack(fill="x")
        label(header, "MATCH OVER", size=24, heading=True, bg="card").pack(side="left")
        label(header, f"match {self.match_id}", size=9, color="faint", bg="card").pack(side="right")
        minutes = int((time.time() - game.ended_at) // 60)
        since = "just now" if minutes < 1 else f"{minutes} min ago"
        body_outer, body = card(self.frame, padding=16)
        body_outer.pack(fill="x", pady=(10, 0))
        label(body, "Getting the match data...", size=14, heading=True, bg="card").pack(anchor="w")
        label(body, "A match's full data appears a few minutes after it ends. This page fills in by itself "
                    "when it's ready, with how you played on your hero.", color="dim", bg="card",
              justify="left", wraplength=900).pack(anchor="w", pady=(4, 8))
        progress = f"Match ended {since} · checked {game.checks} time{'s' if game.checks != 1 else ''}"
        if game.last_error:
            progress += f" · {game.last_error}"
        label(body, progress, size=9, color="faint", bg="card", justify="left", wraplength=900).pack(anchor="w")

        lobby = self.app.lobby
        if lobby and lobby.get("match_id") == self.match_id and lobby["results"]:
            columns = tk.Frame(self.frame, bg=COLORS["bg"])
            columns.pack(fill="x", pady=(10, 0))
            for c, (team, title) in enumerate(TEAM_TITLES.items()):
                columns.columnconfigure(c, weight=1, uniform="team")
                team_outer, box = section(columns, title)
                team_outer.grid(row=0, column=c, sticky="nsew", padx=(0, 8) if c == 0 else (8, 0))
                for r in (r for r in lobby["results"] if r["team"] == team):
                    row = tk.Frame(box, bg=COLORS["card"])
                    row.pack(fill="x", pady=1)
                    hero_label(row, r["hero"], "card", size=22).pack(side="left")
                    label(row, r["player"][:24], color="dim", bg="card").pack(side="left", padx=10)
        self.app.set_status(f"Match {self.match_id} is over · waiting for its data")

    def show(self, r: Dict[str, Any], downloaded):
        self.app.avatars.store(downloaded)
        self.clear(self.frame)
        self.review = r
        me = r.get("me")
        self.my_team = me["team"] if me else 0

        # Header: result, hero, match facts
        outer, header = card(self.frame, padding=14)
        outer.pack(fill="x")
        if me:
            result, color = ("VICTORY", "win") if me["won"] else ("DEFEAT", "loss")
            label(header, result, size=24, heading=True, color=color, bg="card").pack(side="left")
            hero_label(header, me["hero"], "card", size=34, font_size=13).pack(side="left", padx=18)
        else:
            label(header, f"Match {r['match_id']}", size=22, heading=True, bg="card").pack(side="left")
        facts = f"{r['mode']} · {'ranked' if r['ranked'] else 'unranked'} · {r['minutes']:.0f} min · {when(r['start_time'])}"
        label(header, facts, color="dim", bg="card").pack(side="left", padx=(6, 0))
        label(header, f"match {r['match_id']}", size=9, color="faint", bg="card").pack(side="right")

        tabs = tk.Frame(self.frame, bg=COLORS["bg"])
        tabs.pack(fill="x", pady=(10, 0))
        segmented(tabs, ["Overview", "Performance"], self.view, self.switch_view).pack(side="left")
        self.body = tk.Frame(self.frame, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True)
        if self.view == "Performance":
            self.message("Comparing everyone's stats with other players on the same heroes...", self.body)
        else:
            self.show_overview(r)
        self.load_ratings()
        self.app.set_status(f"Match {r['match_id']} · click a player to open their page")

    def show_overview(self, r: Dict[str, Any]):
        me, my_team = r.get("me"), self.my_team
        self.score_slot = None

        # Your game: lobby place and comparison with your usual on this hero
        if me:
            yours_outer, yours = card(self.body, padding=12)
            yours_outer.pack(fill="x", pady=(10, 0))
            title = tk.Frame(yours, bg=COLORS["card"])
            title.pack(fill="x", pady=(0, 6))
            label(title, "YOUR GAME", size=9, color="dim", bold=True, bg="card").pack(side="left")
            self.score_slot = tk.Frame(title, bg=COLORS["card"])  # the performance score, once it's loaded
            self.score_slot.pack(side="right")
            tiles = tk.Frame(yours, bg=COLORS["card"])
            tiles.pack(fill="x")
            stats = [("K / D / A", f"{me['kills']} / {me['deaths']} / {me['assists']}", "kda", None)]
            stats += [(name, f"{me[key]:,}", key, r["vs_usual"].get(key)) for name, key, _ in REVIEW_STATS]
            players = len(r["players"])
            for c, (name, value, key, vs) in enumerate(stats):
                tiles.columnconfigure(c, weight=1, uniform="tiles")
                tile = tk.Frame(tiles, bg=COLORS["card"])
                tile.grid(row=0, column=c, sticky="nw")  # top-aligned: K/D/A has one line less
                label(tile, name.upper(), size=8, color="faint", bold=True, bg="card").pack(anchor="w")
                label(tile, value, size=16, heading=True, bg="card").pack(anchor="w")
                place = r["places"][key]
                label(tile, f"{ordinal(place)} of {players} in the lobby", size=9,
                      color="win" if place <= 3 else "dim", bg="card").pack(anchor="w")
                if vs is not None:
                    label(tile, f"{vs:+.0%} vs your {me['hero']} average", size=9,
                          color="win" if vs >= 0 else "loss", bg="card").pack(anchor="w")
            if me["items"]:
                build = tk.Frame(yours, bg=COLORS["card"])
                build.pack(fill="x", pady=(10, 0))
                label(build, "Final build", size=9, color="dim", bold=True, bg="card").pack(side="left", padx=(0, 8))
                for item in me["items"]:
                    item_tile(build, item, "card").pack(side="left", padx=(0, 4))

        # Net worth lead over the match, from your team's side
        if r["networth_lead"]:
            chart_outer, chart = card(self.body, padding=10)
            chart_outer.pack(fill="x", pady=(10, 0))
            sign = 1 if my_team == 0 else -1
            lead = [(minute, sign * diff) for minute, diff in r["networth_lead"]]
            final = lead[-1][1]
            title = "NET WORTH LEAD" + (" · your team" if me else " · team 1")
            label(chart, f"{title}   (final {final:+,.0f})", size=9, color="dim", bold=True, bg="card").pack(anchor="w")
            lead_chart(chart, lead).pack(fill="x", pady=(4, 0))

        # Both scoreboards
        columns = tk.Frame(self.body, bg=COLORS["bg"])
        columns.pack(fill="both", expand=True, pady=(10, 0))
        for c, team in enumerate((my_team, 1 - my_team)):
            columns.columnconfigure(c, weight=1, uniform="teams")
            frame = tk.Frame(columns, bg=COLORS["bg"])
            frame.grid(row=0, column=c, sticky="nsew", padx=(0, 8) if c == 0 else (8, 0))
            team_players = sorted((p for p in r["players"] if p["team"] == team), key=lambda p: -p["net_worth"])
            won = team == r["winning_team"]
            title = ("Your team" if me and team == my_team else "Enemy team" if me else f"Team {team + 1}")
            rank = f" · avg rank {r['team_ranks'][team]}" if r["team_ranks"][team] else ""
            head = tk.Frame(frame, bg=COLORS["bg"])
            head.pack(fill="x", pady=(0, 4))
            label(head, title, size=13, heading=True, color="friendly" if c == 0 else "enemy").pack(side="left")
            label(head, f"{'won' if won else 'lost'}{rank}", color="win" if won else "loss").pack(side="left", padx=10)
            for p in team_players:
                p["kda_text"] = f"{p['kills']}/{p['deaths']}/{p['assists']}"
            data_table(frame, [
                ("name", "Player", 120, lambda v: v[:16], "w"),
                ("kda_text", "K/D/A", 70, str, "center"),
                ("net_worth", "Souls", 60, lambda v: f"{v / 1000:.1f}k", "center"),
                ("damage", "Damage", 60, lambda v: f"{v / 1000:.1f}k", "center"),
                ("healing", "Healing", 60, lambda v: f"{v / 1000:.1f}k", "center"),
            ], team_players, height=6, hero_key="hero",
                tag=lambda p: "me" if me and p["account_id"] == me["account_id"] else "",
                on_click=lambda p: self.app.open_player(p["account_id"]))

    # --- performance
    def load_ratings(self):
        """Rate everyone after the page is shown: one request per hero (~3 s the first time)."""
        self.ratings_key = ("ratings", self.match_id, self.review.get("source", "api"))  # screen and API differ
        cached = self.app.cache.get(self.ratings_key)
        if cached:
            self.ratings_ready(cached)
            return
        names = self.app.hero_names_by_id()
        self.app.run_task(lambda: rate_match(self.review, names), self.ratings_ready, self.ratings_failed)

    def ratings_failed(self, error):
        if self.view == "Performance":
            self.clear(self.body)
            self.unavailable(self.body, "the performance ratings", bg="bg")

    def ratings_ready(self, rated: Dict[str, Any]):
        self.app.cache[self.ratings_key] = rated
        self.rated = rated
        players = self.review["players"]
        me = self.review.get("me")
        mine = next((i for i, p in enumerate(players) if me and p["account_id"] == me["account_id"]), None)
        if self.view == "Performance":
            self.show_performance(mine)
        elif self.score_slot is not None and mine is not None and rated["ratings"][mine]:
            rating = rated["ratings"][mine]
            text = f"{rating['verdict'].upper()} · {rating['score']}"
            badge = pill(self.score_slot, text, score_color(rating["score"]), size=9)
            badge.pack(side="right")
            bind_click(badge, lambda: self.switch_view("Performance"))
            tooltip(badge, f"How your stats compare with other {players[mine]['hero']} players "
                           f"(50 = a typical game). Click for the details.")

    def show_performance(self, selected: int = None):
        players, ratings = self.review["players"], self.rated["ratings"]
        if selected is None or not ratings[selected]:  # not your match: start with the best-rated player
            rated = [i for i, rating in enumerate(ratings) if rating]
            if not rated:
                self.clear(self.body)
                self.unavailable(self.body, "the performance ratings", bg="bg")
                return
            selected = max(rated, key=lambda i: ratings[i]["score"] or 0)
        self.clear(self.body)
        grid = tk.Frame(self.body, bg=COLORS["bg"])
        grid.pack(fill="both", expand=True, pady=(10, 0))
        grid.columnconfigure(0, weight=3, uniform="perf")
        grid.columnconfigure(1, weight=2, uniform="perf")
        detail_outer, self.detail = card(grid, padding=16)
        detail_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        everyone_outer, everyone = card(grid, padding=14)
        everyone_outer.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        label(everyone, "EVERYONE'S GAME", size=9, color="dim", bold=True, bg="card").pack(anchor="w")
        label(everyone, "Each player's stats against others on the same hero: 50 is a typical game. "
                        "Click a player for their details.", size=9, color="faint", bg="card", justify="left",
              wraplength=380).pack(anchor="w", pady=(0, 6))
        me = self.review.get("me")
        for team in (self.my_team, 1 - self.my_team):
            title = ("Your team" if me and team == self.my_team else "Enemy team" if me else f"Team {team + 1}")
            head = tk.Frame(everyone, bg=COLORS["card"])
            head.pack(fill="x", pady=(8, 2))
            label(head, title, size=11, heading=True, bg="card",
                  color="friendly" if team == self.my_team else "enemy").pack(side="left")
            if self.review["winning_team"] is not None:
                won = team == self.review["winning_team"]
                label(head, "won" if won else "lost", bg="card", color="win" if won else "loss").pack(side="left", padx=8)
            members = sorted((i for i, p in enumerate(players) if p["team"] == team),
                             key=lambda i: -((ratings[i] or {}).get("score") or -1))
            for i in members:
                row = tk.Frame(everyone, bg=COLORS["selected"] if i == selected else COLORS["card"])
                row.pack(fill="x", pady=1)
                rating = ratings[i]
                score = rating["score"] if rating else None
                pill(row, f"{score}" if score is not None else "-", score_color(score), size=9).pack(side="right", padx=6, pady=3)
                hero_label(row, players[i]["hero"], row["bg"], size=22).pack(side="left", padx=(4, 8), pady=2)
                label(row, players[i]["name"][:20], color="dim", bg=row["bg"]).pack(side="left")
                bind_click(row, lambda i=i: self.show_performance(i))
        self.show_rating(selected)

    def show_rating(self, i: int):
        p, rating = self.review["players"][i], self.rated["ratings"][i]
        me = self.review.get("me")
        box = self.detail
        hero = p["hero"]
        title = tk.Frame(box, bg=COLORS["card"])
        title.pack(fill="x")
        hero_label(title, hero, "card", size=40, font_size=15).pack(side="left")
        who = "Your game" if me and p["account_id"] == me["account_id"] else p["name"][:24]
        label(title, who, color="dim", bg="card").pack(side="left", padx=12, pady=(8, 0))
        score = tk.Frame(title, bg=COLORS["card"])
        score.pack(side="right")
        label(score, f"{rating['score']}", size=28, heading=True, bg="card",
              color=score_color(rating["score"])).pack(side="right")
        label(score, rating["verdict"], size=13, bold=True, bg="card",
              color=score_color(rating["score"])).pack(side="right", padx=10, pady=(10, 0))
        low, high = self.rated["window"]
        band = "all ranks" if self.rated["band"] == "All ranks" else self.rated["band"]
        length = f"in {low}-{high} minute matches" if low is not None else "in matches of any length"
        label(box, f"Compared with {hero} players at {band}, {length}, "
                   f"over the last 30 days. The score averages souls, damage, KDA, deaths and objective damage.",
              size=9, color="faint", bg="card", justify="left", wraplength=620).pack(anchor="w", pady=(6, 10))

        stand_outs = [("▲", r, "win") for r in rating["strengths"]] + [("▼", r, "loss") for r in rating["weaknesses"]]
        for arrow, r, color in stand_outs:
            label(box, f"{arrow}  {r['label']}: {r['text']}, {compared_text(r)} of {hero} players", size=11,
                  bold=True, color=color, bg="card").pack(anchor="w", pady=1)
        if not stand_outs:
            label(box, f"Nothing stood out: a typical game on {hero}.", size=11, color="dim", bg="card").pack(anchor="w")

        table = tk.Frame(box, bg=COLORS["card"])
        table.pack(fill="x", pady=(14, 0))
        for c, (text, weight) in enumerate((("STAT", 3), ("THIS GAME", 1), ("AGAINST OTHER " + hero.upper() + " PLAYERS", 3),
                                            ("TYPICAL", 1))):
            table.columnconfigure(c, weight=weight)
            label(table, text, size=8, color="faint", bold=True, bg="card").grid(row=0, column=c, sticky="w", pady=(0, 4))
        for n, r in enumerate(rating["rows"], start=1):
            label(table, r["label"], bg="card").grid(row=n, column=0, sticky="w", pady=3)
            label(table, r["text"], bold=True, bg="card").grid(row=n, column=1, sticky="w", padx=(0, 10))
            cell = tk.Frame(table, bg=COLORS["card"])
            cell.grid(row=n, column=2, sticky="w")
            percentile_bar(cell, r["good"], r["percentile"], width=150).pack(side="left")
            label(cell, compared_text(r), size=9, bg="card",
                  color=score_color(r["good"]) if r["good"] is not None else "dim").pack(side="left", padx=8)
            label(table, r["median_text"], color="dim", bg="card").grid(row=n, column=3, sticky="w")


def rank_bars(parent, by_rank, height: int = 120) -> tk.Canvas:
    """One bar per rank, up from a 50% line when above it (green) and down when below (red)."""
    canvas = tk.Canvas(parent, height=height, bg=COLORS["card"], highlightthickness=0)

    def draw(event=None):
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < 50:
            return
        middle = (height - 30) / 2 + 4               # leave room below for the rank names
        biggest = max(abs(r["win_rate"] - 0.5) for r in by_rank) or 0.01
        slot = width / len(by_rank)
        canvas.create_line(0, middle, width, middle, fill=COLORS["faint"], dash=(2, 3))
        canvas.create_text(2, middle - 2, text="50%", anchor="sw", fill=COLORS["faint"], font=("Segoe UI", 8))
        for n, r in enumerate(by_rank):
            x = slot * n + slot / 2
            bar = (r["win_rate"] - 0.5) / biggest * (middle - 16)
            color = COLORS["win"] if bar >= 0 else COLORS["loss"]
            faded = r["games"] < LOW_SAMPLE_GAMES   # few games: less reliable, so drawn lighter
            canvas.create_rectangle(x - slot * 0.3, middle, x + slot * 0.3, middle - bar, fill=color, outline="",
                                    stipple="gray50" if faded else "")
            canvas.create_text(x, middle - bar - (8 if bar >= 0 else -8), text=f"{r['win_rate']:.1%}",
                               fill=COLORS["text"], font=("Segoe UI", 8, "bold"))
            canvas.create_text(x, height - 18, text=r["name"], fill=COLORS["dim"], font=("Segoe UI", 8))
            canvas.create_text(x, height - 6, text=f"{r['games']:,}", fill=COLORS["faint"], font=("Segoe UI", 7))
    canvas.bind("<Configure>", draw)  # redraw on resize
    return canvas


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def lead_chart(parent, lead, height: int = 70) -> tk.Canvas:
    """A small area chart of a (minute, lead) series: above the line = ahead, below = behind."""
    canvas = tk.Canvas(parent, height=height, bg=COLORS["card"], highlightthickness=0)

    def draw(event=None):
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < 10:
            return
        end_minute = lead[-1][0] or 1
        biggest = max(abs(v) for _, v in lead) or 1
        middle = height / 2

        def point(minute, value):
            return minute / end_minute * (width - 4) + 2, middle - value / biggest * (middle - 4)
        points = [point(0, 0)] + [point(m, v) for m, v in lead]
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            # each segment filled down to the middle line, green while ahead and red while behind
            color = COLORS["win"] if (y1 + y2) / 2 <= middle else COLORS["loss"]
            canvas.create_polygon(x1, middle, x1, y1, x2, y2, x2, middle, fill=color, outline="", stipple="gray50")
            canvas.create_line(x1, y1, x2, y2, fill=color, width=2)
        canvas.create_line(0, middle, width, middle, fill=COLORS["faint"], dash=(2, 3))
        canvas.create_text(4, 2, text=f"+{biggest / 1000:.0f}k", anchor="nw", fill=COLORS["faint"], font=("Segoe UI", 8))
        canvas.create_text(4, height - 2, text=f"-{biggest / 1000:.0f}k", anchor="sw", fill=COLORS["faint"], font=("Segoe UI", 8))
        canvas.create_text(width - 4, height - 2, text=f"{end_minute:.0f} min", anchor="se", fill=COLORS["faint"], font=("Segoe UI", 8))
    canvas.bind("<Configure>", draw)  # redraw whenever the window (and so the canvas) is resized
    return canvas


class PlayerPage(Page):
    nav = None

    def build(self, account_id: int, mode: str = "All", nav: str = None):
        self.nav = nav
        self.account_id, self.mode = account_id, mode
        self.message("Loading player...")

        def work():
            profile = player_profile(account_id, self.app.hero_names_by_id(), mode)
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
        segmented(row, list(MATCH_TYPES), self.mode, self.switch_mode).pack(side="left", padx=14)
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
            kind = "" if self.mode == "All" else self.mode.lower() + " "
            self.message(f"No recorded {kind}matches.", left)

        if p["recent"]:
            for m in p["recent"]:
                m["kda_text"] = f"{m['kills']}/{m['deaths']}/{m['assists']}"
                m["result"] = "Win" if m["won"] else "Loss"
            data_table(right, [
                ("start_time", "When", 70, when, "center"),
                ("result", "Result", 55, str, "center"),
                ("kda_text", "K/D/A", 70, str, "center"),
                ("type", "Type", 90, str, "center"),
            ], list(p["recent"]), height=14, tag=lambda m: "win" if m["won"] else "loss", hero_key="hero",
                on_click=lambda m: self.app.open_match(m["match_id"]))
        else:
            self.message("No recorded matches.", right)
        self.app.set_status(f"{p['name']} · click a match for its post-game review")

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


class SettingsPage(Page):
    """What happens when the scoreboard opens in game, and what the lobby cards show."""
    nav = "settings"
    WHEN_OPEN = [
        ("pop_up", "Bring the app to the front",
         "Puts the window above others without taking keyboard focus from the game. Best with the app "
         "on a second monitor: on the game's monitor it covers the game."),
        ("sound", "Play a sound when the lobby is ready", ""),
        ("reshow_same_lobby", "Show the lobby again when nothing changed",
         "Reopening the scoreboard in the same lobby (mid-match, or in Street Brawl, where heroes are "
         "known from the start) jumps back to the Lobby page, without looking anyone up again."),
        ("post_game_review", "Open the match review when a match ends",
         "When the end-of-match scoreboard appears, jumps to that match's review and fills it in once the "
         "match data is ready (usually a few minutes), with how you played on your hero."),
    ]
    CARD_PARTS = [
        ("show_rank", "Rank", ""),
        ("show_hero_stats", "Stats on their current hero", "Games, win rate, KDA and damage on the hero they're playing."),
        ("show_badges", "Badges", "ONE-TRICK, HIGH WR, NEW ON HERO... ID UNSURE and NAME FIXED always show."),
        ("show_most_played", "Most-played heroes", ""),
        ("show_matchup", "Your matchup", "Your hero against each enemy hero, and popular items against them."),
    ]

    def build(self):
        self.heading("Settings", "saved on this PC · changes apply right away")
        self.variables = []  # tkinter forgets variables nobody holds
        grid = tk.Frame(self.frame, bg=COLORS["bg"])
        grid.pack(fill="x")
        grid.columnconfigure((0, 1), weight=1, uniform="settings")
        when_outer, when = section(grid, "When the scoreboard opens in game")
        when_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        cards_outer, cards = section(grid, "Lobby cards show")
        cards_outer.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.option(when, "Watch for the scoreboard (auto-detect)",
                    f"Same as the switch at the top. Off: press {HOTKEY_TEXT} in game to capture instead.",
                    self.app.auto_detect, self.app.apply_auto_detect)
        prefs = get_preferences()
        for parent, options in ((when, self.WHEN_OPEN), (cards, self.CARD_PARTS)):
            for key, title, note in options:
                variable = tk.BooleanVar(value=prefs[key])
                self.variables.append(variable)
                self.option(parent, title, note, variable, lambda k=key, v=variable: set_preference(k, v.get()))
        self.app.set_status("Settings")

    def option(self, parent, title: str, note: str, variable, command):
        row = tk.Frame(parent, bg=COLORS["card"])
        row.pack(fill="x", pady=(0, 12))
        switch(row, title, variable, command).pack(anchor="w")
        if note:
            label(row, note, size=9, color="dim", bg="card", justify="left", wraplength=430).pack(anchor="w", padx=(40, 0))


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
