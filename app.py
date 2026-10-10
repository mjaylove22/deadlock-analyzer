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
import subprocess
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
import paths
import updater
from version import __version__, is_newer
from matchups import build_matchup
from player_lookup import analyze_records, read_lobby
from scoreboard_ocr import TESSERACT_INSTALL, find_tesseract, read_match_id_file
import end_screen
from match_review import MatchUnavailable, get_summary
from postgame import CHECK_EVERY_S, PostGame, is_our_match, link_players, remember_end_screen
import game_window
import layout as layout_module
from screenshot_manager import (SCREENSHOT_DIR, capture_and_save_screenshot, delete_old_screenshots, get_screenshot_path,
                                save_end_screen)
from settings import get_me, get_preferences, guessed_me, load_settings, save_settings, update_me_guess
from ui.pages import (CoachPage, HeroesPage, PatchesPage, HeroPage, HomePage, ItemsPage, LobbyPage, MatchPage, MatchupPage, PlayerPage,
                      SearchPage, SettingsPage, SetupPage)
from ui import images, theme
from ui.theme import COLORS, FONT, HEADING_FONT, choose_text_size, choose_theme, label, setup_styles, switch
from ui.widgets import AvatarCache, hide_tooltip
from utils.logger import setup_logger

logger = logging.getLogger(__name__)

HOTKEY = "ctrl+shift+d"
POLL_MS = 100
CAPTURE_DELAY_MS = 150   # time for Windows to repaint after the window turns invisible (older Windows only)
CAPTURE_HIDE_MS = 50     # time for Windows to apply "hide from capture" before the app's own screenshot
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11  # SetWindowDisplayAffinity modes
# For pop_up(): window positions that change the stacking order without moving, resizing or activating
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
SW_SHOWNOACTIVATE = 4
OVERLAY_ALPHA = 0.9
WATCH_INTERVAL_S = 1.0   # how often auto-detect checks for the scoreboard (one check takes ~6 ms)
SETTLE_S = 0.5           # after the scoreboard appears, wait for the menu animation before capturing
END_CHECK_INTERVAL_S = 2.0  # how often the end-of-match screen is looked for (one check ~8 ms)
LOBBY_MATCH_ID_MAX_AGE_S = 90 * 60  # a lobby's match ID stands in for an unreadable end screen this long
MATCHUP_AFTER_MS = 30_000  # a new lobby turns into the full matchup after this long, unless the user moved on
NAV_TABS = [("lobby", "Lobby"), ("heroes", "Heroes"), ("items", "Items"), ("patches", "Patches"), ("mystats", "My Stats"), ("coach", "Coach"), ("settings", "Settings")]
ICON_PATH = paths.resource("assets", "icon.ico")


