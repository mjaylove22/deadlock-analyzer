"""The app's look: colours, fonts and the building blocks every page uses.

Rounded shapes come from CustomTkinter (cards, pills, buttons, toggles). Plain text uses ordinary
tk labels, which are much faster to create than CustomTkinter's canvas-drawn widgets.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence

import customtkinter as ctk

FONT = "Segoe UI"
HEADING_FONT = "Segoe UI Semibold"

COLORS = {
    "bg": "#0b0d12", "surface": "#12161e", "card": "#181d27", "card_border": "#222938", "hover_border": "#5ec8ff",
    "text": "#eef1f6", "dim": "#8a94a6", "faint": "#596377",
    "friendly": "#5ec8ff", "enemy": "#ff7a45", "accent": "#5ec8ff", "accent_dark": "#3a9fd1",
    "button": "#232a37", "button_hover": "#2d3646", "link": "#7ab8ff",
    "win": "#5fd38d", "loss": "#ff6b6b", "selected": "#26324a", "note": "#d9c27a",
}
BADGE_COLORS = {"strong": "#f5b942", "good": "#3fbf74", "warn": "#f0803c", "info": "#3d4a5c", "you": "#5ec8ff",
                "history": "#4b3f72", "watch": "#d64545"}
PARTY_COLORS = ["#b36bff", "#2ec4b6", "#ffb347", "#6c8cff"]
MATCHUP_COLORS = {"good": "#2f9e5b", "bad": "#d64545", "even": "#3d4a5c"}
ITEM_SLOT_COLORS = {"weapon": "#ec981a", "vitality": "#6aa11a", "spirit": "#a977d2"}  # the shop's three categories, sampled from the game


def text_color_for(background: str) -> str:
    """Black or white text, whichever reads better on the given hex colour."""
    r, g, b = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    brightness = 0.299 * r + 0.587 * g + 0.114 * b  # standard perceived-brightness weights
    return "#0b0d12" if brightness > 150 else "#ffffff"


def resolve(name: str) -> str:
    """A colour by theme name ("dim", "card"...), or a hex colour passed straight through."""
    return COLORS.get(name, name)


def label(parent, text: str = "", size: int = 10, color: str = "text", bold: bool = False,
          bg: str = "bg", heading: bool = False, **options) -> tk.Label:
    """Plain text. bg must match what it sits on (e.g. "card" inside a card)."""
    family = HEADING_FONT if heading else FONT
    return tk.Label(parent, text=text, bg=resolve(bg), fg=resolve(color),
                    font=(family, size, "bold" if bold else "normal"), **options)


def pill(parent, text: str, fill: str, size: int = 8, image=None, text_color: str = None) -> ctk.CTkLabel:
    """A small rounded label for badges, ranks and matchups; optionally with an image."""
    return ctk.CTkLabel(parent, text=f" {text} " if text else "", fg_color=fill, corner_radius=7, height=20,
                        text_color=text_color or text_color_for(fill), font=(FONT, size, "bold"),
                        image=image, compound="left")


def button(parent, text: str, command: Callable, primary: bool = False, width: int = 0) -> ctk.CTkButton:
    return ctk.CTkButton(parent, text=text, command=command, corner_radius=8, height=32, width=width or 0,
                         font=(FONT, 11, "bold" if primary else "normal"),
                         fg_color=COLORS["accent"] if primary else COLORS["button"],
                         hover_color=COLORS["accent_dark"] if primary else COLORS["button_hover"],
                         text_color="#071018" if primary else COLORS["text"])


def card(parent, padding: int = 14, fill: str = "card", hoverable: bool = False) -> tuple:
    """A rounded card. Returns (outer CTkFrame, inner tk.Frame to put content in)."""
    outer = ctk.CTkFrame(parent, corner_radius=12, fg_color=COLORS[fill], border_width=1,
                         border_color=COLORS["card_border"])
    inner = tk.Frame(outer, bg=COLORS[fill])
    inner.pack(fill="both", expand=True, padx=padding, pady=padding - 2)
    if hoverable:
        set_hover(outer)
    return outer, inner


def set_hover(outer: ctk.CTkFrame) -> None:
    """Light up a card's border while the mouse is over it (or anything inside it)."""
    def inside(event) -> bool:
        widget = outer.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is outer:
                return True
            widget = widget.master
        return False

    def bind_all(widget):
        widget.bind("<Enter>", lambda e: outer.configure(border_color=COLORS["hover_border"]), add="+")
        widget.bind("<Leave>", lambda e: None if inside(e) else outer.configure(border_color=COLORS["card_border"]), add="+")
        for child in widget.winfo_children():
            bind_all(child)
    outer.after_idle(lambda: bind_all(outer))  # after the card's content has been added


