"""Reusable pieces of the interface: avatars, player cards, sortable tables, rank pills, matchup strip."""

import logging
import tkinter as tk
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk

from report import badge_labels, hero_stats_text, items_text, matchup_kind, matchup_text, most_played_text
from ui import images
from ui.theme import BADGE_COLORS, COLORS, FONT, MATCHUP_COLORS, card, label, pill

logger = logging.getLogger(__name__)


class AvatarCache:
    """Steam avatar bytes: downloaded on worker threads, stored and drawn on the main thread."""

    def __init__(self):
        self.data: Dict[str, bytes] = {}

    @staticmethod
    def download(urls) -> Dict[str, bytes]:
        """Worker thread: fetch avatars in parallel. Images that fail are just skipped."""
        def fetch(url):
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    return url, response.read()
            except OSError:
                return url, None
        wanted = {u for u in urls if u}
        with ThreadPoolExecutor(max_workers=6) as pool:
            return {url: data for url, data in pool.map(fetch, wanted) if data}

    def store(self, downloaded: Dict[str, bytes]) -> None:
        self.data.update(downloaded)

    def get(self, url: Optional[str], size: int, ring: Optional[str] = None):
        """A circular avatar (grey circle if missing), with an optional coloured ring."""
        return images.avatar(self.data.get(url), size, ring)


def bind_click(widget: tk.Widget, command: Callable[[], None]) -> None:
    """Make a widget and everything inside it clickable, with a hand cursor."""
    widget.bind("<Button-1>", lambda event: command(), add="+")
    try:
        widget.configure(cursor="hand2")
    except (tk.TclError, ValueError):
        pass
    for child in widget.winfo_children():
        bind_click(child, command)


def rank_pill(parent, rank: Optional[Dict[str, Any]], size: int = 9) -> Optional[ctk.CTkLabel]:
    """The rank's emblem and name, e.g. [emblem] Emissary 2, in a readable version of its colour."""
    if not rank:
        return None
    tier, subrank = divmod(rank.get("badge", 0), 10)
    emblem = images.rank_emblem(tier, subrank, 18)
    text_color = images.readable_on_dark(rank["color"]) if tier else COLORS["dim"]
    return pill(parent, rank["name"], COLORS["surface"], size=size, image=emblem, text_color=text_color)


def hero_label(parent, hero: str, bg: str, size: int = 22, font_size: int = 10, color: str = None) -> tk.Label:
    """The hero's badge followed by their name."""
    badge = images.hero_badge(hero, size)
    return tk.Label(parent, text=f"  {hero}" if badge else hero, image=badge, compound="left", bg=COLORS.get(bg, bg),
                    fg=color or images.readable_on_dark(images.hero_color(hero)), font=(FONT, font_size, "bold"))


def player_card(parent, r: Dict[str, Any], accent: str, avatars: AvatarCache,
                on_open: Optional[Callable[[], None]] = None, on_search: Optional[Callable[[str], None]] = None,
                party: Optional[Tuple[str, str]] = None) -> ctk.CTkFrame:
    """One player: avatar, name, rank, stats on their hero, badges and most-played heroes.
    Clicking the card opens the player's page (when they were found)."""
    clickable = r["status"] == "found" and on_open is not None
    outer, body = card(parent, padding=8, hoverable=clickable)
    outer.pack(fill="x", pady=2)
    party_color, party_label = party or (None, None)

    # Circular avatar; the ring shows the party (or the team)
    tk.Label(body, image=avatars.get(r.get("avatar_url"), 46, ring=party_color or accent), bg=COLORS["card"]).pack(
        side="left", anchor="n", padx=(0, 12))
    info = tk.Frame(body, bg=COLORS["card"])
    info.pack(side="left", fill="both", expand=True)
    info.columnconfigure(0, weight=1)

    label(info, r["player"], size=11, bold=True, bg="card", heading=True, anchor="w").grid(row=0, column=0, sticky="w")
    rp = rank_pill(info, r.get("rank"))
    if rp:
        rp.grid(row=0, column=1, sticky="e")

    line = tk.Frame(info, bg=COLORS["card"])
    line.grid(row=1, column=0, columnspan=2, sticky="w", pady=(1, 0))
    if r.get("hero"):  # search results have no current hero
        hero_label(line, r["hero"], "card", size=18).pack(side="left", padx=(0, 10))
    label(line, hero_stats_text(r), bg="card", color="text").pack(side="left")

    badges = badge_labels(r)
    if party_label:
        badges.insert(0, (party_label, "party"))
    if badges:
        row = tk.Frame(info, bg=COLORS["card"])
        row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(3, 0))
        for text, kind in badges:
            pill(row, text, party_color if kind == "party" else BADGE_COLORS[kind]).pack(side="left", padx=(0, 4))

    details = most_played_text(r)
    if details:
        label(info, details, size=9, color="dim", bg="card", anchor="w").grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

    if clickable:
        outer.after_idle(lambda: bind_click(outer, on_open))
    elif r["status"] == "not found" and on_search:
        query = r.get("corrected_from") or r["player"]
        link = label(info, "Search similar names  >", size=9, color="link", bg="card", cursor="hand2")
        link.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        link.bind("<Button-1>", lambda event: on_search(query))
    return outer


