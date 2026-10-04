"""Desktop app: press Ctrl+Shift+D in game to capture the scoreboard and see the lobby report.

Start without a terminal by double-clicking "Deadlock Analyzer.pyw" (or run: pythonw app.py).
"Overlay mode" keeps the window semi-transparent and on top of the game; that works when the
game runs in borderless windowed mode. Nothing here touches the game: the app only takes
screenshots, like the Windows snipping tool.

Layout: the two teams side by side, one card per player, so a full lobby fits without scrolling.

Threading: tkinter may only be used from the main thread. The global hotkey fires on the
keyboard library's thread, and analysis (OCR + API calls) takes a few seconds, so it runs on a
worker thread to keep the window responsive. Both hand results back through a queue that the
window checks every 100 ms.
"""

import json
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog

import keyboard

from player_lookup import analyze_screenshot
from report import TEAM_TITLES, badge_labels, hero_stats_text, most_played_text, team_summary
from screenshot_manager import capture_and_save_screenshot, delete_old_screenshots, get_screenshot_path
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

HOTKEY = "ctrl+shift+d"
POLL_MS = 100
CAPTURE_DELAY_MS = 150  # time for Windows to repaint after the overlay turns invisible
OVERLAY_ALPHA = 0.9
FONT = "Segoe UI"
SETTINGS_FILE = "settings.json"  # window position/size and overlay mode, remembered between runs

COLORS = {
    "bg": "#0f1115", "header": "#161a22", "card": "#1c212b", "text": "#e8eaed", "dim": "#8b93a1",
    "friendly": "#4fc3f7", "enemy": "#ef5350", "button": "#2a303c",
}
BADGE_COLORS = {"strong": "#f5b942", "good": "#43a047", "warn": "#e8711a", "info": "#4a5a6a"}
PARTY_COLORS = ["#ab47bc", "#26a69a", "#ffa726", "#5c6bc0"]


def text_color_for(background: str) -> str:
    """Black or white text, whichever reads better on the given hex colour."""
    r, g, b = (int(background[i:i + 2], 16) for i in (1, 3, 5))
    brightness = 0.299 * r + 0.587 * g + 0.114 * b  # standard perceived-brightness weights
    return "#111111" if brightness > 150 else "#ffffff"


def pill(parent, text: str, color: str, size: int = 8) -> tk.Label:
    """A small coloured label, used for badges and ranks."""
    return tk.Label(parent, text=text, bg=color, fg=text_color_for(color),
                    font=(FONT, size, "bold"), padx=6, pady=1)