def segmented(parent, values: Sequence[str], selected: str, command: Callable[[str], None]) -> ctk.CTkSegmentedButton:
    """A pill-shaped switch between options (e.g. Normal | Street Brawl)."""
    control = ctk.CTkSegmentedButton(parent, values=list(values), command=command, corner_radius=8, height=30,
                                     font=(FONT, 11), fg_color=COLORS["button"], selected_color=COLORS["accent"],
                                     selected_hover_color=COLORS["accent_dark"], unselected_color=COLORS["button"],
                                     unselected_hover_color=COLORS["button_hover"], text_color=COLORS["text"])
    control.set(selected)
    return control


def dropdown(parent, values: Sequence[str], selected: str, command: Callable[[str], None]) -> ctk.CTkOptionMenu:
    menu = ctk.CTkOptionMenu(parent, values=list(values), command=command, corner_radius=8, height=30, width=190,
                             font=(FONT, 11), dropdown_font=(FONT, 11), fg_color=COLORS["button"],
                             button_color=COLORS["button_hover"], button_hover_color=COLORS["selected"],
                             dropdown_fg_color=COLORS["card"], dropdown_hover_color=COLORS["selected"],
                             dropdown_text_color=COLORS["text"], text_color=COLORS["text"])
    menu.set(selected)
    return menu


def switch(parent, text: str, variable: tk.BooleanVar, command: Callable[[], None]) -> ctk.CTkSwitch:
    return ctk.CTkSwitch(parent, text=text, variable=variable, command=command, font=(FONT, 11),
                         text_color=COLORS["text"], progress_color=COLORS["accent"], button_color="#e6e9ef",
                         fg_color=COLORS["button"], switch_width=36, switch_height=18)


def setup_styles(root) -> None:
    """CustomTkinter dark mode, plus dark styling for ttk tables (which follow neither)."""
    ctk.set_appearance_mode("dark")
    style = ttk.Style(root)
    style.theme_use("clam")  # the default Windows theme ignores most colour settings
    style.configure("Treeview", background=COLORS["card"], fieldbackground=COLORS["card"],
                    foreground=COLORS["text"], rowheight=34, borderwidth=0, font=(FONT, 10),
                    bordercolor=COLORS["card"], lightcolor=COLORS["card"], darkcolor=COLORS["card"])  # no light outline
    style.map("Treeview", background=[("selected", COLORS["selected"])], foreground=[("selected", COLORS["text"])])
    style.configure("Treeview.Heading", background=COLORS["surface"], foreground=COLORS["dim"], relief="flat",
                    borderwidth=0, font=(FONT, 9, "bold"), padding=(8, 7),
                    bordercolor=COLORS["surface"], lightcolor=COLORS["surface"], darkcolor=COLORS["surface"])
    style.map("Treeview.Heading", background=[("active", COLORS["button"])])
    style.configure("Vertical.TScrollbar", background=COLORS["button"], troughcolor=COLORS["card"],
                    borderwidth=0, arrowcolor=COLORS["dim"], bordercolor=COLORS["card"],
                    lightcolor=COLORS["button"], darkcolor=COLORS["button"])