class AnalyzerApp:
    def __init__(self, root: tk.Tk, open_screenshot: str = None):
        self.root = root
        self.events = queue.Queue()  # callables from other threads, run on the main thread
        self.busy = False            # a capture/analysis is in progress
        self.focus_at_capture = None  # title of the focused window when a capture started
        self.lobby = None            # the latest lobby: results, parties, matchup, path, time
        self.last_records = None     # OCR'd lobby shown last, to skip re-analysing the same one
        self.post_game = None        # PostGame: the finished match whose data the app is waiting for
        self.cache: Dict[Any, Any] = {}
        self.avatars = AvatarCache()
        self.history = []            # pages to go Back to: (page class, options)
        self.current = None
        self.page = None
        self.page_token = 0          # bumped on every navigation; stale task results are dropped
        self.update_tag = None       # a newer release's tag ("v0.2.1"), shown on the Home page
        self.update_release = None   # its {"tag", "url", "sha256"} for Update now (updater.py)

        settings = load_settings()
        choose_theme(settings.get("theme", "dark"))
        choose_text_size(root, settings.get("text_size", "normal"))
        self.overlay =tk.BooleanVar(value=settings.get("overlay", False))
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
            # An .ico has a sharp image for every size Windows asks for. Setting one also stops
            # CustomTkinter from putting its own icon on the window 200 ms after start.
            root.iconbitmap(default=ICON_PATH)
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
        if paths.INSTALLED:  # from source, updating is a git pull, not an installer
            threading.Thread(target=self._check_for_update, daemon=True).start()

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
        # The bar keeps its normal size with Large text (it was already full; the search box gave way): these
        # sizes are divided here and multiplied back by the Large scaling
        bar_size = lambda points: round(points / theme.text_scale)  # noqa: E731
        title = tk.Frame(bar, bg=COLORS["surface"], cursor="hand2")
        title.pack(side="left", padx=(12, 6))
        label(title, "DEADLOCK", size=bar_size(14), heading=True, bg="surface", cursor="hand2").pack(side="left")
        label(title, " ANALYZER", size=bar_size(14), heading=True, color="accent", bg="surface", cursor="hand2").pack(side="left")
        for widget in (title, *title.winfo_children()):
            widget.bind("<Button-1>", lambda event: self.navigate(HomePage))

        self.tabs = {}
        for key, text in NAV_TABS:
            holder = tk.Frame(bar, bg=COLORS["surface"])
            holder.pack(side="left", padx=(14 if key == "lobby" else 2, 0))
            tab = label(holder, text, size=bar_size(11), color="dim", bg="surface", cursor="hand2", padx=10, pady=4)
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
        hide_tooltip(now=True)  # its widget is gone, so it would never get a "mouse left" event
        self.page = page_class(self, self.container, **options)
        self.page.frame.pack(fill="both", expand=True)
        self.back_button.configure(state="normal" if self.history else "disabled")
        for key, (tab, underline) in self.tabs.items():
            active = key == self.page.nav
            tab.config(fg=COLORS["text"] if active else COLORS["dim"],
                       font=(HEADING_FONT if active else FONT, round(11 / theme.text_scale)))  # the bar keeps its size
            underline.config(bg=COLORS["accent"] if active else COLORS["surface"])
        self.fit_window()

    def back(self):
        if self.history:
            page_class, options = self.history.pop()
            self.navigate(page_class, push=False, **options)

    def open_tab(self, key: str):
        {"lobby": self.open_lobby, "heroes": self.open_heroes, "items": self.open_items, "mystats": self.open_my_stats,
         "coach": lambda: self.navigate(CoachPage), "patches": lambda: self.navigate(PatchesPage), "settings": lambda: self.navigate(SettingsPage)}[key]()

    def open_lobby(self):
        self.navigate(LobbyPage)

    def open_heroes(self, mode: str = "Normal", band: str = "All ranks", push: bool = True):
        self.navigate(HeroesPage, push=push, mode=mode, band=band)

    def open_items(self, mode: str = "Normal", band: str = "All ranks", category: str = "All", push: bool = True):
        self.navigate(ItemsPage, push=push, mode=mode, band=band, category=category)

    def open_hero(self, hero: str, mode: str = "Normal", band: str = "All ranks", push: bool = True, view: str = "Stats"):
        self.navigate(HeroPage, push=push, hero=hero, mode=mode, band=band, view=view)

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

    def open_match(self, match_id: int, view: str = "Overview"):
        self.navigate(MatchPage, match_id=match_id, view=view)

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
                if isinstance(e, MatchUnavailable):  # expected (not stored yet) and explained on the page
                    logger.info("Match not available: %s", e)
                else:
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
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                event()
            except Exception:  # one page failing to draw mustn't stop every later result from arriving
                logger.exception("Showing a result failed")
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

    def _check_for_update(self):
        """Background: is there a newer release on GitHub? Its answer is kept on disk for 6 hours, so
        this is one small request now and then (GitHub allows 60 an hour without an account)."""
        try:
            release = deadlock_api.disk_cached("latest_release_v2", updater.latest_release, max_age=6 * 3600)
        except Exception as e:  # offline, or no release yet (404): nothing to say
            logger.info(f"Couldn't check for updates ({e})")
            return
        if is_newer(release["tag"]):
            logger.info(f"Update available: {release['tag']}")
            self.events.put(lambda: self.show_update(release))

    def show_update(self, release: Dict[str, Any]):
        self.update_tag, self.update_release = release["tag"], release
        if isinstance(self.page, HomePage):
            self.navigate(HomePage, push=False)

    def update_now(self):
        """Download the newest installer, check its SHA-256, run it and close so it can replace this app's
        files; it opens the app again when done. Not while Deadlock runs: the app would vanish mid-match.
        Its own thread, not run_task: leaving the page mustn't drop a finished download."""
        if game_window.find_window():
            self.set_status("Close Deadlock first: updating closes this app for a few seconds.")
            return
        release, shown = self.update_release, [-1]
        self.set_status("Downloading the update...")

        def progress(done, total):
            if total and done * 10 // total != shown[0]:  # every 10%, not every chunk
                shown[0] = done * 10 // total
                self.progress(f"Downloading the update: {done / 1e6:.0f} of {total / 1e6:.0f} MB")

        def work():
            try:
                path = updater.download(release, progress)
            except Exception as e:  # network, disk, or a checksum mismatch: say so, keep running
                logger.warning(f"Update failed: {e}")
                self.events.put(lambda: self.set_status(f"The update didn't work ({e}). Try again, or use Download update."))
                return
            self.events.put(lambda: (updater.install(path), self.close()))
        threading.Thread(target=work, daemon=True).start()

    def hero_names_by_id(self) -> Dict[int, str]:
        names = {h["id"]: h["name"] for h in deadlock_api.fetch_heroes()}
        if self.cache.get("heroes", names) != names:
            self.cache.clear()  # a new hero: the session's tier lists were made without him
        self.cache["heroes"] = names
        return names

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
        was_open = was_end = False
        last_full_search = last_end_check = 0.0
        with mss.mss() as sct:  # mss objects can't be shared between threads, so this thread has its own
            while True:
                time.sleep(WATCH_INTERVAL_S)
                if not self.watching or self.busy:
                    continue
                try:
                    window = game_window.find_window()
                    if window is None:
                        was_open = was_end = False
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
                    if time.time() - last_end_check >= END_CHECK_INTERVAL_S:
                        last_end_check = time.time()
                        end_layout = known or layout_module.candidates(width, height)[0]
                        is_end = not is_open and end_screen.is_end_screen(grab, end_layout)
                        if is_end and not was_end:
                            shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
                            image = Image.frombytes("RGB", shot.size, shot.rgb)
                            self.events.put(lambda image=image, end_layout=end_layout: self.match_over(image, end_layout))
                        was_end = is_end
                except Exception:
                    logger.exception("Auto-detect check failed")

    def capture(self, auto: bool = False):
        if self.busy:
            return
        self.busy = True
        self.focus_at_capture = (game_window.focused_window_title(), game_window.find_window() is not None)
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
        logger.info(f"{'Auto-detect' if auto else 'Manual'} capture: {os.path.basename(path)}")
        self.start_analysis(path, auto, from_game=True)

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
        path = filedialog.askopenfilename(title="Open a scoreboard screenshot", initialdir=SCREENSHOT_DIR,
                                          filetypes=[("PNG screenshots", "*.png"), ("All files", "*.*")])
        if path:
            self.busy = True
            self.start_analysis(path)

    def start_analysis(self, path: str, auto: bool = False, from_game: bool = False):
        """from_game: captured in game (auto-detect or the hotkey), not opened from a file."""
        if not find_tesseract():
            self.busy = False
            self.navigate(HomePage)  # its banner offers to install it
            self.set_status("Can't read the screenshot: the Tesseract OCR engine isn't installed (see the Home page).")
            return
        self.set_status(f"Reading {os.path.basename(path)}...")
        threading.Thread(target=self._analyze, args=(path, auto, from_game), daemon=True).start()

    def _analyze(self, path: str, auto: bool = False, from_game: bool = False):
        """Worker thread: OCR, then look everyone up. No tkinter calls here, only queued callables.
        (Not run_task: a lobby should arrive even if the user is browsing another page.)"""
        try:
            records = read_lobby(path, self.progress)
            if auto and records and records == self.last_records:
                # Menu reopened in the same lobby: nothing changed, so skip the API calls and the duplicate file
                os.remove(path)
                self.events.put(self.same_lobby)
                return
            # The match ID (bottom right) is read while the players are looked up, so it costs no time
            (results, parties), match_id = deadlock_api.parallel(
                lambda: analyze_records(records, progress=self.progress, me=get_me()),
                lambda: self.read_match_id(path))
            def matchup():
                if not any(r.get("is_me") for r in results):
                    return None
                try:
                    heroes = deadlock_api.fetch_heroes()
                    result = build_matchup(results, {h["name"]: h["id"] for h in heroes},
                                           {h["id"]: h["name"] for h in heroes})
                    if result:
                        assets.load_many((item.get("image") for item in result["items"]), assets.ITEM_MAX_SIDE)
                    return result
                except Exception:
                    logger.exception("Matchup failed")  # the lobby report is still useful without it
                    return None
            self.progress("Loading your matchup and avatars...")
            matchup, images = deadlock_api.parallel(
                matchup, lambda: self.avatars.download(r.get("avatar_url") for r in results))
            if match_id:
                logger.info(f"Match ID read from the scoreboard: match {match_id}")
            lobby = {"path": path, "records": records, "results": results, "parties": parties,
                     "matchup": matchup, "images": images, "time": time.time(), "from_game": from_game,
                     "match_id": match_id}
            self.events.put(lambda: self.show_lobby(lobby))
        except Exception as e:
            logger.exception("Analysis failed")
            error = str(e)
            self.events.put(lambda: self.analysis_failed(error))

    def read_match_id(self, path: str):
        """The screenshot's match ID, or None: the lobby is still worth showing without it."""
        try:
            return read_match_id_file(path)
        except Exception:
            logger.exception("Couldn't read the match ID")
            return None

    def check_focus_kept(self, stage: str, last: bool = True):
        """Log it if keyboard focus moved, or the game was minimised, since the capture started: the app
        must never pull you out of the game. Checked when the reading is done and again a second after
        the result is shown, so the log says which step did it. Warns once per capture."""
        if self.focus_at_capture is None:
            return
        before, game_was_up = self.focus_at_capture
        after = game_window.focused_window_title()
        minimised = game_was_up and game_window.find_window() is None
        if after != before or minimised:
            # The Esc menu frees the pointer, so a click on another window moves focus too: where the
            # pointer is tells that apart from a program taking focus
            logger.warning(f"Keyboard focus moved {stage}: {before!r} -> {after!r}"
                           + (" (the game window is minimised)" if minimised else "")
                           + f"; mouse pointer over {game_window.window_under_mouse()!r}")
            last = True
        if last:
            self.focus_at_capture = None

    def check_focus_after_showing(self):
        self.check_focus_kept("while reading the screenshot", last=False)
        self.root.after(1000, lambda: self.check_focus_kept("while showing the result"))

    def show_lobby(self, lobby):
        self.busy = False
        self.check_focus_after_showing()
        self.avatars.store(lobby.pop("images"))
        self.lobby = lobby
        self.last_records = lobby["records"]
        # Jump to the lobby (Back returns to wherever the user was); refresh it if already there
        self.navigate(LobbyPage, push=not isinstance(self.page, LobbyPage))
        watching = "watching for the scoreboard" if self.watching else f"{HOTKEY.upper()} for a new screenshot"
        self.set_status(f"{len(lobby['results'])} players  ·  {watching}")
        if lobby["from_game"] and not get_me():  # a screenshot opened from a file could be anyone's lobby
            guess = update_me_guess(load_settings().get("me_guess"), lobby["results"], lobby.get("match_id"))
            save_settings({"me_guess": guess})
            if guessed_me(guess):
                self.set_status(f"{len(lobby['results'])} players  ·  the Home page asks which player is you")
        prefs = get_preferences()
        # Only for a lobby captured in game: an opened screenshot is someone wanting to look at that lobby.
        # Any navigation (a click, a new lobby) cancels it.
        if lobby["from_game"] and lobby.get("matchup") and prefs["show_matchup"]:
            token = self.page_token
            self.root.after(MATCHUP_AFTER_MS, lambda: self.navigate(MatchupPage) if token == self.page_token else None)
        if prefs["sound"]:
            self.root.bell()  # audible cue when the report is ready while you're in game
        if lobby["from_game"] and prefs["pop_up"]:
            self.pop_up()

    def same_lobby(self):
        logger.info("Same lobby as the last capture; nothing to look up")
        self.busy = False
        self.check_focus_after_showing()
        prefs = get_preferences()
        if prefs["reshow_same_lobby"] and self.lobby:
            self.navigate(LobbyPage, push=not isinstance(self.page, LobbyPage))
            if prefs["pop_up"]:
                self.pop_up()
        self.set_status("Same lobby as before, so no new lookups  ·  watching for the scoreboard")

    def pop_up(self):
        """Bring the window above the others without taking keyboard focus from the game (a window
        that grabs focus can minimise a fullscreen game). Keeps it on top only in overlay mode."""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.root.winfo_id())
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)  # un-minimise without activating
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
            if not self.overlay.get():
                user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        except Exception:
            logger.exception("Could not bring the window to the front")

    def install_tesseract(self):
        """Install the OCR engine with winget, in its own console window so the user sees it (and
        Windows' permission prompt), then notice when it's there."""
        try:
            subprocess.Popen(TESSERACT_INSTALL, creationflags=subprocess.CREATE_NEW_CONSOLE)
        except OSError as e:
            logger.warning(f"Couldn't start winget ({e})")
            self.set_status("Couldn't start winget. Install Tesseract from github.com/UB-Mannheim/tesseract/wiki instead.")
            return
        self.set_status("Installing Tesseract OCR... (allow it if Windows asks)")
        self.root.after(3000, self.wait_for_tesseract)

    def wait_for_tesseract(self, tries: int = 0):
        if find_tesseract():
            logger.info("Tesseract installed")
            self.set_status("Tesseract installed: the app is ready.")
            if isinstance(self.page, HomePage):
                self.page.reload()
        elif tries < 200:  # 10 minutes
            self.root.after(3000, lambda: self.wait_for_tesseract(tries + 1))

    # ---- after a match --------------------------------------------------------------------

    def match_over(self, image, end_layout):
        """The end-of-match screen appeared (after a match, or a past match opened in game): read
        its scoreboard and match ID (worker thread: OCR, ~2 s)."""
        logger.info("End-of-match screen detected")
        if not get_preferences()["post_game_review"]:
            return
        lobby = self.lobby
        recent = lobby if lobby and lobby.get("from_game") and time.time() - lobby["time"] < LOBBY_MATCH_ID_MAX_AGE_S else None
        lobby_id = recent.get("match_id") if recent else None
        names = list(self.hero_names_by_id().values())

        def work():
            read, screen = None, None
            try:
                save_end_screen(image)
                read = end_screen.read_end_match_id(image, end_layout)
                screen = end_screen.read_scoreboard(image, end_layout, names)
            except Exception:
                logger.exception("Couldn't read the end-of-match screen")
            if read and lobby_id and read != lobby_id:
                logger.warning(f"End screen says match {read}, the lobby said match {lobby_id}; using the end screen")
            match_id = read or lobby_id
            if screen:
                # The lobby belongs to this match if its ID matches (or no ID was read at all)
                same_match = recent and (not read or read == lobby_id)
                link_players(screen, get_me(), recent["results"] if same_match else [])
                screen["match_id"] = match_id
                remember_end_screen(screen, match_id)  # for Home's recent matches and session summary
            logger.info(f"Match over: match {match_id} (from {'the end screen' if read else 'the lobby' if match_id else 'nowhere'}); "
                        f"scoreboard: {len(screen['players']) if screen else 0} players read")
            self.events.put(lambda: self.start_post_game(match_id, screen))
        threading.Thread(target=work, daemon=True).start()

    def start_post_game(self, match_id, screen=None):
        if not match_id and not screen:
            self.set_status("Match over, but the end screen couldn't be read.")
            return
        if match_id and self.post_game and self.post_game.match_id == match_id:
            return  # the same match's end screen again (e.g. after looking at another tab)
        self.post_game = PostGame(match_id, screen=screen)
        self.set_status("Match over" + (f" · getting the full data for match {match_id}..." if match_id else ""))
        self.open_match(match_id or 0, "Performance")  # 0: the screen's numbers only
        if get_preferences()["pop_up"]:
            self.pop_up()
        self.check_post_game(self.post_game)

    def check_post_game(self, game):
        """One check for the finished match's data; repeats every minute until it's ready. Each chain
        of checks belongs to one match and ends when a newer match ends."""
        if game is not self.post_game or game.done or not game.match_id:
            return
        if game.expired(time.time()):
            game.done = True
            logger.info(f"Gave up waiting for match {game.match_id}")
            self.set_status(f"Match {game.match_id} still isn't available. Try again later from the Lobby or Home page.")
            self.refresh_post_game_page(game)
            return
        names = self.hero_names_by_id()

        def work():
            summary = game.attempt(lambda match_id, steam: get_summary(match_id, names, steam))
            self.events.put(lambda: self.post_game_checked(game, summary))
        threading.Thread(target=work, daemon=True).start()

    def post_game_checked(self, game, summary):
        if game is not self.post_game:
            return  # a newer match has ended since
        if summary is None:
            logger.info(f"Match {game.match_id} not ready (check {game.checks}): {game.last_error}")
            self.refresh_post_game_page(game)
            self.root.after(CHECK_EVERY_S * 1000, lambda: self.check_post_game(game))
            return
        lobby = self.lobby
        lobby_heroes = [r["hero"] for r in lobby["records"]] if lobby and lobby.get("match_id") == game.match_id else []
        if not is_our_match(summary, get_me(), lobby_heroes):
            logger.warning(f"Match {game.match_id} doesn't have your account or the lobby's heroes: a misread ID?")
        logger.info(f"Match {game.match_id} ready after {game.checks} check(s), {game.steam_tries} from Steam")
        self.set_status(f"Your match review is ready · match {game.match_id}")
        if get_preferences()["sound"]:
            self.root.bell()
        self.refresh_post_game_page(game)

    def refresh_post_game_page(self, game):
        """Update the waiting match page, if that's where the user is: the whole page once the data is
        ready (or the app gave up), else just its progress line (a full redraw every check flashes)."""
        if isinstance(self.page, MatchPage) and self.page.match_id == game.match_id:
            if game.done:
                self.page.reload()
            else:
                self.page.show_progress(game)

    def analysis_failed(self, error: str):
        self.busy = False
        self.check_focus_after_showing()
        self.set_status(f"Something went wrong: {error}")

    def close(self):
        save_settings({"geometry": self.root.geometry(), "overlay": self.overlay.get()})  # merges, keeps "me"
        keyboard.unhook_all()
        self.root.destroy()


def main():
    setup_logger()
    logger.info(f"Deadlock Analyzer {__version__} starting ({'installed' if paths.INSTALLED else 'from source'})")
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
