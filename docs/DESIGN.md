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
| `identity.py` | Decide which same-named account is which; detect parties. Pure logic, no network | standard library only |
| `player_lookup.py` | Fetch candidates and stats for the whole lobby, then print the report | `scoreboard_ocr`, `deadlock_api`, `identity` |
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

### 4.3 One OCR pass, teams from the row grid
`pytesseract.image_to_data` returns every word with its coordinates, so each parsed row keeps the vertical position of its name line.

The first version split teams at a fixed height, which assumed 6v6. A Street Brawl lobby (4v4, one player still connecting, so 3 vs 4) put three enemies on the wrong team. Two alternatives were tested:
- **Reading the ENEMY TEAM header with OCR:** it was read in two screenshots and missed entirely in the third.
- **Finding the header gap from pixel brightness:** it worked on bright scenes, but on a dark scene the semi-transparent panel was as dark as the gaps.

What held on every screenshot: **player rows sit on a 60px grid, and the enemy header pushes all enemy rows down an extra 40px.** So a row's offset from the grid tells its team (≈0px means friendly, ≈40px means enemy), whatever the team sizes.

Cropping each team separately was also rejected early on: OCR on the smaller enemy crop misread `Vyper Level` as `Wyper Level`. One pass keeps the text that is known to be good.

### 4.4 Steam names are not unique: resolve the whole lobby with evidence
A name search for one test player returned **seven accounts with exactly that name**. Only **exact** (case-insensitive) matches are accepted; a fuzzy match is usually a different person or an OCR misread, so the report says "not found" and shows the closest name instead of guessing.

When several accounts share a name, `identity.py` resolves the **whole lobby together**, using evidence in order of strength:
1. **Unique name.** Settled outright.
2. **Friend links to settled players.** The search response includes each account's Steam friends. A candidate who is friends with an already-settled player in the same lobby is almost certainly the right one. This repeats, because each newly settled player can settle another (in one real lobby: a unique name settled a second player, and those two together settled a third). A link counts if either side lists it, since friend lists can be private. Links between two *unsettled* guesses, or ties, don't count as evidence.
3. **Hero history.** Most matches on the hero being played right now, with the runner-up shown so close calls are visible.

All candidates' hero stats come from **one batch request** for the whole lobby.

**Why friend links come before hero history.** In one Street Brawl lobby, hero history picked a "PlayerB" with 25 games on his hero. The "PlayerB" who was friends with two other players in that lobby, who were also friends with each other, had **0** games on it. Hero history fails exactly when someone tries an unfamiliar hero, which is common.

**Party detection** falls out of the same data: settled players on the same team who are connected by friend links almost always queued together, so the report lists them ("Party of 3: …"). Friend lists are used only in memory for linking, and are never displayed or stored.

Every result says how it was decided: `unique name`, `friends with X in this lobby`, `picked the one with 16 matches on this hero (next best: 8)`, or `this pick is a guess`.

**A lesson from testing against a known answer.** The search endpoint hides accounts with fewer than 5 recorded matches in the last 30 days by default. Bot matches aren't recorded, so the author's own account was filtered out *before* the tie-breaker ran, and two screenshots confidently picked two different wrong accounts. Checking against a player whose real account was known exposed it. With the filter off there were 7 accounts with the exact name, and the tie-breaker picked the right one on both screenshots (16 vs 8 matches on one hero, 55 vs 7 on the other). A unit test now pins that parameter.

### 4.5 Bots are skipped
In bot lobbies, bots are named after their hero. Looking up "Haze" would return random strangers, so a player whose name equals their hero is marked as a likely bot and not looked up.

### 4.6 Hero list comes from the API
An early hardcoded list turned out to be largely invented, and a hand-verified list went stale within days when a new hero was released. Hero names now load from the API, with a verified hardcoded list as an offline fallback.

### 4.7 Privacy
- Only the fields needed are kept from API responses (the search endpoint also returns things like friends lists).
- `screenshots/` and `logs/` are excluded from git because they contain other players' names.

## 5. Testing

`python -m unittest discover -s tests -v` runs 32 tests in under a second:
- **Parser tests** use OCR output actually produced from real screenshots, including a noisy version, plus edge cases: headers, noise-only lines, duplicate player names, multi-word heroes, hero lines with nothing above them.
- **Identity tests** use plain data to cover settling by unique name, friend links (including links listed by only one side and chains of settled players), ties falling back to hero history, and party grouping.
- **Lookup tests** mock the API to cover rejecting fuzzy matches, bot skipping, one stats request per lobby, favourite heroes, and network errors being reported instead of crashing the report.

- **Screenshot regression tests** run the full OCR pipeline on real screenshots and compare against hand-checked `.expected.json` answers. Screenshots contain other players' names, so they stay in the gitignored `screenshots/` folder and the test skips on machines without them. This test exists because the unit tests alone missed an OCR failure on a new screenshot; with the fix disabled, it fails.

`python scoreboard_ocr.py <screenshot>` prints the raw OCR lines next to the parsed rows, for debugging a new screenshot.

## 6. Limitations and next steps

- **Resolution:** coordinates were measured at 1920×1080 (6v6 and Street Brawl layouts). Other sizes log a warning. Scaling coordinates by resolution, or locating the panel automatically, would remove this limit.
- **Sample size:** tuned on a small number of screenshots, so it needs more varied real matches (long or unusual Steam names, different heroes).
- **Integration:** capture and analysis are separate commands; the next step is running the analysis from the hotkey.
- **Accuracy:** a misread Steam name gives "not found". The report shows the closest match so a person can judge it.

## 7. How this was built

Built iteratively with AI coding assistants, as a deliberate exercise in AI-assisted development. The rule throughout: verify claims against real evidence (screenshots, the OpenAPI spec, actual API responses) before building on them, make one small change at a time, and test each change. Several early AI suggestions, such as an invented hero list, an unverified API URL and image preprocessing that erased text, were caught and reversed this way.
