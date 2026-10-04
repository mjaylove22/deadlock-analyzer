# Deadlock Analyzer

Reads the in-game scoreboard of Valve's **Deadlock** from a screenshot and looks up each player's public stats.

```
screenshot ──► OCR (Tesseract) ──► player / hero / team ──► public Deadlock API ──► favourite heroes, win rates
```

For every player in the lobby, the app shows:

- **Stats on the hero they're playing right now:** games, win rate, KDA, damage per minute
- **Badges:** ONE-TRICK, ON MAIN, COMFORT PICK, NEW ON HERO, FIRST GAME ON HERO, HIGH WR / LOW WR, VETERAN
- **Rank**, in the game's rank colours
- **Parties:** friends queued together on the same team share a colour
- **Their Steam avatar**, **most-played heroes** (by games played, with win rate) and a clickable profile
- **ID UNSURE** when the account match is a close call, so you know when not to trust it
- **NAME FIXED** when OCR misread one character of a name and the real Steam name was found

Both teams sit side by side, so a full 6v6 lobby fits on one screen without scrolling. Heroes are shown
with their portraits and ranks with their emblems (downloaded once, then kept in a local `cache/` folder).

**Your matchup** (once the app knows which account is yours) appears in a strip at the bottom: your
hero's win rate against each enemy hero, compared with your hero's average (red = harder than usual,
green = easier), and the items most often bought by your hero against this enemy team. Items are shown
with their in-game icons; hover over one for its cost and how often it's bought.

## Ground rules

The tool works **only from screenshots** and public web data. It does not read game memory, inject code,
modify game files, automate input, or interact with anti-cheat in any way.

## How it works

1. **Capture**: the app (`app.py`) runs in the background and takes a screenshot when the scoreboard opens
   (auto-detect) or when you press `Ctrl+Shift+D`.
2. **OCR**: `scoreboard_ocr.py` crops the scoreboard's player list and runs Tesseract once.
   Every scoreboard row is two lines, a Steam name and then `<Hero> Level N`, so each line containing
   a hero name and "Level" is paired with the line above it. Each line's vertical position decides the team.
