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
| `app.py` | Desktop app: global hotkey, capture, report window, overlay mode | `tkinter`, `keyboard`, `player_lookup`, `report` |
| `screenshot_manager.py` | Capture and save screenshots; find the latest one | `mss` |
| `scoreboard_ocr.py` | Screenshot → player/hero/team records. Pure parsing logic is separated from OCR so it can be unit-tested without images | `pytesseract`, `Pillow`, `deadlock_api` (hero names) |
| `deadlock_api.py` | Thin client: every HTTP call to the Deadlock API lives here | standard library only |
| `identity.py` | Decide which same-named account is which; detect parties. Pure logic, no network | standard library only |
| `player_lookup.py` | Fetch candidates and stats for the whole lobby; `analyze_screenshot()` runs the full pipeline | `scoreboard_ocr`, `deadlock_api`, `identity` |
| `insights.py` | Stats on the current hero and badge rules (one-trick, new on hero, ...). Pure logic, no network | standard library only |
| `matchups.py` | Your hero vs the enemy heroes (relative to the hero's average) and popular items against them | `deadlock_api` |
| `settings.py` | settings.json (gitignored): window layout and which account is you; saving merges | standard library only |
| `scoreboard_detector.py` | Spots the open scoreboard from a tiny grab of the PLAYERS tab (colour + pattern match) | `mss`, `Pillow` |
| `profiles.py` | Player pages (per-hero stats by mode, recent matches, mode summary) and the hero tier list | `deadlock_api` |
| `ui/` | Theme, reusable widgets (cards, sortable tables, avatars) and the pages | `tkinter`, `Pillow` |
| `assets.py` | Hero portraits, hero colours and rank emblems from the API, cached on disk | `deadlock_api`, `Pillow` |
| `make_shortcut.py` | Creates the desktop shortcut (with an .ico icon) | `Pillow`, PowerShell |
| `match_review.py` | Post-game review: condenses a match's ~1.5 MB data to a small summary, saved on disk; lobby places and comparisons | `deadlock_api`, `profiles` |
| `layout.py` | Finds the scoreboard at any screen size: candidate layouts confirmed by the tab detector | `scoreboard_detector`, `settings` |
| `game_window.py` | Finds the Deadlock window by its program (any monitor, windowed or not) | Windows API via `ctypes` |
| `report.py` | Report lines shared by the terminal and the app, so the two can't drift apart | standard library only |
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
| **CustomTkinter** | Rounded, modern widgets | A small, popular add-on to tkinter: rounded cards, switches, segmented buttons and a dark title bar without changing frameworks |
| **tkinter** (standard library) | App window | Ships with Python, so nothing to install, and enough for a text report with colours and clickable links |
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

### 4.8 The app: threads, a queue, and staying out of its own screenshot
- **tkinter may only be used from the main thread**, but the hotkey fires on the `keyboard` library's thread and analysis (OCR + API calls) takes seconds. The hotkey and a worker thread hand results back through a `queue.Queue` that the window checks every 100 ms, so the window never freezes and never gets touched from the wrong thread.
- **Overlay mode** is an ordinary always-on-top, semi-transparent window. It works over the game in borderless windowed mode. Overlays that draw over *exclusive fullscreen* do it by injecting into the game's renderer, which this project deliberately never does.
- **The overlay would cover the scoreboard in its own screenshot**, so it turns fully transparent for 150 ms during capture. Transparency is used instead of hiding and re-showing the window, because re-showing can steal keyboard focus from the game.

### 4.9 Insights: badges with thresholds, and honest confidence
- **Stats on the current hero**, not just overall: a player's usual heroes say less about *this* match than how they do on the hero they're actually playing.
- **Badges** are rules with named thresholds in `insights.py`, chosen so a badge means something rather than firing on tiny samples: e.g. ONE-TRICK needs their most-played hero with 50+ games *and* 40%+ of everything they play, and win-rate badges need 20+ games. Stats are from normal matches (the API default); in a Street Brawl lobby most players really were on heroes with no recorded games, checked against Street Brawl stats too.
- **Rank** comes from one batch request per lobby (that endpoint allows 20 requests per minute), using the API's own tier names and colours rather than hardcoded ones.
- **ID UNSURE**: a hero-history pick only counts as confident if the winner has at least twice the runner-up's games on the hero (55 vs 7 yes, 8 vs 5 no). The 8-vs-5 case was a real wrong pick, so the UI now warns instead of presenting it as fact.

### 4.10 OCR noise and misread names
- **Junk words:** the panel's textured background sometimes adds junk to the end of a line ("BrightFox ."). Tesseract reports a confidence per word; the junk scored 0-47 while real name words scored 60+, so low-confidence words are trimmed from the *end* of a line only, never the first word, so a real name is never emptied.
- **Misread characters:** OCR sometimes swaps a character for a lookalike ("Or. Night Owl" for "Dr. Night Owl"). With no exact match, a name that differs by **exactly one character, at the same length, in a name of 6+ characters** is accepted as a misread, marked NAME FIXED and ID UNSURE.
- **The first version was wrong, and real data showed it.** It used a general similarity score (90%+). On a real 6v6 screenshot that rule "corrected" two names OCR had read *right*: "Kovas" became a stranger called "Kovmas", "Ravenl" became one of 25 "raven" accounts. Misreads swap characters; they don't add or drop them. The stricter rule fixes "Dr. Night Owl", leaves "Kovas" alone, and still caught a genuine lowercase-L / capital-i swap ("Ravenl" → "RavenI"). Both bad corrections are now unit tests.

### 4.11 UI layout, measured
Teams sit side by side with one card per player. Whether a full lobby fits was **measured**, not eyeballed: tkinter reports the size a layout needs, so the worst case (12 real players with badges and parties) was rendered off-screen. It needed 826px against an 820px window, so card spacing was tightened to 790px.

### 4.12 Avatars, search, and players who can't be found
- **Avatars** come from URLs already in the search response, downloaded in parallel on the worker thread. tkinter only shows an image while Python holds a reference to it, so the app keeps them in a dictionary.
- **Manual search** shows every account with the exact name (or the closest names), so a person can tell them apart by avatar, rank and stats.
- **Why some players are never found:** one real "not found" player had no exact match in search, and none of the 196 friends of their identified teammates had that name either. The account simply isn't in the API's database, so no name-based method can find it. Valve's own match data (match ID → metadata, after the match) is the only fix for that case.

### 4.13 Knowing who "you" are, and your matchup
- The user's own account (name + exact account ID) is stored in `settings.json`, which is gitignored. A lobby player with that name is identified as that account with certainty, which fixed a real case where a stranger sharing the author's name had more games on the hero being played. Saving settings **merges** into the file, so the app's window settings (saved on close) and "me" can't erase each other.
- **Matchups** come from one request returning every hero-vs-hero pair. A strong hero wins most matchups (one new hero won 57-65% against everyone), so each matchup is compared with **the hero's own average**, not with 50%.
- **Items** are the ones most often bought by the user's hero *against this enemy team* (the API filters by enemy heroes), with win rates shown but not used for ranking: an item's win rate is inflated when mostly players who are already winning can afford it, so ranking by win rate would always recommend expensive late items.
- Lanes are deliberately left out: the scoreboard doesn't show them.

### 4.14 Auto-detecting the scoreboard
- A watcher thread grabs only the **PLAYERS tab** area (185x20 pixels) once a second and compares it with a reference image (`assets/players_tab.png`). One check takes about 6 ms, so the cost is negligible. Screen reading only, like a screen recorder.
- **Two checks must pass, and the second was added because a test found the first wasn't enough.** Mean colour difference alone (< 15) was fooled by a plain block of the tab's colour, with no text, at 14.3. So the detector also requires **normalised cross-correlation** > 0.9, which compares the pattern of light and dark pixels (the word "PLAYERS") and scores flat areas 0. Real scoreboards: difference 0-1.6 and correlation 1.00, on four very different scenes (the tab is drawn solid, so the game behind it never shows). Everything else: correlation 0.51 at most.
- It fires once per opening, after a short wait for the menu animation, and re-checks first. If the OCR'd lobby matches the one already shown, the API calls are skipped and the duplicate screenshot deleted.
- **Keeping the app out of its own way:** the window calls `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`, so Windows leaves it out of screen captures. **That applies to every capture tool**: a user couldn't stream the app on Discord (the stream showed what was behind it), so it's now only hidden while overlay mode is on, and for the instant of the app's own screenshot. In overlay mode it can then sit over the scoreboard without hiding it from screenshots or from the detector. (Tested: a window placed over the tab vanished from the capture.) Older Windows falls back to briefly turning the window transparent.
- Tested end to end without a game: the watcher was fed a scripted sequence (closed, open, closed, open again) with a real screenshot standing in for the capture; the first opening produced the full report and the second was recognised as the same lobby.

### 4.15 From a report window to an app with pages
- **Structure:** `app.py` owns the window, the top bar, navigation and all background work; `ui/pages.py` has one class per page; `ui/widgets.py` has the reusable pieces (player cards, sortable tables, toggles, avatars); `ui/theme.py` the colours and table styling. Before this, one 500-line file mixed all of it.
- **Navigation** keeps a history of (page, options), so **Back** retraces exactly where the user went, and the title always goes Home. Pages are rebuilt on each visit, from data kept in the app (the last lobby, cached tier lists), so there's no stale widget state.
- **One way to load data:** `run_task(work, on_done)` runs `work` on a worker thread and `on_done` on the main thread, **only if the user is still on the page that asked** (a page token changes on every navigation), so a slow response can never draw over a different page. Errors become a status message. One detail: Python deletes an `except ... as e` variable when the block ends, so the error text is copied into a variable before the callback that uses it.
- **A detected lobby still arrives while browsing:** lobby analysis doesn't use `run_task`, so it isn't dropped when the user is on another page. The app jumps to the Lobby page, and Back returns to where they were.
- **Data from match history:** game mode and result are stored as numbers, decoded from the API's spec (game mode 1 = Normal, 4 = Street Brawl). The result field is 0 ("invalid") in 969 of the author's 1,015 matches, so a win is computed as "the player's team won" (`match_result == player_team`), which agrees with the result field in every match where it's set.
- **Tables** are `ttk.Treeview`, styled dark (the default Windows theme ignores colours, so the `clam` theme is used). Clicking a heading sorts by that column: numbers biggest first, text A-Z, click again to reverse.
- **Checked visually:** every page was photographed with real data and adjusted (table borders, fitting the player page in 880 px, filling the empty Home page).

### 4.16 Visual design
- **CustomTkinter** (a small add-on to tkinter) supplies what plain tkinter can't: rounded cards, pills, buttons, a segmented Normal/Street Brawl switch, toggle switches, a search box with placeholder text and Windows' dark title bar. Plain text stays as ordinary tk labels, because CustomTkinter draws every widget on a canvas, which is slower; that keeps a 12-player lobby quick to draw.
- **A small design system** in `ui/theme.py`: background / surface / card layers, one accent colour (cyan) plus orange for the enemy team, Segoe UI Semibold for headings, 12 px card corners. Pages only use the helpers (`card`, `pill`, `button`, `segmented`, `switch`, `label`), so the look changes in one place.
- **Images from the API's assets:** each hero's small icon sits on a circle in **that hero's own colour** (from the assets), because some icons are nearly black and would vanish on a dark theme. Rank pills show the rank's emblem, and rank colours are lightened until readable as text (Obscurus is #333333). Player pages show the main hero's portrait. Steam avatars are drawn as circles with a ring in the team or party colour.
- **Disk cache:** 105 hero and rank images are downloaded once into `cache/` (gitignored): 4 s on the first run, 0.02 s afterwards. They load in the background at startup, then the current page redraws.
- **Hover:** clickable cards light up their border. Recolouring the whole card would mean recolouring every widget inside it; the border is one change.
- **Measured, not eyeballed:** a full 6v6 lobby needed 1,061 px at first (Windows then maximised the window). Measuring each part showed the height was spread evenly across 12 cards of ~112 px, so the cards were tightened and the default window raised to 960 px (fits a 1080p screen). The window also never grows past the screen any more.

### 4.17 Speed: measure first, then fix the real bottleneck
- **Measured before changing anything.** Building a page's widgets took 17-35 ms (182 ms for a 12-card lobby) and startup ~240 ms. The waits were the network: the player page made 5 requests one after another (~450 ms), and nothing was reused, so even Back re-fetched everything.
- **Response cache:** `get_json` reuses answers for 5 minutes (an hour for analytics the server itself recomputes hourly), capped at 300 entries. Player page revisit / Back: 441 ms → 1 ms. Cached answers are shared objects, so callers treat them as read-only.
- **Parallel requests** (`parallel()`): the player page's four requests run at once (~200 ms); a lobby searches all names concurrently and fetches ranks in the same round as hero stats, one batch request each (12-player lobby 1.85 s → 1.2 s, of which ~0.6 s is OCR). Data that needs another request's answer first ("plays most with", 2 requests) loads **after** the page is shown, so it never delays it.
- **Keeping it small:** the first disk cache stored raw API responses (items 6 MB, heroes 2 MB) and full-size images (rank emblems are 512 px, 220 KB each, shown at 18 px): 16 MB. Now only the processed fields are stored and images are capped at 96 px (portraits 240 px): **1.6 MB**, and later launches load every list and image in ~70 ms. Mate stats are requested with `min_matches_played` (275 KB → 10 KB). The running app uses about 65 MB of memory.

### 4.18 Hero pages, rank bands and teammates
- **Rank filter:** five bands rather than eleven single ranks, so every win rate still rests on 1,000+ games per hero (checked at the highest band). The bands are tested to cover every rank exactly once.
- **Hero page:** best and toughest matchups judged against the hero's own average (the same idea as the matchup strip), and its most-bought items coloured by shop category. Two requests in parallel, cached for an hour.
- **Plays most with:** the API's "same party" filter returned nothing, so this is "most games together", which in practice means friends.
- **Desktop shortcut:** `make_shortcut.py` asks Windows where the Desktop is (it can be inside OneDrive), starts the app with `pythonw` so there's no console, and the app sets its own taskbar ID so Windows shows its icon instead of Python's.

### 4.19 Post-game review
- **Source:** `/v1/matches/{id}/metadata` has every player's stats, items with buy/sell times and 11 snapshots over the match. It's **~1.5 MB**, so it's condensed into a **~9 KB summary** straight away (scoreboard, final build = items never sold, net-worth lead per snapshot), and the raw response is never kept: `get_json(max_age=0)` now means "don't store this", where before it was still added to the memory cache.
- **Finished matches never change**, so summaries are saved to disk (newest 50). Reopening a match is instant, and the API is asked once.
- **Rate limits:** matches the API hasn't stored are fetched from Steam at **3 per hour**. Testing ran into it (`429`), so a failed match isn't requested again for 10 minutes, and the page explains the failure: rate-limited, or not available yet (`404`, or `503` while the API is still fetching it: a just-finished match returned 503, later 404). Bot matches are never recorded.
- **"Your game"** puts each stat in context two ways: place in the lobby ("3rd of 12") and per-minute value vs the player's own average on that hero (from hero stats; there's no healing average, so healing has only the lobby place).
- **The lead chart** is a plain `tk.Canvas`: one filled segment per snapshot, green while ahead, red while behind, redrawn when the window resizes. No charting library needed.
- **Found while measuring the review page:** avatars were being re-downloaded on every page visit, because the downloader never checked what it already had. Fixed for every page (match reopen 431 ms → 142 ms).

### 4.20 Ban share, not ban rate
The ban endpoint returns how many times each hero was banned, but not how many matches those bans came from, so a true ban rate would need a guessed number of bans per match. The app shows **ban share**: each hero's part of all recorded bans. Every hero is divided by the same total, so the ranking matches a ban rate; only the scale differs, and it's labelled. A hero missing from the ban data (a brand-new hero) shows "-", not "0%", which would claim nobody bans it.

### 4.21 Stats by rank, and ranked vs unranked
- **Hero win rate at every rank** comes from **one** request: the analytics endpoint's `bucket=avg_badge` groups every hero's stats by rank. The answer is ~1.6 MB (24 fields per row); only wins and games per hero per tier are kept, on disk for 6 hours: **8 KB**, and every hero's chart is then instant. Ranks with under 1,000 games (e.g. Eternus) are drawn faded, as less reliable.
- **Player pages split Ranked / Unranked / Street Brawl.** Match history marks each match (`match_mode` 4 = ranked; Street Brawl is always unranked, so the three don't overlap), and the per-hero table uses the hero-stats endpoint's `match_mode` filter (default: both).
- **Size check** (measured): ~3,400 lines of app code and ~1,100 of tests; committed files 282 KB; Python packages ~18 MB (Pillow 16 MB of that) plus the Tesseract engine (~116 MB, separate install); ~70 MB of memory with a 12-player lobby loaded; window on screen in ~0.4 s; local cache ~2 MB.

### 4.22 Any screen size, any monitor
- **What was tied to 1920x1080:** the scoreboard crop, the row grid that splits the teams, the PLAYERS-tab position and reference image, and capture of the primary monitor only (the author has a second monitor, where this would have silently failed).
- **No guessed coordinates.** Without screenshots at other sizes, `layout.py` lists a few plausible layouts (UI scaling with the screen's height or width; anchored to the right edge or a centred 16:9 area; top or letterboxed) and **confirms each with the PLAYERS-tab detector**. The match is remembered per screen size, so it's found once. The scoreboard is then scaled back to its 1920x1080 size before reading, so every tuned value (crop, brightness cutoff, row grid) is unchanged.
- **The detector is position-sensitive:** shifted by one pixel, the real tab fails (correlation 1.00 → 0.73). That would also have broken auto-detect at 1080p if a game patch nudged the tab. So it now searches ±3 px around the predicted spot. Two faster shortcuts were tried and rejected because they gave wrong answers: Pillow's `multiply` (rounds every pixel; scored an identical image 0.87) and statistics on float images (impossible values, from a 256-bucket histogram). Restructuring the exact integer maths (`sum(map(operator.mul, ...))`, reference sums computed once) made each check 5x faster (0.14 ms) with identical results. Blurring for tolerance was rejected too: it made the unselected tab look like the selected one.
- **Capture follows the game window** (`game_window.py`, matched by `deadlock.exe`, ~1 ms), so any monitor and windowed mode work, and the watcher does nothing while the game isn't running.
- **Simulated sizes** (resized/padded real screenshots, `tests/test_resolutions.py`): 1440p, both ultrawide arrangements, 16:10 and 900p read 12/12; simulated 4K and 720p 11/12. Small screens get sharpening plus a higher cutoff (720p went from 1/7 to 7/7 on one screenshot); at 1080p and above sharpening slightly hurt, so it's only used below. The 4K misses turned out to be ordinary OCR noise that can happen at any size, which led to two general fixes: a hero name one letter off ("Oynamo") counts as that hero (5+ letters), and names are compared ignoring spaces ("Dr.NightOwl"). **Simulated images are blurrier than real ones**, so tuning stopped there: real screenshots at those sizes are the next step.

### 4.23 Housekeeping
- Screenshots older than 7 days are deleted at startup and after each capture. Only files named like the app's own captures are touched, and a screenshot with an `.expected.json` (a regression test case) is never deleted.
- Hero and rank lists are cached for the life of the app (`functools.lru_cache`); a failed request isn't cached, so it's retried next time.
- The analysis reports progress through a callback, so the lookup code doesn't need to know about the window.

### 4.24 Installer
- **Why:** running from source needs Python, a pip install and a separate Tesseract install. That's fine for a developer and a dead end for most players, so this was the biggest barrier to anyone else using the app.
- **PyInstaller, one folder** (`installer/build.py`): it bundles Python and the app's packages next to an `.exe`. The single-file option was rejected because it unpacks itself to a temp folder on every start. That's slower, and antivirus tools are more suspicious of it.
- **Tesseract, trimmed:** the full install is 112 MB, mostly training tools and libraries for rendering text into images. The build reads each program's import table (with `pefile`, which PyInstaller already uses) and follows DLLs that load other DLLs, starting from `tesseract.exe`. It ends up with 33 DLLs plus the English model (53 MB). It found the same list `ldd` did, and it stays correct when Tesseract updates and DLL names change. The build then OCRs a test image using only the copy, with no PATH or TESSDATA_PREFIX, so a missing file fails the build instead of a user's first scan.
- **Measured, then trimmed:** the first build was 141 MB. Listing the biggest files showed every Tesseract DLL twice: PyInstaller analyses DLLs given as data and copies their dependencies next to Python's. Tesseract is now copied in after PyInstaller runs. Pillow's AVIF plugin (8 MB, never used) is excluded. Result: **28 MB installer, 87 MB installed**. The window opens in about 1.4 s and the app uses about 80 MB of memory, the same as from source.
- **`paths.py`:** shipped files (icon, PLAYERS-tab reference, Tesseract) come from the bundle. Files the app writes (settings, cache, screenshots, logs) go next to the `.exe`. From source, both are the project folder. Every module asks `paths`, so the app no longer depends on which folder it was started from.
- **Inno Setup, per-user:** installs to `%LOCALAPPDATA%\Programs`, like VS Code's user installer: no admin prompt, and the app can write its files next to itself. A fixed AppId lets a newer installer upgrade an older one. `_internal` is cleared before each upgrade so stale files can't linger. Uninstalling removes what the app wrote too.
- **Checked by installing it:** silent install and uninstall (files, both shortcuts and the *Installed apps* entry appear, then all disappear). The installed copy started from its Start-menu shortcut and read a real screenshot (12/12). Windows Defender found no threats in either the installer or the app folder.
- **Licences:** the app now redistributes other people's software, so `LICENSE.txt`, `THIRD_PARTY_NOTICES.txt` and a `licenses/` folder (Python, each package, Tesseract and the GNU texts for its LGPL/GPL DLLs) ship next to the `.exe`. The app runs `tesseract.exe` as a separate program rather than linking to those libraries.
- **Not code-signed:** Windows SmartScreen warns about new unsigned programs, and the README explains the "Run anyway" click. A certificate costs money, though free signing exists for open-source projects (e.g. SignPath).
- **Found along the way:** CustomTkinter replaces the window icon with its own 200 ms after start unless `iconbitmap` is called. The `.ico` is set that way now, which fixed the icon from source too.

### 4.25 Item icons and hover details
- **Weight first:** the API offers each item's white symbol (128 px, under 1 KB) and its shop artwork (200 px, 35 KB). The symbol drawn on a rounded square in the item's category colour looks like the in-game shop and costs almost nothing. 17 of 173 items have no readable symbol (missing, or SVG, which Pillow can't open), so they use their artwork clipped to the same square.
- **Only what's shown is downloaded**, stored at 48 px (shown at up to 26 px). All 169 icons together would be about 350 KB; a normal session downloads 10-20. The first version fetched every player's build on the match page although only yours is shown, and stored icons at 96 px: measuring the cache (513 KB for 100 icons) caught both.
- **Hover popups** (`tooltip` in `ui/widgets.py`): one shared borderless window, so popups cost nothing until used. Hiding waits 60 ms so moving between a row's icon and its text doesn't flicker, and changing page hides it (a destroyed widget never gets a "mouse left" event).
- The match page's final build became a row of icons with names on hover, like the in-game end screen. Reviews saved before item ids were stored find their icons by item name.

### 4.26 Hero trends
- **One request:** the API can group hero stats by week, so every hero's last 12 weeks come in one request (0.4-0.9 s, ~335 KB), condensed to wins and games per week and kept on disk for 6 hours (8 KB). The API's weeks start Sunday 00:00 UTC (checked against real responses); the current week is left out until it's over, because a few days of games (316k against 4.3M for a full week) would make every line jump at the end.
- **"Steady" means something:** the change is the last 4 weeks against 9-12 weeks ago. It's called steady when it's within two standard errors of the difference between the two win rates (a hero on 5,000 games a month can move a point by luck), or under half a point: with millions of games even a 0.2-point move is statistically real, but nobody notices it. With all ranks, 17 of 39 heroes come out steady.
- **Comparable lines:** every small line is drawn on the same 5-point scale, centred on that hero's own level. Each line scaled to its own range made noise look like big swings; one scale for all heroes (44-61%) made every line flat. The hero page's bigger chart uses a real scale with a 50% line.
- **A canvas table** (`ui/charts.py`) replaced the ttk table on the Heroes page: ttk tables can only show text and one image per row, so they can't hold a chart. The canvas draws the 39 rows (469 shapes) in 6 ms, redraws one trend cell under the mouse in 0.02 ms, and keeps sorting and click-to-open. The other tables stay ttk.
- **Found on the way:** the API refuses a rank filter for Street Brawl ("Cannot filter by average badge for street brawl game mode"), so choosing a rank band there failed. Street Brawl now always uses all ranks, with the filter greyed out and a hover note explaining why.

## 5. Testing

`python -m unittest discover -s tests -v` runs 132 tests in a few seconds:
- **Parser tests** use OCR output actually produced from real screenshots, including a noisy version, plus edge cases: headers, noise-only lines, duplicate player names, multi-word heroes, hero lines with nothing above them.
- **Identity tests** use plain data to cover settling by unique name, friend links (including links listed by only one side and chains of settled players), ties falling back to hero history, and party grouping.
- **Insights tests** cover each badge rule and its thresholds.
- **Lookup tests** mock the API (a guard makes any unmocked call fail, so unit tests can never quietly use the network) to cover rejecting fuzzy matches, bot skipping, one stats request per lobby, favourite heroes, and network errors being reported instead of crashing the report.

- **Screenshot regression tests** run the full OCR pipeline on real screenshots and compare against hand-checked `.expected.json` answers. Screenshots contain other players' names, so they stay in the gitignored `screenshots/` folder and the test skips on machines without them. This test exists because the unit tests alone missed an OCR failure on a new screenshot; with the fix disabled, it fails.

`python scoreboard_ocr.py <screenshot>` prints the raw OCR lines next to the parsed rows, for debugging a new screenshot.

## 6. Limitations and next steps

- **Resolution:** other screen sizes are handled by `layout.py` (4.22), but so far only tested on simulated screenshots. Real ones are needed.
- **Sample size:** tuned on a small number of screenshots, so it needs more varied real matches (long or unusual Steam names, different heroes).
- **App:** the GUI itself is checked with a smoke test (hidden window, real worker thread and queue), not unit tests.
- **Accuracy:** a misread Steam name gives "not found". The report shows the closest match so a person can judge it.

## 7. How this was built

Built iteratively with AI coding assistants, as a deliberate exercise in AI-assisted development. The rule throughout: verify claims against real evidence (screenshots, the OpenAPI spec, actual API responses) before building on them, make one small change at a time, and test each change. Several early AI suggestions, such as an invented hero list, an unverified API URL and image preprocessing that erased text, were caught and reversed this way.
