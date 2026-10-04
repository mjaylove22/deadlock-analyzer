# Design notes

How Deadlock Analyzer works, why each piece of technology is there, and the evidence behind the main design decisions.

## 1. The problem

Deadlock's Esc menu shows a scoreboard: for each of the 12 players, a hero portrait, the player's **Steam display name**, and a smaller line `<Hero> Level N`. There is no official API for "who is in my current match", so the only legitimate source is what is on screen.

Goal: turn a screenshot of that scoreboard into structured data, then enrich it with each player's public stats.

```
screenshot ──► crop ──► Tesseract OCR ──► lines + positions ──► parse rows ──► [{"player", "hero", "team"}]
                                                                                        │
                                       Deadlock API: Steam name search ──► hero stats ◄─┘
```

**Ground rules:** screenshot and public web data only. No game memory reading, code injection, game-file changes, input automation or anti-cheat interaction. This keeps the tool clearly on the right side of the game's rules and makes it safe to run.

## 2. Components

| File | Responsibility | Depends on |
|---|---|---|
| `main.py` | Background hotkey (`Ctrl+Shift+D`) that saves a full-screen screenshot | `keyboard`, `screenshot_manager` |
| `screenshot_manager.py` | Capture and save screenshots; find the latest one | `mss` |
| `scoreboard_ocr.py` | Screenshot → player/hero/team records. Pure parsing logic is separated from OCR so it can be unit-tested without images | `pytesseract`, `Pillow`, `deadlock_api` (hero names) |
| `deadlock_api.py` | Thin client: every HTTP call to the Deadlock API lives here | standard library only |
| `player_lookup.py` | Name → Steam account → hero stats, plus the command-line report | `scoreboard_ocr`, `deadlock_api` |
| `tests/` | Unit tests for parsing and lookup logic; the API is mocked | `unittest` (standard library) |
| `utils/logger.py` | One place to configure logging to `logs/app.log` and the console | standard library |

Each module has one job, and data flows one way: OCR knows nothing about stats, and the API client knows nothing about screenshots. That separation is what makes each part testable on its own.

## 3. Technology choices

