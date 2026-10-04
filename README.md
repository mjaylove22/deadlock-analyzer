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

Both teams sit side by side, so a full 6v6 lobby fits on one screen without scrolling.

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

**App (no terminal needed):** double-click `Deadlock Analyzer.pyw`. In game, open the Esc menu on the
**PLAYERS** tab and press `Ctrl+Shift+D`. The report appears in the app window (a sound plays when it's ready).
Profile links are clickable.

- **Overlay mode** keeps the window semi-transparent and on top of the game. This needs the game in
  **borderless windowed** mode; the app never draws into the game itself.
- **Analyze latest** re-runs the report on the newest screenshot; **Open screenshot...** picks any saved one.
- **Search player:** type any Steam name to see every account with that name (avatar, rank, overall
  stats). A player the lookup couldn't find gets a **Search similar names** link.
- Progress shows while it works ("Looking up player 5/12..."), and each team header sums up the team
  ("party of 3 · 2 new on hero · best rank Oracle 6").
- Screenshots older than **7 days are deleted automatically** (except ones kept as test cases).
- The window remembers its size, position and overlay setting.

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
app.py               desktop app: hotkey, capture, report window, overlay mode
Deadlock Analyzer.pyw  double-click launcher (no console window)
screenshot_manager.py
scoreboard_ocr.py    screenshot -> [{"player", "hero", "team"}]
deadlock_api.py      thin client for the public Deadlock API
identity.py          which same-named account is which; party detection
player_lookup.py     whole-lobby lookup and command-line report
insights.py          stats on the current hero and badge rules
report.py            wording shared by the terminal and the app
tests/               unit tests (API calls are mocked)
docs/DESIGN.md       design decisions and technology choices
utils/logger.py      logging to logs/app.log and the console
```

For the reasoning behind the design, see [docs/DESIGN.md](docs/DESIGN.md).

## License

[MIT](LICENSE). Not affiliated with or endorsed by Valve.
