# Deadlock Analyzer

Reads the in-game scoreboard of Valve's **Deadlock** from a screenshot and looks up each player's public stats.

```
screenshot ──► OCR (Tesseract) ──► player / hero / team ──► public Deadlock API ──► favourite heroes, win rates
```

Example output (illustrative names and numbers):

```
=== ENEMY TEAM ===
  Party of 3: PlayerA + PlayerB + ExamplePlayer

ExamplePlayer  (playing Paradox)  [found]
    friends with PlayerA, PlayerB in this lobby
    https://steamcommunity.com/profiles/<steam-id>/
    Graves         20 matches  70% wins
    Mirage         18 matches  50% wins
    The Doorman    17 matches  47% wins
```

## Ground rules

The tool works **only from screenshots** and public web data. It does not read game memory, inject code,
modify game files, automate input, or interact with anti-cheat in any way.

## How it works

1. **Capture**: `main.py` saves a full-screen screenshot when you press `Ctrl+Shift+D`.
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

```bash
python main.py                      # run in the background; Ctrl+Shift+D captures a screenshot
python player_lookup.py             # analyse the latest screenshot (or pass a path)
python scoreboard_ocr.py            # debug view: raw OCR lines and parsed rows
python -m unittest discover -s tests -v
```

Take the screenshot with the Esc menu open on the **PLAYERS** tab.

## Limitations

- Crop coordinates were measured on a **1920×1080** screen with a 6v6 scoreboard; other resolutions log a warning and will likely misread.
- OCR occasionally garbles a hero line; that player is skipped rather than guessed.

## Project layout

```
main.py              hotkey screenshot capture
screenshot_manager.py
scoreboard_ocr.py    screenshot -> [{"player", "hero", "team"}]
deadlock_api.py      thin client for the public Deadlock API
identity.py          which same-named account is which; party detection
player_lookup.py     whole-lobby lookup and command-line report
tests/               unit tests (API calls are mocked)
docs/DESIGN.md       design decisions and technology choices
utils/logger.py      logging to logs/app.log and the console
```

For the reasoning behind the design, see [docs/DESIGN.md](docs/DESIGN.md).

## License

[MIT](LICENSE). Not affiliated with or endorsed by Valve.
