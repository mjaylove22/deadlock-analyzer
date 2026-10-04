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
green = easier), and the items most often bought by your hero against this enemy team.

## Ground rules

The tool works **only from screenshots** and public web data. It does not read game memory, inject code,
modify game files, automate input, or interact with anti-cheat in any way.

## How it works

1. **Capture**: the app (`app.py`) runs in the background and takes a screenshot when you press `Ctrl+Shift+D`.
2. **OCR**: `scoreboard_ocr.py` crops the scoreboard's player list and runs Tesseract once.
   Every scoreboard row is two lines, a Steam name and then `<Hero> Level N`, so each line containing
   a hero name and "Level" is paired with the line above it. Each line's vertical position decides the team.
3. **Lookup**: `player_lookup.py` searches the [Deadlock API](https://api.deadlock-api.com) for each Steam name
   and fetches per-hero stats.
   - Steam names aren't unique, so `identity.py` resolves the whole lobby together: unique names first,
     then candidates who are Steam friends with already-identified players, then hero history.
   - Friends on the same team are reported as a party.
   - Players whose name equals their hero (bots in bot lobbies) are skipped.

## Setup

1. Python 3.13+ and [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (installed to `C:\Program Files\Tesseract-OCR`).
2. `pip install -r requirements.txt`

## Usage

**App (no terminal needed):** double-click `Deadlock Analyzer.pyw` and leave it running. In game, just open
the Esc menu on the **PLAYERS** tab: **auto-detect** notices the scoreboard, captures it and shows the lobby
(a sound plays when it's ready). Reopening the menu in the same lobby doesn't redo the work.
`Ctrl+Shift+D` still captures on demand.

The app has pages, with a **Back** button (or Alt+Left) and the title as a link to **Home**:

| Page | What it shows |
|---|---|
| **Home** | Your account, the last lobby, the strongest heroes right now and your recent matches |
| **Lobby** | Both teams side by side, with your matchup at the bottom. Click any player to open their page |
| **Heroes** | Every hero's win rate, pick rate, games and KDA, for Normal or Street Brawl. Click a heading to sort |
| **My Stats** | Your own player page |
| **Player** | Avatar, rank, games and win rate per game mode, a sortable per-hero table (Normal or Street Brawl) and recent matches |
| **Search** | Type any Steam name in the top bar to see every account with that name, then click one |

- **Set your account:** search your Steam name, open your account and click **Set as my account**. You're
  then identified exactly in every lobby (even if others share your name) and your matchup appears.
- **Overlay** keeps the window semi-transparent and on top of the game (needs **borderless windowed**
  mode). The window asks Windows to leave it out of screen captures, so it never hides the scoreboard.
- Screenshots older than **7 days are deleted automatically** (except ones kept as test cases).

**Terminal:**

```bash
python player_lookup.py             # report for the latest screenshot (or pass a path)
python scoreboard_ocr.py            # debug view: raw OCR lines and parsed rows
python -m unittest discover -s tests -v
```

## Limitations

- Crop coordinates were measured on a **1920×1080** screen with a 6v6 scoreboard; other resolutions log a warning and will likely misread.
- OCR occasionally garbles a hero line; that player is skipped rather than guessed.
- A player whose account isn't in the Deadlock API's database can't be found by name at all (only
  identifying players by match ID, after the match, could fix that).
- Hero stats and badges count normal matches only (the API default); Street Brawl and bot matches aren't included.

## Project layout

```
app.py               desktop app: window, navigation, background tasks, capture and auto-detect
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
settings.py          settings.json: window layout and which account is you (stays on your PC)
insights.py          stats on the current hero and badge rules
report.py            wording shared by the terminal and the app
tests/               unit tests (API calls are mocked)
docs/DESIGN.md       design decisions and technology choices
utils/logger.py      logging to logs/app.log and the console
```

For the reasoning behind the design, see [docs/DESIGN.md](docs/DESIGN.md).

## License

[MIT](LICENSE). Not affiliated with or endorsed by Valve.