Column = Tuple[str, str, int, Callable[[Any], str], str]  # (key, title, width, format, anchor)


def data_table(parent, columns: Sequence[Column], rows: List[Dict[str, Any]], height: int = 15,
               tag: Optional[Callable[[Dict[str, Any]], str]] = None, hero_key: Optional[str] = None,
               hero_title: str = "Hero", on_click: Optional[Callable[[Dict[str, Any]], None]] = None) -> ttk.Treeview:
    """A table with a scrollbar inside a rounded card. Click a column heading to sort by it; click
    again to reverse. hero_key: show that column first, as the hero's icon and name.
    tag(row) can return "win"/"loss" to colour a row. on_click(row) runs when a row is clicked."""
    outer, inner = card(parent, padding=6)
    outer.pack(fill="both", expand=True)
    tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show="tree headings" if hero_key else "headings",
                        height=height, selectmode="browse")
    scrollbar = ttk.Scrollbar(inner, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    tree.tag_configure("win", foreground=COLORS["win"])
    tree.tag_configure("loss", foreground=COLORS["loss"])
    formats = {key: fmt for key, _, _, fmt, _ in columns}
    sort_state = {"key": None, "reverse": False}
    row_of = {}  # tree item id -> row, so a click knows which row it was

    def fill():
        tree.delete(*tree.get_children())
        row_of.clear()
        for r in rows:
            options = {"values": [formats[key](r.get(key)) for key in formats], "tags": (tag(r),) if tag else ()}
            if hero_key:
                badge = images.hero_badge(r[hero_key], 24)
                options.update(text=f"  {r[hero_key]}", image=badge or "")
            row_of[tree.insert("", "end", **options)] = r

    def sort_by(key):
        # First click on a number column sorts biggest first; text columns A-Z
        if sort_state["key"] == key:
            sort_state["reverse"] = not sort_state["reverse"]
        else:
            sort_state["key"] = key
            sort_state["reverse"] = any(isinstance(r.get(key), (int, float)) for r in rows)
        rows.sort(key=lambda r: (r.get(key) is None, r.get(key) if r.get(key) is not None else 0),
                  reverse=sort_state["reverse"])
        fill()

    if hero_key:
        tree.heading("#0", text=hero_title, anchor="w", command=lambda: sort_by(hero_key))
        tree.column("#0", width=170, anchor="w", stretch=True)
    for key, title, width, _, anchor in columns:
        tree.heading(key, text=title, command=lambda k=key: sort_by(k))
        tree.column(key, width=width, anchor=anchor, stretch=True)
    if on_click:
        tree.configure(cursor="hand2")
        tree.bind("<ButtonRelease-1>", lambda event: on_click(row_of[tree.identify_row(event.y)])
                  if tree.identify_row(event.y) in row_of else None)
    fill()
    return tree


def matchup_strip(parent, matchup: Dict[str, Any]) -> ctk.CTkFrame:
    """Your hero against each enemy hero (vs your hero's average), and popular items against them."""
    outer, inner = card(parent, padding=9, fill="surface")
    top = tk.Frame(inner, bg=COLORS["surface"])
    top.pack(fill="x")
    hero_label(top, matchup["hero"], "surface", size=26, font_size=11).pack(side="left")
    label(top, f"  YOUR MATCHUP · averages {matchup['average_win_rate']:.0%}   vs", color="dim", bg="surface").pack(side="left", padx=(0, 8))
    for m in matchup["matchups"]:  # toughest first
        pill(top, matchup_text(m), MATCHUP_COLORS[matchup_kind(m["vs_average"])], size=9,
             image=images.hero_badge(m["enemy_hero"], 18, kind="ctk")).pack(side="left", padx=(0, 5))
    if matchup["items"]:
        label(inner, items_text(matchup), size=9, color="dim", bg="surface", anchor="w").pack(fill="x", pady=(4, 0))
    return outer