class AnalyzerApp:
    def __init__(self, root: tk.Tk, open_screenshot: str = None):
        self.root = root
        self.events = queue.Queue()  # (kind, payload) messages from other threads
        self.busy = False
        settings = load_settings()
        self.overlay = tk.BooleanVar(value=settings.get("overlay", False))

        root.title("Deadlock Analyzer")
        root.geometry(settings.get("geometry", "1180x820"))
        root.minsize(980, 640)
        root.configure(bg=COLORS["bg"])
        self._build_header()
        self.body = tk.Frame(root, bg=COLORS["bg"])
        self.body.pack(fill="both", expand=True, padx=14, pady=(6, 14))
        self.show_message(f"Press {HOTKEY.upper()} in game with the Esc menu on the PLAYERS tab.\n\n"
                          "The lobby report will appear here.")

        keyboard.add_hotkey(HOTKEY, lambda: self.events.put(("hotkey", None)))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(POLL_MS, self.poll)
        self.apply_overlay()
        delete_old_screenshots()
        if open_screenshot:
            self.busy = True
            self.start_analysis(open_screenshot)

    # ---- layout ----------------------------------------------------------------------------

    def _build_header(self):
        header = tk.Frame(self.root, bg=COLORS["header"], padx=14, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="DEADLOCK ANALYZER", bg=COLORS["header"], fg=COLORS["text"],
                 font=(FONT, 14, "bold")).pack(side="left")
        tk.Checkbutton(header, text="Overlay mode", variable=self.overlay, command=self.apply_overlay,
                       bg=COLORS["header"], fg=COLORS["text"], selectcolor=COLORS["button"],
                       activebackground=COLORS["header"], activeforeground=COLORS["text"],
                       font=(FONT, 10)).pack(side="right", padx=(10, 0))
        for text, command in (("Open screenshot...", self.open_screenshot), ("Analyze latest", self.analyze_latest)):
            tk.Button(header, text=text, command=command, bg=COLORS["button"], fg=COLORS["text"],
                      activebackground=COLORS["card"], activeforeground=COLORS["text"], relief="flat",
                      font=(FONT, 10), padx=10).pack(side="right", padx=(6, 0))
        self.status = tk.Label(header, bg=COLORS["header"], fg=COLORS["dim"], font=(FONT, 10), anchor="w")
        self.status.pack(side="left", padx=20, fill="x", expand=True)

    def clear_body(self):
        for widget in self.body.winfo_children():
            widget.destroy()

    def show_message(self, message: str):
        self.clear_body()
        tk.Label(self.body, text=message, bg=COLORS["bg"], fg=COLORS["dim"],
                 font=(FONT, 13), justify="center").pack(expand=True)

    def render(self, results, parties):
        """Two team columns side by side, one card per player."""
        self.clear_body()
        if not results:
            self.show_message("No players found in the screenshot.\n\n"
                              "Make sure the Esc menu is open on the PLAYERS tab.")
            return

        # Each party gets its own colour, shown as the card's side stripe and a badge
        party_of = {}
        for n, party in enumerate(parties):
            for i in party:
                party_of[i] = (PARTY_COLORS[n % len(PARTY_COLORS)], f"PARTY {chr(65 + n)}")

        self.body.columnconfigure(0, weight=1, uniform="team")
        self.body.columnconfigure(1, weight=1, uniform="team")
        for column, (team, title) in enumerate(TEAM_TITLES.items()):
            members = [i for i, r in enumerate(results) if r["team"] == team]
            frame = tk.Frame(self.body, bg=COLORS["bg"])
            frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 8 if column == 0 else 0))

            team_parties = [p for p in parties if results[p[0]]["team"] == team]
            summary = team_summary([results[i] for i in members], team_parties)
            heading = tk.Frame(frame, bg=COLORS["bg"])
            heading.pack(fill="x", pady=(0, 6))
            tk.Label(heading, text=title, bg=COLORS["bg"], fg=COLORS[team], font=(FONT, 13, "bold")).pack(side="left")
            tk.Label(heading, text=summary, bg=COLORS["bg"], fg=COLORS["dim"], font=(FONT, 10)).pack(side="left", padx=10)

            for i in members:
                self._card(frame, results[i], COLORS[team], *party_of.get(i, (None, None)))

    def _card(self, parent, r, accent, party_color, party_label):
        bg = COLORS["card"]
        card = tk.Frame(parent, bg=bg)
        card.pack(fill="x", pady=3)
        tk.Frame(card, bg=party_color or accent, width=5).pack(side="left", fill="y")
        body = tk.Frame(card, bg=bg, padx=12, pady=5)
        body.pack(side="left", fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        # Row 1: name (click opens the Steam profile) and rank
        name = tk.Label(body, text=r["player"], bg=bg, fg=COLORS["text"], font=(FONT, 12, "bold"), anchor="w")
        name.grid(row=0, column=0, sticky="w")
        if r["profile_url"]:
            self._make_link(name, r["profile_url"])
        if r["rank"]:
            pill(body, r["rank"]["name"], r["rank"]["color"], size=9).grid(row=0, column=1, sticky="e")

        # Row 2: the hero they're on and how they do on it
        line = tk.Frame(body, bg=bg)
        line.grid(row=1, column=0, columnspan=2, sticky="w", pady=(1, 0))
        tk.Label(line, text=r["hero"], bg=bg, fg=accent, font=(FONT, 10, "bold")).pack(side="left")
        tk.Label(line, text="   " + hero_stats_text(r), bg=bg, fg=COLORS["text"], font=(FONT, 10)).pack(side="left")

        # Row 3: badges
        badges = badge_labels(r)
        if party_label:
            badges.insert(0, (party_label, "party"))
        if badges:
            row = tk.Frame(body, bg=bg)
            row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
            for label, kind in badges:
                color = party_color if kind == "party" else BADGE_COLORS[kind]
                pill(row, label, color).pack(side="left", padx=(0, 4))

        # Row 4: their usual heroes, in smaller, dimmer text (the identity details are in the badges)
        details = most_played_text(r)
        if details:
            tk.Label(body, text=details, bg=bg, fg=COLORS["dim"], font=(FONT, 9), anchor="w").grid(
                row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))

    def _make_link(self, label: tk.Label, url: str):
        normal, hover = (FONT, 12, "bold"), (FONT, 12, "bold underline")
        label.config(cursor="hand2")
        label.bind("<Button-1>", lambda event: webbrowser.open(url))
        label.bind("<Enter>", lambda event: label.config(font=hover))
        label.bind("<Leave>", lambda event: label.config(font=normal))

    # ---- main-thread helpers -------------------------------------------------------------

    def set_status(self, message: str):
        self.status.config(text=message)

    def apply_overlay(self):
        on = self.overlay.get()
        self.root.attributes("-topmost", on)
        self.root.attributes("-alpha", OVERLAY_ALPHA if on else 1.0)

    def poll(self):
        """Handle messages from the hotkey and worker threads, then check again shortly."""
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "hotkey":
                    self.capture()
                elif kind == "progress":
                    self.set_status(payload)
                elif kind == "done":
                    self.show_report(*payload)
                elif kind == "error":
                    self.busy = False
                    self.set_status(f"Something went wrong: {payload}")
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self.poll)

    def capture(self):
        if self.busy:
            return
        self.busy = True
        self.set_status("Capturing screenshot...")
        # In overlay mode the window would cover the scoreboard in its own screenshot. Making it
        # fully transparent (instead of hiding it) avoids stealing keyboard focus from the game.
        self.root.attributes("-alpha", 0.0)
        self.root.after(CAPTURE_DELAY_MS, self._capture_now)

    def _capture_now(self):
        try:
            path = capture_and_save_screenshot()
        except Exception as e:
            logger.exception("Screenshot failed")
            self.busy = False
            self.set_status(f"Screenshot failed: {e}")
            return
        finally:
            self.apply_overlay()  # restore normal opacity
        delete_old_screenshots()
        self.start_analysis(path)

    def analyze_latest(self):
        if self.busy:
            return
        path = get_screenshot_path()
        if not path:
            self.set_status("No screenshots yet. Press the hotkey in game first.")
            return
        self.busy = True
        self.start_analysis(path)

    def open_screenshot(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="Open a scoreboard screenshot", initialdir="screenshots",
                                          filetypes=[("PNG screenshots", "*.png"), ("All files", "*.*")])
        if path:
            self.busy = True
            self.start_analysis(path)

    def start_analysis(self, path: str):
        self.set_status(f"Reading {os.path.basename(path)} and looking up players...")
        threading.Thread(target=self._analyze, args=(path,), daemon=True).start()

    def _analyze(self, path: str):
        """Runs on a worker thread: no tkinter calls here, only queue messages."""
        try:
            results, parties = analyze_screenshot(path, progress=lambda message: self.events.put(("progress", message)))
            self.events.put(("done", (path, results, parties)))
        except Exception as e:
            logger.exception("Analysis failed")
            self.events.put(("error", str(e)))

    def show_report(self, path, results, parties):
        self.busy = False
        self.render(results, parties)
        self.set_status(f"{len(results)} players from {os.path.basename(path)}  ·  "
                        f"{HOTKEY.upper()} for a new screenshot")
        self.root.bell()  # audible cue when the report is ready while you're in game

    def close(self):
        save_settings({"geometry": self.root.geometry(), "overlay": self.overlay.get()})
        keyboard.unhook_all()
        self.root.destroy()


def load_settings() -> dict:
    """Saved window settings, or {} on first run (or if the file is unreadable)."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError as e:
        logger.warning(f"Could not save settings ({e})")


def main():
    setup_logger()
    root = tk.Tk()
    # Optional: python app.py path/to/screenshot.png opens straight onto that screenshot
    AnalyzerApp(root, open_screenshot=sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
