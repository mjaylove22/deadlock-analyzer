"""Reusable pieces of the interface: avatars, player cards, sortable tables, toggles, matchup strip."""

import io
import logging
import tkinter as tk
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageTk

from report import badge_labels, hero_stats_text, items_text, matchup_kind, matchup_text, most_played_text
from ui.theme import BADGE_COLORS, COLORS, FONT, MATCHUP_COLORS, label, pill

logger = logging.getLogger(__name__)


class AvatarCache:
    """Steam avatars, downloaded on worker threads and turned into tkinter images on the main thread.

    tkinter only displays an image while Python still holds a reference to it, so every image is
    kept here; an image held only by a local variable would silently vanish from the screen.
    """

    def __init__(self):
        self.images = {}        # (url, size) -> PhotoImage
        self.data = {}          # url -> downloaded bytes
        self.placeholders = {}  # size -> PhotoImage

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
        """Main thread: keep downloaded bytes so images of any size can be made from them."""
        self.data.update(downloaded)

    def get(self, url: Optional[str], size: int) -> ImageTk.PhotoImage:
        if url in self.data:
            key = (url, size)
            if key not in self.images:
                try:
                    image = Image.open(io.BytesIO(self.data[url])).convert("RGB").resize((size, size), Image.LANCZOS)
                    self.images[key] = ImageTk.PhotoImage(image)
                except Exception:
                    logger.warning(f"Could not read avatar {url}")
            if key in self.images:
                return self.images[key]
        if size not in self.placeholders:
            self.placeholders[size] = ImageTk.PhotoImage(Image.new("RGB", (size, size), COLORS["button"]))
        return self.placeholders[size]


def bind_click(widget: tk.Widget, command: Callable[[], None]) -> None:
    """Make a widget and everything inside it clickable, with a hand cursor."""
    widget.bind("<Button-1>", lambda event: command())
    widget.config(cursor="hand2")
    for child in widget.winfo_children():
        bind_click(child, command)


def player_card(parent, r: Dict[str, Any], accent: str, avatars: AvatarCache,
                on_open: Optional[Callable[[], None]] = None, on_search: Optional[Callable[[str], None]] = None,
                party: Optional[Tuple[str, str]] = None) -> tk.Frame:
    """One player: avatar, name, rank, stats on their hero, badges and most-played heroes.
    Clicking the card opens the player's page (when they were found)."""
    bg = COLORS["card"]
    card = tk.Frame(parent, bg=bg)
    card.pack(fill="x", pady=3)
    party_color, party_label = party or (None, None)
    tk.Frame(card, bg=party_color or accent, width=5).pack(side="left", fill="y")
    tk.Label(card, image=avatars.get(r.get("avatar_url"), 48), bg=bg).pack(side="left", anchor="n", padx=(10, 0), pady=8)
    body = tk.Frame(card, bg=bg, padx=12, pady=5)
    body.pack(side="left", fill="both", expand=True)
    body.columnconfigure(0, weight=1)

    label(body, r["player"], size=12, bold=True, bg="card", anchor="w").grid(row=0, column=0, sticky="w")
    if r.get("rank"):
        pill(body, r["rank"]["name"], r["rank"]["color"], size=9).grid(row=0, column=1, sticky="e")

    line = tk.Frame(body, bg=bg)
    line.grid(row=1, column=0, columnspan=2, sticky="w", pady=(1, 0))
    if r.get("hero"):  # search results have no current hero
        label(line, r["hero"] + "   ", color=accent, bold=True, bg="card").pack(side="left")
    label(line, hero_stats_text(r), bg="card").pack(side="left")

    badges = badge_labels(r)
    if party_label:
        badges.insert(0, (party_label, "party"))
    if badges:
        row = tk.Frame(body, bg=bg)
        row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        for text, kind in badges:
            pill(row, text, party_color if kind == "party" else BADGE_COLORS[kind]).pack(side="left", padx=(0, 4))

    details = most_played_text(r)
    if details:
        label(body, details, size=9, color="dim", bg="card", anchor="w").grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))

    if r["status"] == "found" and on_open:
        bind_click(card, on_open)
    elif r["status"] == "not found" and on_search:
        query = r.get("corrected_from") or r["player"]
        link = label(body, "Search similar names  >", size=9, color="link", bg="card", cursor="hand2")
        link.grid(row=4, column=0, columnspan=2, sticky="w", pady=(3, 0))
        link.bind("<Button-1>", lambda event: on_search(query))
    return card


Column = Tuple[str, str, int, Callable[[Any], str], str]  # (key, title, width, format, anchor)


def data_table(parent, columns: Sequence[Column], rows: List[Dict[str, Any]], height: int = 15,
               tag: Optional[Callable[[Dict[str, Any]], str]] = None) -> ttk.Treeview:
    """A table with a scrollbar. Click a column heading to sort by it; click again to reverse.
    tag(row) can return "win"/"loss" to colour a row."""
    frame = tk.Frame(parent, bg=COLORS["card"])
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height, selectmode="browse")
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    tree.tag_configure("win", foreground=COLORS["win"])
    tree.tag_configure("loss", foreground=COLORS["loss"])
    formats = {key: fmt for key, _, _, fmt, _ in columns}
    sort_state = {"key": None, "reverse": False}

    def fill():
        tree.delete(*tree.get_children())
        for r in rows:
            tree.insert("", "end", values=[formats[key](r.get(key)) for key in formats], tags=(tag(r),) if tag else ())

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

    for key, title, width, _, anchor in columns:
        tree.heading(key, text=title, command=lambda k=key: sort_by(k))
        tree.column(key, width=width, anchor=anchor, stretch=True)
    fill()
    return tree


def toggle(parent, options: Sequence[str], selected: str, on_change: Callable[[str], None], bg: str = "bg") -> tk.Frame:
    """A row of buttons where one is selected (e.g. Normal | Street Brawl)."""
    frame = tk.Frame(parent, bg=COLORS[bg])
    for option in options:
        chosen = option == selected
        tk.Button(frame, text=option, relief="flat", font=(FONT, 9, "bold" if chosen else "normal"), padx=10, pady=2,
                  bg=COLORS["friendly"] if chosen else COLORS["button"], fg="#0b0d10" if chosen else COLORS["text"],
                  activebackground=COLORS["selected"], cursor="hand2",
                  command=lambda o=option: on_change(o)).pack(side="left", padx=(0, 2))
    return frame


def matchup_strip(parent, matchup: Dict[str, Any]) -> tk.Frame:
    """Your hero against each enemy hero (vs your hero's average), and popular items against them."""
    strip = tk.Frame(parent, bg=COLORS["header"], padx=14, pady=8)
    top = tk.Frame(strip, bg=COLORS["header"])
    top.pack(fill="x")
    label(top, f"YOUR MATCHUP · {matchup['hero']}", size=11, color="friendly", bold=True, bg="header").pack(side="left")
    label(top, f"averages {matchup['average_win_rate']:.0%}   vs", color="dim", bg="header").pack(side="left", padx=(8, 6))
    for m in matchup["matchups"]:  # toughest first
        pill(top, matchup_text(m), MATCHUP_COLORS[matchup_kind(m["vs_average"])], size=9).pack(side="left", padx=(0, 4))
    if matchup["items"]:
        label(strip, items_text(matchup), size=9, color="dim", bg="header", anchor="w").pack(fill="x", pady=(4, 0))
    return strip
