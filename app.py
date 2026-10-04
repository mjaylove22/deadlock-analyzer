"""Desktop app: press Ctrl+Shift+D in game to capture the scoreboard and see the lobby report.

Start without a terminal by double-clicking "Deadlock Analyzer.pyw" (or run: pythonw app.py).
"Overlay mode" keeps the window semi-transparent and on top of the game; that works when the
game runs in borderless windowed mode. Nothing here touches the game: the app only takes
screenshots, like the Windows snipping tool.

Threading: tkinter may only be used from the main thread. The global hotkey fires on the
keyboard library's thread, and analysis (OCR + API calls) takes a few seconds, so it runs on a
worker thread to keep the window responsive. Both hand results back through a queue that the
window checks every 100 ms.
"""

import logging
import os
import queue
import threading
import tkinter as tk
import webbrowser

import keyboard

from player_lookup import analyze_screenshot
from report import build_report
from screenshot_manager import capture_and_save_screenshot, get_screenshot_path
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

HOTKEY = "ctrl+shift+d"
POLL_MS = 100
CAPTURE_DELAY_MS = 150  # time for Windows to repaint after the overlay turns invisible
OVERLAY_ALPHA = 0.88

COLORS = {
    "bg": "#16181d", "panel": "#1f232b", "text": "#e6e6e6", "dim": "#9aa3b2",
    "team": "#f0c060", "party": "#ff8a65", "link": "#6fa8ff", "hero": "#c7d0dc",
}


class AnalyzerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events = queue.Queue()  # (kind, payload) messages from other threads
        self.busy = False
        self.overlay = tk.BooleanVar(value=False)
        self.link_count = 0

        root.title("Deadlock Analyzer")
        root.geometry("460x720")
        root.configure(bg=COLORS["bg"])
        self._build_widgets()

        keyboard.add_hotkey(HOTKEY, lambda: self.events.put(("hotkey", None)))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(POLL_MS, self.poll)
        self.set_status(f"Press {HOTKEY.upper()} in game with the Esc menu on the PLAYERS tab.")

    def _build_widgets(self):
        bar = tk.Frame(self.root, bg=COLORS["bg"])
        bar.pack(fill="x", padx=8, pady=(8, 4))
        tk.Button(bar, text="Analyze latest screenshot", command=self.analyze_latest,
                  bg=COLORS["panel"], fg=COLORS["text"], relief="flat", padx=8).pack(side="left")
        tk.Checkbutton(bar, text="Overlay mode", variable=self.overlay, command=self.apply_overlay,
                       bg=COLORS["bg"], fg=COLORS["text"], selectcolor=COLORS["panel"],
                       activebackground=COLORS["bg"], activeforeground=COLORS["text"]).pack(side="right")

        self.status = tk.Label(self.root, anchor="w", bg=COLORS["bg"], fg=COLORS["dim"], wraplength=440, justify="left")
        self.status.pack(fill="x", padx=8)

        frame = tk.Frame(self.root, bg=COLORS["bg"])
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")
        self.text = tk.Text(frame, bg=COLORS["panel"], fg=COLORS["text"], relief="flat", wrap="none",
                            font=("Consolas", 10), padx=8, pady=8, yscrollcommand=scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.text.yview)

        # One text style per report line style (see report.py)
        self.text.tag_configure("team", foreground=COLORS["team"], font=("Consolas", 11, "bold"), spacing1=6)
        self.text.tag_configure("party", foreground=COLORS["party"])
        self.text.tag_configure("player", font=("Consolas", 10, "bold"), spacing1=6)
        self.text.tag_configure("note", foreground=COLORS["dim"])
        self.text.tag_configure("link", foreground=COLORS["link"], underline=True)
        self.text.tag_configure("hero", foreground=COLORS["hero"])
        self.text.config(state="disabled")

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

    def start_analysis(self, path: str):
        self.set_status(f"Reading {os.path.basename(path)} and looking up players...")
        threading.Thread(target=self._analyze, args=(path,), daemon=True).start()

    def _analyze(self, path: str):
        """Runs on a worker thread: no tkinter calls here, only queue messages."""
        try:
            results, parties = analyze_screenshot(path)
            self.events.put(("done", (path, results, parties)))
        except Exception as e:
            logger.exception("Analysis failed")
            self.events.put(("error", str(e)))

    def show_report(self, path, results, parties):
        self.busy = False
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for text, style in build_report(results, parties):
            if style == "link":
                # Each link gets its own tag so a click knows which URL to open
                url = text.strip()
                tag = f"link{self.link_count}"
                self.link_count += 1
                self.text.tag_bind(tag, "<Button-1>", lambda event, u=url: webbrowser.open(u))
                self.text.tag_bind(tag, "<Enter>", lambda event: self.text.config(cursor="hand2"))
                self.text.tag_bind(tag, "<Leave>", lambda event: self.text.config(cursor=""))
                self.text.insert("end", text + "\n", ("link", tag))
            else:
                self.text.insert("end", text + "\n", style)
        self.text.config(state="disabled")
        self.set_status(f"{len(results)} players from {os.path.basename(path)}. "
                        f"Press {HOTKEY.upper()} again for a new screenshot.")
        self.root.bell()  # audible cue when the report is ready while you're in game

    def close(self):
        keyboard.unhook_all()
        self.root.destroy()


def main():
    setup_logger()
    root = tk.Tk()
    AnalyzerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
