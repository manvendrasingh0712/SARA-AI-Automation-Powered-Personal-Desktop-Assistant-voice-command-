# GUI audit (2026-10-30)

Scope: `sara/gui` (index.html, 49 JS files, 37 CSS files, 21 Python bridge modules).
Method: static cross-checks (script), the page opened in the built-in browser in preview mode at 1280x800 and 1000x640 (the app's minimum), DOM hit-testing of every control on every page, JS test run. **Not covered:** the real pywebview window with a live backend, measured FPS (the browser pane was hidden, so rAF was throttled), listening to the sounds, screenshots of the lower part of each page (the pane crops them), mobile/touch.

## 1. What works (verified)
| Check | Result |
|---|---|
| Every `callApi('x')` in JS has a Python method | 85/85 |
| Every JS `ev:` handler has a Python pusher | 19/19 |
| Pages load, no console errors in the real code path | 6/6 (only mock-gap errors, see 3) |
| JS tests (`node js/tests/*.test.js`) | pass (47 + 2 other files) |
| Dead CSS | low: ~1 % of classes unused overall |
| UI sounds | 11 Web-Audio tones, all wired to callers, toggle persisted |
| Reduced-motion | honoured in 23 CSS files and 21 JS files |
| Hidden-window pause | `visibilitychange` handled in 6 JS files |

## 2. Bugs found
| # | Problem | Status |
|---|---|---|
| G1 | Mini-player (fixed, bottom centre) sat on top of the **chat input** and on Home's **pause listening / customize** buttons whenever music was playing (`elementFromPoint` returned the player) | **Fixed** in `style/music.css` (chat: lifted above the dock, home: bottom-right). Re-tested: no overlap on home/chat/notes |
| G2 | `fpsHudTier` element is referenced by `quality.js:57` but never created, so the dev HUD never shows the tier | open, trivial |
| G3 | `get_security_events` exists in `app/security_api.py` but nothing in the UI calls it: the security event log is not visible to the user | open (unwired feature) |
| G4 | Preview mock lacks `get_ui_prefs` and `get_frequent_misses` (console errors in preview only; real app unaffected) | open, trivial |
| G5 | 12 ids in index.html are never referenced by JS/CSS (`anKpis anPeak audioModalTitle grp-access grp-presets mvTab-archived mvTab-entities mvTab-events mvTitle pane-notes pane-reminders rtSavedWrap`); some may be built by string concatenation, verify before deleting | open |
| G6 | Stale comments: `main.js` says "loaded LAST" but `chat-plus`, `nlparse`, `notes-plus` load after it; index.html header says "ui -> mock -> core" but mock is not loaded; `quality.js` mentions `premium.js` which no longer exists | open |

## 3. Structure problems (why it feels disorganised)
1. **Layering by patching.** Six "-plus" JS files and eight "-plus" CSS files sit on top of the originals. Four of them wrap `SARA.callApi` (`automation-plus`, `empty-plus`, `music-plus`, `notes-plus`), two wrap `SARA.toast`. Behaviour now depends on script order and on every wrapper forwarding correctly; one forgotten `return` breaks every API call.
2. **Flat folders.** 49 JS and 37 CSS files in two flat folders; page ownership is only in file names. `motion-pack.css` (52 KB) and `motion-pack.js` (20 KB, 10 rAF loops) are a merged grab-bag of former files.
3. **Order is hand-maintained** as 40 `<script>` and 38 `<link>` tags in index.html (51 KB, 305 ids, all pages' markup in one file). No bundling or minification: ~1.2 MB loaded synchronously at start.
4. **Big files:** `media.py` 1317 lines, `core.py` 700; `music.js` 50 KB, `home.js` 36 KB.
5. **Tests:** 3 JS test files for 49 JS files; no test for the CSS/overlap class of bug (G1).
6. **No sound files / no Python-side UI sound control**: all UI sound is beeps generated in JS (fine, tiny), but there is no volume setting separate from on/off.

## 4. Production-quality gaps
- No visual regression or overlap test (G1 slipped through).
- No FPS/perf budget check in CI; effects tiers (low/balanced/high) exist but are chosen manually, not auto-detected.
- No error boundary: a thrown error in one page file can silently leave that page half-initialised (only `console.error`).
- No single `SARA` public API document; wrappers make it hard to know what `callApi` really does.
- Accessibility exists (`a11y-plus`) but no automated check.
- No packaged-app smoke test (window opens, bridge binds, boot overlay finishes).

## 5. Recommended restructure (safe order, each step shippable)
1. **Folder by feature, not by layer** (no behaviour change): `gui/features/{home,chat,notes,apps,automation,settings}/` each with `*.js`, `*.css`; `gui/core/` (ui, core, prefs, theme, bridge); `gui/fx/` (motion, motion-pack, fx, quality); `gui/shared/`.
2. **Fold each "-plus" file into its base file** one feature at a time; replace the four `callApi` wrappers with one explicit hook list in `core.js` (`SARA.api.use(name, handler)`), and one `toast` module.
3. **Generate the tag lists**: a small `build_gui.py` that concatenates in a declared order into `dist/app.js` and `dist/app.css` (minified), with index.html referencing two files. Keeps dev readable, startup faster.
4. **Split index.html** per page into templates injected at boot, or keep one file but drop dead ids (G5).
5. **Tests:** add a Playwright/DOM overlap test (every control reachable by `elementFromPoint` at 1000x640 with music playing), a bridge-contract test already exists, add event-contract test.
6. **Perf:** auto-pick the effects tier from a 2-second FPS probe on first run; pause all rAF loops when the window is hidden (6 of ~19 files do now).
7. **Python side:** split `media.py` into player / metadata / controls.

Steps 1 and 3 are mechanical; 2 is the risky one and needs the live app to verify each page.

## 6. Restructure status
Done (2026-10-30): steps 1 and 3 of section 5.
- Sources moved (git mv, history kept) to `js/<group>/` and `style/<group>/` with groups core, fx, shared, home, chat, notes, apps, automation, settings, dev (preview mock).
- `assets.json` is the single load-order list; `sara/gui/build.py` bundles it into `bundle/app.css` + `bundle/app.js`; `index.html` has two tags instead of 78; `bootstrap.py` rebuilds the bundle at launch if a source changed.
- `main.js` is now last in the bundle (it was followed by three files before).
- Tests: `test_gui_assets.py` now checks the manifest, that index.html loads only the bundle, and that the bundle is not stale; node tests find sources by name.
Step 2 done (same day):
- `SARA.callApi` / `SARA.toast` are no longer re-assigned by six files. `core.js` now owns `SARA.api.before / after / raw` and `ui.js` owns `SARA.onToast`; the six wrappers (automation, empty states, music, notes, a11y, motion-pack) became explicit hooks. Hooks never change a result, errors in them are logged.
- Five JS and five CSS "-plus" files were folded into their base file (home, music, automation, chat, notes); `nlparse.js` now loads before `notes-reminders.js`. The three cross-cutting ones were renamed: `a11y.js`, `empty-states.js`, `settings-presets.js` (+ CSS).
- Check used: computed style of all 7095 element/pseudo-element states (6 pages, animations off) compared before and after the CSS merge: identical except the two time-dependent ones (sun position, seek thumb) and one real bug found by the diff: **`.rt-num` was defined twice** - the automation step-number badge and the Settings > Router stat tiles - so each was styled by the other's rules. The tiles are now `.rt-stat`.
- Verified in the browser preview only (no live pywebview backend), so click-through of every feature in the real app is still advisable.

Workflow after editing any GUI source: `python -m sara.gui.build` (the app also does it on start).

## 7. Follow-up fixes (after running the real app)
| Item | Fix |
|---|---|
| "Frequently missed commands" was full of small talk ("thank you" 13x, "yes" 7x) and plain questions | `is_not_a_command()` in `sara/core/unmatched_log.py`: not written to the log any more, and filtered when reading the old log. Also drops Whisper silence hallucinations ("thank you for watching", "nd nd nd"). On the real log 72 of 189 lines were noise |
| Memory consolidation saved guesses and one-off remarks ("They likely confused ... with a story") | Prompt now requires facts stated by the user and starting with "User"; `_is_durable_fact()` rejects hedged/one-off phrasing before storing |
| G2 `fpsHudTier` | dead line removed (the HUD already prints the tier) |
| G3 `get_security_events` unwired | Security card shows "Show more" (up to 50 events) |
| G4 preview mock gaps | mock answers `get_ui_prefs`, `set_ui_pref`, `get_frequent_misses` |
| G5 unused ids | `anKpis`, `anPeak`, `rtSavedWrap` removed (`audioModalTitle`, `mvTitle` are used by aria-labelledby and stay) |

## 8. Fixes from the user's first run of the new build
| Report | Root cause (found by attaching to the real window over CDP) | Fix |
|---|---|---|
| Black screen at launch, fine after switching page | The user had the "pm" effects pack switched off (`data-fx-off="ax pm lx"`). `motion-pack.js` still created its overlay elements (`pm-dust` canvas, `pm-aura`, ...), but their `position:fixed` rules only apply while the pack is ON, so the full-window canvas became a normal block in `<body>` and pushed the whole app ~1000 px down (`#app` top = 997 px) | `.pm-*` overlays are `position:fixed` unconditionally and hidden when the pack is off (`style/fx/motion-pack.css`); test added. After the fix `#app` top = 0 |
| Notes: no way to delete one | There was no backend method at all (`save_note` / `get_notes` only) | `delete_note` (storage + Api + UI trash button + Undo); the id shifts after a delete so the text is sent as a guard against a stale list |
| Security card empty | The card worked (Strict mode, 3 confirmations after the first real use); with no events it just printed "No security events yet" | Empty state now explains what is tracked and whether protection is on |
| Lyrics never shown | lrclib answers 400 (bad length, e.g. a podcast reporting 7650 s) and 404 (song not found, e.g. Devanagari artist names) and the code treated both as "can't reach the service"; the search also required the artist to match | 400/404 = not found; absurd lengths dropped; fallbacks: cleaned title, `q=` search, title-only search, with a title/length match check so a wrong song is never shown. Verified live ("Jeete Hain Chal", "Paniyon Sa", "Zara Sa") |
| Setup wizard showed an error every launch ("gemini-embedding-001 ... nothing to pull") | The check ignored `EMBEDDING_BACKEND=ollama` | It now probes the Ollama embedder it actually uses; "Details" can pull that model |
| (found on the way) App kept running an OLD bundle after an update | WebView2 reused its cached `app.js` without asking the server | Bundle URLs carry a content hash (`?v=...`) written by `build.py`; the WebView2 HTTP cache is cleared once when the GUI version changes |

Open: the page assumes the bridge is bound within ~1.5 s of load; if the local HTTP server is slow while models load (fonts took 9 s in one run) the first calls fall back to the preview mock and fail. The data reloads when the bridge appears, but the root cause (server starvation during heavy start-up) is not fixed.

## 9. Fixes from the second round of real use
| Report | Cause | Fix |
|---|---|---|
| "close terminal" -> "No running process found matching 'cmd.exe'" | the alias mapped "terminal" to `cmd`; on Windows 11 the window is `WindowsTerminal.exe` hosting PowerShell/cmd tabs | `close_application` tries WindowsTerminal / OpenConsole / powershell / pwsh / cmd for "terminal"; "command prompt" only cmd; "powershell" only PowerShell. SARA's own launcher chain stays protected, so it cannot close the terminal it runs in |
| "play music" opened YouTube; "pause music" while paused started playing | `play music` matched the YouTube pattern (query = "music"); pause and play shared one blind play/pause key | generic nouns ("music", "the song", bare "play/resume/pause/unpause") now route to `play_pause_media`; the dispatcher picks `pause_media` / `resume_media` from the words, which ask the Windows media session first ("It's already paused.", "Resumed the music."). Real songs still search. Verified against Spotify |
| Security card has no "more" | button only appeared with 10+ events | always shown; says so when the log has nothing more |
| Wi-Fi toggle needs Administrator | `netsh interface set` is admin-only | Windows radio API (`Windows.Devices.Radios`, what the Settings quick toggle uses) first, netsh as fallback; also used by "wifi on/off" voice commands and Bluetooth on/off. Tested live: off and on, no admin |
| Lyrics ahead of the song, the sung line scrolls out of view | `offsetTop` was measured from the music card (the scroller had no `position:relative`), so every line looked ~400 px lower | scroll target computed from bounding rects; scroller is `position:relative`. Verified: active line within 4 px of the centre while the song plays |
