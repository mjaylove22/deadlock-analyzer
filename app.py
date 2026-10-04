"""Desktop app: open the scoreboard in game and see who's in your lobby.

Start without a terminal by double-clicking "Deadlock Analyzer.pyw" (or run: pythonw app.py).
Nothing here touches the game: the app only takes screenshots, like the Windows snipping tool.

Structure:
- This file: the window, top bar, navigation (with Back), background tasks, capture and auto-detect.
- ui/pages.py: Home, Lobby, Heroes, Search, Player (and My Stats). ui/widgets.py: reusable pieces.

Threading: tkinter may only be used from the main thread. The hotkey, the auto-detect watcher and
all slow work (OCR, API calls, downloads) run on other threads and hand results back through a
queue that the window checks every 100 ms.
"""

import ctypes
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Dict

import customtkinter as ctk
import keyboard
import mss
from PIL import Image

import assets
import deadlock_api
from matchups import build_matchup
from player_lookup import analyze_records, read_lobby
import game_window
import layout as layout_module
from screenshot_manager import capture_and_save_screenshot, delete_old_screenshots, get_screenshot_path
from settings import get_me, load_settings, save_settings
from ui.pages import HeroesPage, HeroPage, HomePage, LobbyPage, MatchPage, PlayerPage, SearchPage, SetupPage
from ui import images
from ui.theme import COLORS, FONT, HEADING_FONT, label, setup_styles, switch
from ui.widgets import AvatarCache
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

HOTKEY = "ctrl+shift+d"
POLL_MS = 100
CAPTURE_DELAY_MS = 150   # time for Windows to repaint after the window turns invisible (older Windows only)
CAPTURE_HIDE_MS = 50     # time for Windows to apply "hide from capture" before the app's own screenshot
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11  # SetWindowDisplayAffinity modes
OVERLAY_ALPHA = 0.9
WATCH_INTERVAL_S = 1.0   # how often auto-detect checks for the scoreboard (one check takes ~6 ms)
SETTLE_S = 0.5           # after the scoreboard appears, wait for the menu animation before capturing
NAV_TABS = [("lobby", "Lobby"), ("heroes", "Heroes"), ("mystats", "My Stats")]
ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")


