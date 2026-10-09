"""The app's look: colours, fonts and the building blocks every page uses.

Rounded shapes come from CustomTkinter (cards, pills, buttons, toggles). Plain text uses ordinary
tk labels, which are much faster to create than CustomTkinter's canvas-drawn widgets.
"""

import ctypes
import ctypes.wintypes
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
    "map_streets": "#323b4d",  # the minimap's walkable area, a shade lighter than a card
    "switch_off": "#232a37", "switch_knob": "#e6e9ef",
}
# Settings' light theme: the same names, each text colour at least 4.5:1 on a card (tests/test_theme.py)
LIGHT_COLORS = {
    "bg": "#eef1f5", "surface": "#e3e8ef", "card": "#ffffff", "card_border": "#d3dae4", "hover_border": "#1574b8",
    "text": "#161b24", "dim": "#5a6475", "faint": "#7d8696",
    "friendly": "#1574b8", "enemy": "#c94a12", "accent": "#1574b8", "accent_dark": "#105d94",
    "button": "#e3e8ef", "button_hover": "#d4dbe5", "link": "#1a62c4",
    "win": "#1b7f45", "loss": "#cc3333", "selected": "#d5e5fa", "note": "#7d6210",
    "map_streets": "#d6dce6",
    "switch_off": "#b4bdca", "switch_knob": "#ffffff",
}
BADGE_COLORS = {"strong": "#f5b942", "good": "#3fbf74", "warn": "#f0803c", "info": "#3d4a5c", "you": "#5ec8ff",
                "history": "#4b3f72", "watch": "#c23535"}
PARTY_COLORS = ["#b36bff", "#2ec4b6", "#ffb347", "#6c8cff"]
MATCHUP_COLORS = {"good": "#2f9e5b", "bad": "#c23535", "even": "#3d4a5c"}
ITEM_SLOT_COLORS = {"weapon": "#ec981a", "vitality": "#6aa11a", "spirit": "#a977d2"}  # the shop's three categories, sampled from the game


def text_color_for(background: str) -> str:
    """Black or white text, whichever has the higher WCAG contrast on the given hex colour. (A plain
    brightness cutoff put white on mid-bright greens and teals at ~2:1, hard to read.)"""
    def luminance(color: str) -> float:
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    dark, light = "#0b0d12", "#ffffff"
    fill = luminance(background)
    return dark if (fill + 0.05) / (luminance(dark) + 0.05) >= (1.05 / (fill + 0.05)) else light


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
                         text_color=COLORS["text"], progress_color=COLORS["accent"], button_color=COLORS["switch_knob"],
                         fg_color=COLORS["switch_off"], switch_width=36, switch_height=18,
                         border_width=0 if is_light() else 3)  # light: a white knob inside the track, not over the card


def choose_theme(name: str) -> None:
    """"light" or "dark" (the default). Call before any widget is built: every widget reads COLORS when
    it's made, so a change shows after a restart."""
    if name == "light":
        COLORS.update(LIGHT_COLORS)
    ctk.set_appearance_mode("light" if name == "light" else "dark")


def is_light() -> bool:
    return COLORS["card"] == LIGHT_COLORS["card"]


TEXT_SIZES = {"normal": 1.0, "large": 1.15}
text_scale = 1.0  # set once at start-up by choose_text_size: the chosen size, Normal or Large
DESIGN_HEIGHT = 1000  # the window's outer height at 100% display scaling: every page is made to fit it


def fit_factor(room: int, dpi: float) -> float:
    """How much to shrink so the window fits room (the screen's work area, in pixels) when Windows display
    scaling (dpi, 1.25 for 125%) would make it taller: 1 when it fits as it is."""
    return min(1.0, room / (DESIGN_HEIGHT * dpi))


def work_area_height() -> int:
    """The primary screen's height minus the taskbar, in pixels. ponytail: the primary screen only; a
    second screen of another size would need MonitorFromWindow."""
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return rect.bottom - rect.top


def choose_text_size(root, name: str) -> None:
    """"normal" (the default) or "large": every font grows by the same factor, tk's (sized in points, so they
    follow tk scaling) and CustomTkinter's (sized in pixels, following its widget scaling). Like the theme,
    only at start-up. Pixel sizes (images, gaps, wrap widths) stay, so large text wraps a little sooner.
    And on a screen too short for the window at its display scaling (a 1080p laptop at 150%: the window
    was 1,179 px tall, its bottom under the taskbar), everything, window included, shrinks until it fits."""
    global text_scale
    text_scale = TEXT_SIZES.get(name, 1.0)
    try:
        fit = fit_factor(work_area_height(), ctk.ScalingTracker.get_window_dpi_scaling(root))
    except (OSError, AttributeError, ValueError):  # not on Windows, or no answer: leave it as it is
        fit = 1.0
    if text_scale * fit != 1.0:
        root.tk.call("tk", "scaling", float(root.tk.call("tk", "scaling")) * text_scale * fit)
        ctk.set_widget_scaling(text_scale * fit)
    if fit < 1.0:
        ctk.set_window_scaling(fit)


def setup_styles(root) -> None:
    """Styling for ttk tables, which don't follow CustomTkinter's theme."""
    style = ttk.Style(root)
    style.theme_use("clam")  # the default Windows theme ignores most colour settings
    style.configure("Treeview", background=COLORS["card"], fieldbackground=COLORS["card"],
                    foreground=COLORS["text"], rowheight=34, borderwidth=0, font=(FONT, 10),  # roomy enough for Large text
                    bordercolor=COLORS["card"], lightcolor=COLORS["card"], darkcolor=COLORS["card"])  # no light outline
    style.map("Treeview", background=[("selected", COLORS["selected"])], foreground=[("selected", COLORS["text"])])
    style.configure("Treeview.Heading", background=COLORS["surface"], foreground=COLORS["dim"], relief="flat",
                    borderwidth=0, font=(FONT, 9, "bold"), padding=(8, 7),
                    bordercolor=COLORS["surface"], lightcolor=COLORS["surface"], darkcolor=COLORS["surface"])
    style.map("Treeview.Heading", background=[("active", COLORS["button"])])
    style.configure("Vertical.TScrollbar", background=COLORS["button"], troughcolor=COLORS["card"],
                    borderwidth=0, arrowcolor=COLORS["dim"], bordercolor=COLORS["card"],
                    lightcolor=COLORS["button"], darkcolor=COLORS["button"])
    # Nothing to scroll: clam has no colours for that state and shows a white bar, so blend it into the card
    style.map("Vertical.TScrollbar", background=[("disabled", COLORS["card"])], arrowcolor=[("disabled", COLORS["card"])],
              lightcolor=[("disabled", COLORS["card"])], darkcolor=[("disabled", COLORS["card"])])