3. **Lookup**: `player_lookup.py` searches the [Deadlock API](https://api.deadlock-api.com) for each Steam name
   and fetches per-hero stats.
   - Steam names aren't unique, so `identity.py` resolves the whole lobby together: unique names first,
     then candidates who are Steam friends with already-identified players, then hero history.
   - Friends on the same team are reported as a party.
   - Players whose name equals their hero (bots in bot lobbies) are skipped.

## Install

**Windows 10/11:** download `DeadlockAnalyzer-Setup-<version>.exe` from
[Releases](https://github.com/mjaylove22/deadlock-analyzer/releases) and run it. Python and Tesseract are
included, so nothing else is needed (28 MB download, 87 MB installed). It installs for your Windows
user only (no admin prompt) and adds Start-menu and desktop shortcuts. Uninstalling it from Windows'
*Installed apps* list also removes its settings, cache and screenshots.

Windows may show **"Windows protected your PC"**, because the installer isn't code-signed: click
**More info**, then **Run anyway**.

**From source:**
1. Python 3.13+ and [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (installed to `C:\Program Files\Tesseract-OCR`).
2. `pip install -r requirements.txt`
3. Optional: `python make_shortcut.py` puts a **Deadlock Analyzer** icon on your desktop.

**Building the installer:** `pip install -r requirements-dev.txt`, `winget install JRSoftware.InnoSetup`,
then `python installer/build.py`. The installer appears in `dist/`.

## Usage

**App:** start it from the desktop or Start-menu icon (from source: `Deadlock Analyzer.pyw`) and leave it running. In game, just open
the Esc menu on the **PLAYERS** tab: **auto-detect** notices the scoreboard, captures it and shows the lobby
(a sound plays when it's ready). Reopening the menu in the same lobby doesn't redo the work.
`Ctrl+Shift+D` still captures on demand.

The app has pages, with a **Back** button (or Alt+Left) and the title as a link to **Home**:

| Page | What it shows |
|---|---|
| **Home** | Your account, the last lobby, the strongest heroes right now and your recent matches |
| **Lobby** | Both teams side by side, with your matchup at the bottom. Click any player to open their page |
| **Matchup** | Opened from the Lobby's matchup strip: an overall read of your hero against this team, your hero with each teammate's hero, and a card per enemy (toughest first) with the player's record on their hero, your win rate against that hero overall and in lane (as bars from your usual), your K/D/A against them, and the items that help most against them. Plus items that win more than usual against the whole team |
| **Settings** | What happens when the scoreboard opens in game (bring the app to the front without taking focus from the game, a sound, show the lobby again when nothing changed) and what the lobby cards show (rank, current-hero stats, badges, most-played heroes, your matchup) |
| **Heroes** | Every hero's win rate, pick rate, ban share, games and KDA, for Normal or Street Brawl and any rank band, with a 12-week trend line per hero and whether it's rising, falling or steady. Hover a trend line for each week's numbers; click a heading to sort, or a hero to open it |
| **Hero** | A hero's win rate over the last 12 weeks (hover for each week), its best and toughest matchups (against its own average), its most-bought items with icons, and its win rate at every rank, by mode and rank band |
| **My Stats** | Your own player page |
| **Player** | Avatar, rank, games and win rate for ranked, unranked and Street Brawl, who they play with most, a sortable per-hero table (All / Ranked / Unranked / Street Brawl) and recent matches with their type (click one for its review) |
| **Match review** | A finished match: victory or defeat, your K/D/A, net worth, damage, healing and last hits with your place in the lobby and how they compare with your usual on that hero, your final build, the net-worth lead over time and both scoreboards |
| **Search** | Type any Steam name in the top bar to see every account with that name, or a **match ID** to open its review |

- **Set your account:** search your Steam name, open your account and click **Set as my account**. You're
  then identified exactly in every lobby (even if others share your name) and your matchup appears.
- **Overlay** keeps the window semi-transparent and on top of the game (needs **borderless windowed**
  mode). While overlay is on, the window is hidden from screen capture so it never covers the
  scoreboard in its own screenshots; that also hides it from Discord/OBS streams. With overlay off, it
  shows up in screen sharing normally.
- Screenshots older than **7 days are deleted automatically** (except ones kept as test cases).
- **Light:** ~3,400 lines of Python, about 70 MB of memory, the window opens in ~0.4 s, and the local
  cache stays around 2 MB. API answers are reused for a few minutes, so going Back or revisiting a
  page is instant. The biggest piece is the Tesseract OCR engine (~116 MB, installed separately).

**Terminal:**

```bash
python player_lookup.py             # report for the latest screenshot (or pass a path)
python scoreboard_ocr.py            # debug view: raw OCR lines and parsed rows
python -m unittest discover -s tests -v
```

## Limitations

- **Screen sizes:** built and verified on real 1920x1080 screenshots. Other sizes (1440p, 4K, ultrawide,
  16:10, smaller screens) are located automatically and tested with **simulated** screenshots; real
  screenshots at those sizes are still needed to confirm how Deadlock lays out its UI there.
- Works on any monitor and in windowed mode (it captures the Deadlock window itself).
- OCR occasionally garbles a line; that player is skipped rather than guessed. One misread letter in a
  hero name, and dropped or added spaces in names, are handled.
- Match reviews appear a while after a match ends (bot matches never do). Matches the API hasn't stored
  yet are fetched from Steam, which is limited to 3 an hour, so failed matches aren't retried for 10 minutes.
- **Ban share** is each hero's part of all recorded bans, not a ban rate: the API gives ban counts but not
  how many matches they came from. The ranking is the same either way.
- Hero stats and badges count normal matches only (the API default); Street Brawl and bot matches aren't included.

## Project layout

```
app.py               desktop app: window, navigation, background tasks, capture and auto-detect
layout.py            finds the scoreboard at any screen size (checked by the PLAYERS-tab detector)
game_window.py       finds the Deadlock window (any monitor, windowed or not)
ui/                  theme, images, reusable widgets (cards, sortable tables) and the pages
assets.py            hero portraits and rank emblems, cached on disk
scoreboard_detector.py  spots the open scoreboard from a tiny screen grab
Deadlock Analyzer.pyw  double-click launcher (no console window)
screenshot_manager.py
scoreboard_ocr.py    screenshot -> [{"player", "hero", "team"}]
deadlock_api.py      thin client for the public Deadlock API
identity.py          which same-named account is which; party detection
player_lookup.py     whole-lobby lookup and command-line report
matchups.py          your hero vs the enemy heroes, and popular items against them
profiles.py          player pages (per-hero stats, recent matches, modes) and the hero tier list
match_review.py      post-game review: condenses a match's data, saved on disk
settings.py          settings.json: window layout and which account is you (stays on your PC)
paths.py             where shipped files and the app's own files live, from source or installed
version.py           the version number, shown on the Home page and used by the installer
installer/           build.py (Tesseract trim + PyInstaller + Inno Setup), setup.iss, licence notices
make_shortcut.py     creates the desktop shortcut
insights.py          stats on the current hero and badge rules
report.py            wording shared by the terminal and the app
tests/               unit tests (API calls are mocked)
docs/DESIGN.md       design decisions and technology choices
utils/logger.py      logging to logs/app.log and the console
```

For the reasoning behind the design, see [docs/DESIGN.md](docs/DESIGN.md).

## License

[MIT](LICENSE). Not affiliated with or endorsed by Valve.