class AnalyzerApp:
    def __init__(self, root: tk.Tk, open_screenshot: str = None):
        self.root = root
        self.events = queue.Queue()  # callables from other threads, run on the main thread
        self.busy = False            # a capture/analysis is in progress
        self.lobby = None            # the latest lobby: results, parties, matchup, path, time
        self.last_records = None     # OCR'd lobby shown last, to skip re-analysing the same one
        self.cache: Dict[Any, Any] = {}
        self.avatars = AvatarCache()
        self.history = []            # pages to go Back to: (page class, options)
        self.current = None
        self.page = None
        self.page_token = 0          # bumped on every navigation; stale task results are dropped

        settings = load_settings()
        self.overlay = tk.BooleanVar(value=settings.get("overlay", False))
        self.auto_detect = tk.BooleanVar(value=settings.get("auto_detect", True))
        self.watching = self.auto_detect.get()  # plain copy for the watcher thread (tk variables are main-thread only)

        root.title("Deadlock Analyzer")
        root.geometry(settings.get("geometry", "1180x960"))  # a full 6v6 lobby needs ~950 px
        root.minsize(1100, 760)
        if isinstance(root, ctk.CTk):
            root.configure(fg_color=COLORS["bg"])
        else:
            root.configure(bg=COLORS["bg"])
        setup_styles(root)
        try:
            self.icon = tk.PhotoImage(file=ICON_PATH)  # kept on self: tkinter drops images nobody references
            root.iconphoto(True, self.icon)
        except tk.TclError:
            pass
        self._build_top_bar()
        self.status = label(root, color="dim", bg="surface", anchor="w", padx=14, pady=5)
        self.status.pack(side="bottom", fill="x")
        self.container = tk.Frame(root, bg=COLORS["bg"])
        self.container.pack(fill="both", expand=True, padx=16, pady=10)

        keyboard.add_hotkey(HOTKEY, lambda: self.events.put(self.capture))
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Alt-Left>", lambda event: self.back())
        root.after(POLL_MS, self.poll)
        self.apply_overlay()
        delete_old_screenshots()
        threading.Thread(target=self._watch, daemon=True).start()
        threading.Thread(target=self._preload_images, daemon=True).start()

        self.navigate(HomePage)
        self.set_status("Watching for the scoreboard..." if self.watching else f"Press {HOTKEY.upper()} in game to capture")
        if open_screenshot:
            self.busy = True
            self.start_analysis(open_screenshot)

    # ---- top bar and navigation ------------------------------------------------------------

    def _build_top_bar(self):
        bar = tk.Frame(self.root, bg=COLORS["surface"], padx=12, pady=10)
        bar.pack(fill="x")
        tk.Frame(self.root, bg=COLORS["card_border"], height=1).pack(fill="x")  # hairline under the bar
        self.back_button = ctk.CTkButton(bar, text="\u2190", command=self.back, width=34, height=32, corner_radius=8,
                                         font=(FONT, 15), fg_color=COLORS["button"], hover_color=COLORS["button_hover"],
                                         text_color=COLORS["text"], text_color_disabled=COLORS["faint"])
        self.back_button.pack(side="left")
        title = tk.Frame(bar, bg=COLORS["surface"], cursor="hand2")
        title.pack(side="left", padx=(12, 6))
        label(title, "DEADLOCK", size=14, heading=True, bg="surface", cursor="hand2").pack(side="left")
        label(title, " ANALYZER", size=14, heading=True, color="accent", bg="surface", cursor="hand2").pack(side="left")
        for widget in (title, *title.winfo_children()):
            widget.bind("<Button-1>", lambda event: self.navigate(HomePage))

        self.tabs = {}
        for key, text in NAV_TABS:
            holder = tk.Frame(bar, bg=COLORS["surface"])
            holder.pack(side="left", padx=(14 if key == "lobby" else 2, 0))
            tab = label(holder, text, size=11, color="dim", bg="surface", cursor="hand2", padx=10, pady=4)
            tab.pack()
            underline = tk.Frame(holder, bg=COLORS["surface"], height=2)
            underline.pack(fill="x", padx=8)
            tab.bind("<Button-1>", lambda event, k=key: self.open_tab(k))
            self.tabs[key] = (tab, underline)

        for text, variable, command in (("Overlay", self.overlay, self.toggle_overlay),
                                        ("Auto-detect", self.auto_detect, self.apply_auto_detect)):
            switch(bar, text, variable, command).pack(side="right", padx=(10, 0))
        self.search_box = ctk.CTkEntry(bar, width=240, height=32, corner_radius=8, border_width=1,
                                       placeholder_text="Player name or match ID", font=(FONT, 11),
                                       fg_color=COLORS["button"], border_color=COLORS["card_border"],
                                       text_color=COLORS["text"], placeholder_text_color=COLORS["faint"])
        self.search_box.pack(side="right", padx=(0, 10))
        self.search_box.bind("<Return>", lambda event: self.search(self.search_box.get()))

    def navigate(self, page_class, push: bool = True, **options):
        """Show a page. push=False replaces the current page instead of adding a Back step."""
        if push and self.current and self.current != (page_class, options):
            self.history.append(self.current)
        self.current = (page_class, options)
        self.page_token += 1
        if self.page:
            self.page.frame.destroy()
        self.page = page_class(self, self.container, **options)
        self.page.frame.pack(fill="both", expand=True)
        self.back_button.configure(state="normal" if self.history else "disabled")
        for key, (tab, underline) in self.tabs.items():
            active = key == self.page.nav
            tab.config(fg=COLORS["text"] if active else COLORS["dim"], font=(HEADING_FONT if active else FONT, 11))
            underline.config(bg=COLORS["accent"] if active else COLORS["surface"])
        self.fit_window()

    def back(self):
        if self.history:
            page_class, options = self.history.pop()
            self.navigate(page_class, push=False, **options)

    def open_tab(self, key: str):
        {"lobby": self.open_lobby, "heroes": self.open_heroes, "mystats": self.open_my_stats}[key]()

    def open_lobby(self):
        self.navigate(LobbyPage)

    def open_heroes(self, mode: str = "Normal", band: str = "All ranks", push: bool = True):
        self.navigate(HeroesPage, push=push, mode=mode, band=band)

    def open_hero(self, hero: str, mode: str = "Normal", band: str = "All ranks", push: bool = True):
        self.navigate(HeroPage, push=push, hero=hero, mode=mode, band=band)

    def open_player(self, account_id: int, mode: str = "All", nav: str = None, push: bool = True):
        self.navigate(PlayerPage, push=push, account_id=account_id, mode=mode, nav=nav)

    def open_my_stats(self):
        me = get_me()
        if me:
            self.open_player(me["account_id"], nav="mystats")
        else:
            self.navigate(SetupPage)

    def search(self, query: str):
        query = query.strip()
        if not query:
            return
        self.search_box.delete(0, "end")
        self.search_box.insert(0, query)
        if query.isdigit() and len(query) >= 6:  # a match ID (shown bottom-right of the scoreboard)
            self.open_match(int(query))
        else:
            self.navigate(SearchPage, query=query)

    def open_match(self, match_id: int):
        self.navigate(MatchPage, match_id=match_id)

    def focus_search(self):
        self.search_box.focus_set()
        self.set_status("Type a Steam name in the search box and press Enter")

    def fit_window(self):
        """Grow the window if the content needs more height than it has (e.g. a small saved size)."""
        self.root.update_idletasks()
        # Never taller than the screen (minus room for the taskbar), or Windows maximises the window
        needed = min(self.root.winfo_reqheight(), self.root.winfo_screenheight() - 80)
        if self.root.state() == "normal" and self.root.winfo_height() < needed:
            self.root.geometry(f"{self.root.winfo_width()}x{needed}")

    # ---- background work --------------------------------------------------------------------

    def run_task(self, work: Callable[[], Any], on_done: Callable[[Any], None],
                 on_error: Callable[[Exception], None] = None):
        """Run work() on a worker thread, then on_done(result) on the main thread, but only if the
        user is still on the page that asked for it. on_error(exception) handles failures."""
        token = self.page_token

        def runner():
            try:
                result = work()
                self.events.put(lambda: on_done(result) if token == self.page_token else None)
            except Exception as e:
                logger.exception("Background task failed")
                error = e  # bound now: Python clears "e" when the except block ends
                if on_error:
                    self.events.put(lambda: on_error(error) if token == self.page_token else None)
                else:
                    self.events.put(lambda: self.set_status(f"Something went wrong: {error}"))
        threading.Thread(target=runner, daemon=True).start()

    def progress(self, message: str):
        """Safe to call from any thread."""
        self.events.put(lambda: self.set_status(message))

    def poll(self):
        """Run everything other threads have queued for the main thread, then check again shortly."""
        try:
            while True:
                self.events.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self.poll)

    def _preload_images(self):
        """Background: hero icons and rank emblems (from the disk cache after the first run), then
        redraw the current page so it gets its images."""
        try:
            art = assets.hero_art()
            assets.preload()
        except Exception:
            logger.exception("Could not preload images")
            return

        def ready():
            images.set_hero_art(art)
            if self.current:
                page_class, options = self.current
                self.navigate(page_class, push=False, **options)
        self.events.put(ready)

    def hero_names_by_id(self) -> Dict[int, str]:
        return {h["id"]: h["name"] for h in deadlock_api.fetch_heroes()}

    def set_status(self, message: str):
        self.status.config(text=message)

    # ---- capture, auto-detect and lobby analysis -------------------------------------------

    def set_capture_hidden(self, hidden: bool) -> bool:
        """Ask Windows to leave this window out of screen captures, or to show it again.

        Hiding applies to EVERY capture tool, Discord and OBS included (a stream shows what's behind
        the app), so it's only used while needed: in overlay mode, where the window floats over the
        game and would otherwise cover the scoreboard in its own screenshots, and for the instant of
        the app's own screenshot. Needs Windows 10 (2004) or later; returns whether it worked."""
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if hidden else WDA_NONE))
        except Exception:
            return False

    def apply_overlay(self):
        on = self.overlay.get()
        self.root.attributes("-topmost", on)
        self.root.attributes("-alpha", OVERLAY_ALPHA if on else 1.0)
        # Visible to Discord/OBS normally; hidden only while floating over the game
        self.capture_hidden = self.set_capture_hidden(on) and on

    def toggle_overlay(self):
        self.apply_overlay()
        self.set_status("Overlay on: the window floats over the game and is hidden from screen sharing (Discord, OBS)."
                        if self.overlay.get() else "Overlay off: the window shows up normally in screen sharing.")

    def apply_auto_detect(self):
        self.watching = self.auto_detect.get()
        save_settings({"auto_detect": self.watching})
        self.set_status("Watching for the scoreboard..." if self.watching else "Auto-detect off")

    def _watch(self):
        """Watcher thread: when the scoreboard appears, ask the main thread to capture it.
        Fires once per opening; closing and reopening the menu fires again.

        It follows the Deadlock window (any monitor, windowed or not) and does nothing at all while
        the game isn't running. The layout that matches a window size is remembered, so after the
        first time only that one spot is checked (a few ms per second)."""
        was_open = False
        last_full_search = 0.0
        with mss.mss() as sct:  # mss objects can't be shared between threads, so this thread has its own
            while True:
                time.sleep(WATCH_INTERVAL_S)
                if not self.watching or self.busy:
                    continue
                try:
                    window = game_window.find_window()
                    if window is None:
                        was_open = False
                        continue  # game not running: nothing to check
                    left, top, right, bottom = window
                    width, height = right - left, bottom - top

                    def grab(box):
                        shot = sct.grab({"left": left + box[0], "top": top + box[1],
                                         "width": box[2] - box[0], "height": box[3] - box[1]})
                        return Image.frombytes("RGB", shot.size, shot.rgb)

                    known = layout_module.remembered(width, height)
                    if known:
                        found = layout_module.check(grab, known)  # calibrated: just the one spot
                    elif time.time() - last_full_search >= 3:  # not calibrated yet: try every candidate, every 3 s
                        last_full_search = time.time()
                        found = layout_module.locate(grab, width, height)
                    else:
                        found = None
                    is_open = found is not None
                    if is_open and not was_open:
                        layout_module.remember(width, height, found)
                        time.sleep(SETTLE_S)
                        if layout_module.check(grab, found):  # still open after the animation
                            self.events.put(lambda: self.capture(auto=True))
                    was_open = is_open
                except Exception:
                    logger.exception("Auto-detect check failed")

    def capture(self, auto: bool = False):
        if self.busy:
            return
        self.busy = True
        self.set_status("Scoreboard detected, capturing..." if auto else "Capturing screenshot...")
        if self.capture_hidden:
            self._capture_now(auto)  # already hidden (overlay mode)
        elif self.set_capture_hidden(True):
            # Hide for just this screenshot, so the window can't cover the scoreboard; nothing changes on screen
            self.root.after(CAPTURE_HIDE_MS, lambda: self._capture_now(auto))
        else:
            # Older Windows: making the window fully transparent (instead of hiding it) avoids
            # stealing keyboard focus from the game
            self.root.attributes("-alpha", 0.0)
            self.root.after(CAPTURE_DELAY_MS, lambda: self._capture_now(auto))

    def _capture_now(self, auto: bool = False):
        try:
            path = capture_and_save_screenshot()
        except Exception as e:
            logger.exception("Screenshot failed")
            self.busy = False
            self.set_status(f"Screenshot failed: {e}")
            return
        finally:
            self.apply_overlay()  # back to normal: opacity, and visible to screen sharing unless overlay is on
        delete_old_screenshots()
        self.start_analysis(path, auto)

    def analyze_latest(self):
        if self.busy:
            return
        path = get_screenshot_path()
        if not path:
            self.set_status("No screenshots yet. Open the scoreboard in game first.")
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

    def start_analysis(self, path: str, auto: bool = False):
        self.set_status(f"Reading {os.path.basename(path)}...")
        threading.Thread(target=self._analyze, args=(path, auto), daemon=True).start()

    def _analyze(self, path: str, auto: bool = False):
        """Worker thread: OCR, then look everyone up. No tkinter calls here, only queued callables.
        (Not run_task: a lobby should arrive even if the user is browsing another page.)"""
        try:
            records = read_lobby(path, self.progress)
            if auto and records and records == self.last_records:
                # Menu reopened in the same lobby: nothing changed, so skip the API calls and the duplicate file
                os.remove(path)
                self.events.put(self.same_lobby)
                return
            results, parties = analyze_records(records, progress=self.progress, me=get_me())
            def matchup():
                if not any(r.get("is_me") for r in results):
                    return None
                try:
                    heroes = deadlock_api.fetch_heroes()
                    return build_matchup(results, {h["name"]: h["id"] for h in heroes},
                                         {h["id"]: h["name"] for h in heroes})
                except Exception:
                    logger.exception("Matchup failed")  # the lobby report is still useful without it
                    return None
            self.progress("Loading your matchup and avatars...")
            matchup, images = deadlock_api.parallel(
                matchup, lambda: self.avatars.download(r.get("avatar_url") for r in results))
            lobby = {"path": path, "records": records, "results": results, "parties": parties,
                     "matchup": matchup, "images": images, "time": time.time()}
            self.events.put(lambda: self.show_lobby(lobby))
        except Exception as e:
            logger.exception("Analysis failed")
            error = str(e)
            self.events.put(lambda: self.analysis_failed(error))

    def show_lobby(self, lobby):
        self.busy = False
        self.avatars.store(lobby.pop("images"))
        self.lobby = lobby
        self.last_records = lobby["records"]
        # Jump to the lobby (Back returns to wherever the user was); refresh it if already there
        self.navigate(LobbyPage, push=not isinstance(self.page, LobbyPage))
        watching = "watching for the scoreboard" if self.watching else f"{HOTKEY.upper()} for a new screenshot"
        self.set_status(f"{len(lobby['results'])} players  ·  {watching}")
        self.root.bell()  # audible cue when the report is ready while you're in game

    def same_lobby(self):
        self.busy = False
        self.set_status("Same lobby as before; nothing new  ·  watching for the scoreboard...")

    def analysis_failed(self, error: str):
        self.busy = False
        self.set_status(f"Something went wrong: {error}")

    def close(self):
        save_settings({"geometry": self.root.geometry(), "overlay": self.overlay.get()})  # merges, keeps "me"
        keyboard.unhook_all()
        self.root.destroy()


def main():
    setup_logger()
    try:
        # Its own taskbar identity, so Windows shows the app's icon instead of grouping it under Python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeadlockAnalyzer")
    except Exception:
        pass
    root = ctk.CTk()  # a CustomTkinter window: dark title bar on Windows
    # Optional: python app.py path/to/screenshot.png opens straight onto that screenshot
    AnalyzerApp(root, open_screenshot=sys.argv[1] if len(sys.argv) > 1 else None)
    root.mainloop()


if __name__ == "__main__":
    main()