| Technology | Used for | Why this one |
|---|---|---|
| **Python** | Everything | Strong ecosystem for OCR, imaging and HTTP; fast to iterate on an exploratory problem like OCR tuning |
| **Tesseract** (via `pytesseract`) | Text recognition | Free, offline, mature open-source OCR engine. Running locally means no screenshots leave the machine. `pytesseract` is a thin Python wrapper around the Tesseract executable |
| **Pillow** | Cropping images | The standard Python imaging library; cropping is all that's needed |
| **mss** | Screenshots | Fast, dependency-free screen capture |
| **keyboard** | Global hotkey | Lets the capture run in the background while the game has focus. It only *listens* for a key combination; it never sends input to the game |
| **urllib** (standard library) | HTTP requests | Only three GET requests are needed. Avoiding `requests` keeps the dependency list short; `deadlock_api.py` is the only file that would change if that ever stopped being true |
| **[Deadlock API](https://api.deadlock-api.com)** | Steam name search, hero stats, hero list | Free, public, documented with an OpenAPI spec, and no API key needed for the endpoints used (rate limit: 100 requests/s per IP). Using a documented API instead of scraping sites like Tracklock is more reliable and respects those sites |
| **unittest + unittest.mock** (standard library) | Tests | Built into Python, so nothing extra to install. `mock` replaces network calls so tests are fast, offline and give the same result every run |
| **logging** (standard library) | Diagnostics | Log levels and a log file instead of scattered `print` debugging |
| **Git + GitHub** | Version control | History, safe experimentation, and sharing |

## 4. Key design decisions (and the evidence behind them)

OCR is sensitive to small changes, so every decision below was made by trying alternatives against a real screenshot, not by assumption.

### 4.1 Crop tightly, upscale, and binarize with a measured cutoff
- **Crop** `(1560, 110, 1875, 940)` on a 1920×1080 screen. The left edge starts just past the hero portraits, which Tesseract otherwise reads as junk text (`sy`, `@`, `53`) glued to player names.
- **The right edge must stay inside the panel.** A crop ending at x=1900 (including a few pixels of black background) made OCR return almost nothing. x=1875 works, and every width tested from 1700 to 1875 worked.
- **Upscale 2x, then a fixed black/white cutoff.** The scoreboard panel is semi-transparent, so its brightness depends on the game scene behind it. Plain OCR read a first screenshot perfectly and a second one (a brighter, red-tinted scene) as **nothing**, because Tesseract's automatic black/white conversion failed on light-grey text over mid-grey. Measured brightness was panel ≈55–63 and text ≈159, so pixels brighter than 110 become black text on white. Upscaling helps because the small "Level" line is only about 10 px tall. Result: 12/12 on both screenshots, for any cutoff from 90 to 130, so the fix doesn't depend on one lucky value.
- **No contrast stretch.** An earlier version doubled the contrast around the image's average before OCR, which pushed text and background to the same extreme and erased the names. The difference from the fix above: a cutoff placed *between measured values* keeps text and background apart.
- **No character whitelist.** Restricting Tesseract to letters and digits stripped spaces, turning `Grey Mirage` into `GreyMirage`, which breaks multi-word Steam names.

### 4.2 Pair rows by structure, not by guessing
Every scoreboard row is two lines:
```
<Steam name>
<Hero> Level -1
```
The first parser tried to decide from a line's *contents* whether it was a player or a hero, which failed whenever a Steam name was not a hero name (e.g. `Grey Mirage` playing Paradox). The current rule uses *structure*: **a line containing a hero name and "Level" is paired with the line above it.** That works for any Steam name. If OCR garbles the hero line (e.g. `Wperlevel-1`), the row is skipped rather than guessed.

Hero names are matched longest-first, so a short name can never match inside a longer one.

### 4.3 One OCR pass, teams by position
The team headers fall outside the tight crop, so team membership comes from **vertical position**: in a 6v6 layout the ENEMY TEAM header always sits at the same height. `pytesseract.image_to_data` returns every word with its coordinates, so each line is assigned to a team by its `top` value.

The alternative, cropping each team separately, was tested and rejected: OCR on the smaller enemy crop misread `Vyper Level` as `Wyper Level`. One pass keeps the text that is known to be good.

### 4.4 Steam names are not unique
A name search for one test player returned **five accounts with exactly that name**. The lookup:
1. Accepts **exact** (case-insensitive) matches only. Fuzzy matches are usually a different person or an OCR misread, so it reports "not found" with the closest name instead of guessing.
2. If several accounts share the name, it fetches all their hero stats in **one batch request** and picks the account with the most matches on the hero being played right now. The API's own ranking (name similarity + recent activity) breaks ties.
3. Every result says how confident it is (`unique name`, `picked the one with 8 matches on this hero`, or `this pick is a guess`).

### 4.5 Bots are skipped
In bot lobbies, bots are named after their hero. Looking up "Haze" would return random strangers, so a player whose name equals their hero is marked as a likely bot and not looked up.

### 4.6 Hero list comes from the API
An early hardcoded list turned out to be largely invented, and a hand-verified list went stale within days when a new hero was released. Hero names now load from the API, with a verified hardcoded list as an offline fallback.

### 4.7 Privacy
- Only the fields needed are kept from API responses (the search endpoint also returns things like friends lists).
- `screenshots/` and `logs/` are excluded from git because they contain other players' names.

## 5. Testing

`python -m unittest discover -s tests -v` runs 18 tests in under a second:
- **Parser tests** use OCR output actually produced from real screenshots, including a noisy version, plus edge cases: headers, noise-only lines, duplicate player names, multi-word heroes, hero lines with nothing above them.
- **Lookup tests** mock the API to cover account selection, rejecting fuzzy matches, bot skipping, sorting favourite heroes, and network errors being reported instead of crashing the report.

- **Screenshot regression tests** run the full OCR pipeline on real screenshots and compare against hand-checked `.expected.json` answers. Screenshots contain other players' names, so they stay in the gitignored `screenshots/` folder and the test skips on machines without them. This test exists because the unit tests alone missed an OCR failure on a new screenshot; with the fix disabled, it fails.

`python scoreboard_ocr.py <screenshot>` prints the raw OCR lines next to the parsed rows, for debugging a new screenshot.

## 6. Limitations and next steps

- **Resolution:** coordinates were measured at 1920×1080. Other sizes log a warning. Scaling coordinates by resolution, or locating the panel automatically, would remove this limit.
- **Sample size:** tuned on a small number of screenshots, so it needs more varied real matches (long or unusual Steam names, different heroes).
- **Integration:** capture and analysis are separate commands; the next step is running the analysis from the hotkey.
- **Accuracy:** a misread Steam name gives "not found". The report shows the closest match so a person can judge it.

## 7. How this was built

Built iteratively with AI coding assistants, as a deliberate exercise in AI-assisted development. The rule throughout: verify claims against real evidence (screenshots, the OpenAPI spec, actual API responses) before building on them, make one small change at a time, and test each change. Several early AI suggestions, such as an invented hero list, an unverified API URL and image preprocessing that erased text, were caught and reversed this way.
