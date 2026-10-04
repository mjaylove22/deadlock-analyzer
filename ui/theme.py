"""Colours, fonts and widget styling shared by every page."""

import tkinter as tk
from tkinter import ttk

FONT = "Segoe UI"

COLORS = {
    "bg": "#0f1115", "header": "#161a22", "card": "#1c212b", "card_hover": "#232a36", "text": "#e8eaed",
    "dim": "#8b93a1", "friendly": "#4fc3f7", "enemy": "#ef5350", "accent": "#9fa8da", "button": "#2a303c",
    "link": "#6fa8ff", "win": "#66bb6a", "loss": "#ef5350", "selected": "#2f3a4d",
}
BADGE_COLORS = {"strong": "#f5b942", "good": "#43a047", "warn": "#e8711a", "info": "#4a5a6a", "you": "#4fc3f7"}
PARTY_COLORS = ["#ab47bc", "#26a69a", "#ffa726", "#5c6bc0"]
MATCHUP_COLORS = {"good": "#43a047", "bad": "#e53935", "even": "#4a5a6a"}


def text_color_for(background: str) -> str:
    """Black or white text, whichever reads better on the given hex colour."""
    r, g, b = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    brightness = 0.299 * r + 0.587 * g + 0.114 * b  # standard perceived-brightness weights
    return "#111111" if brightness > 150 else "#ffffff"


def pill(parent, text: str, color: str, size: int = 8) -> tk.Label:
    """A small coloured label, used for badges, ranks and matchups."""
    return tk.Label(parent, text=text, bg=color, fg=text_color_for(color),
                    font=(FONT, size, "bold"), padx=6, pady=1)


def button(parent, text: str, command, primary: bool = False) -> tk.Button:
    return tk.Button(parent, text=text, command=command, relief="flat", font=(FONT, 10), padx=12, pady=3,
                     bg=COLORS["friendly"] if primary else COLORS["button"],
                     fg="#0b0d10" if primary else COLORS["text"],
                     activebackground=COLORS["selected"], activeforeground=COLORS["text"], cursor="hand2")


def label(parent, text: str = "", size: int = 10, color: str = "text", bold: bool = False, bg: str = "bg", **options) -> tk.Label:
    return tk.Label(parent, text=text, bg=COLORS.get(bg, bg), fg=COLORS.get(color, color),
                    font=(FONT, size, "bold" if bold else "normal"), **options)


def setup_styles(root: tk.Tk) -> None:
    """Dark styling for ttk tables (Treeview), which don't follow tk colour options."""
    style = ttk.Style(root)
    style.theme_use("clam")  # the default Windows theme ignores most colour settings
    style.configure("Treeview", background=COLORS["card"], fieldbackground=COLORS["card"],
                    foreground=COLORS["text"], rowheight=26, borderwidth=0, font=(FONT, 10),
                    bordercolor=COLORS["card"], lightcolor=COLORS["card"], darkcolor=COLORS["card"])  # no light outline
    style.map("Treeview", background=[("selected", COLORS["selected"])], foreground=[("selected", COLORS["text"])])
    style.configure("Treeview.Heading", background=COLORS["header"], foreground=COLORS["dim"],
                    relief="flat", borderwidth=0, font=(FONT, 9, "bold"), padding=(6, 5))
    style.map("Treeview.Heading", background=[("active", COLORS["button"])])
    style.configure("Vertical.TScrollbar", background=COLORS["button"], troughcolor=COLORS["card"],
                    borderwidth=0, arrowcolor=COLORS["dim"])
