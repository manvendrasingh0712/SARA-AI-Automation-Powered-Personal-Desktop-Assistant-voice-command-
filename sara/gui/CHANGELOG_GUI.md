# GUI audit and upgrade - changelog

Work order: `SARA_GUI_AUDIT_AND_UPGRADE.md`. Nothing here is committed yet (the owner decides when).
"measured" = numbers from this PC through the Chrome DevTools protocol on the real running app (Windows 11, 144 Hz display).
"verified live" = checked in the running app. "not heard" = nobody listened to the sounds: sound quality is unverified by ear.

## What was different from the work order
The work order was written for an older GUI. Already done in this repo before it started: Phase 3 (feature folders under `style/` and `js/`,
no "-plus" files, one bundle: `index.html` loads only `bundle/app.css` + `bundle/app.js`), P1-8 (`__pycache__/` is ignored, none tracked),
P2-6 (one script tag). Not applicable: P2-4 "start loops only when the page is active" (inactive pages are `display:none`, so their
animations already cost nothing). Node tests: 86 (not 77).

## Phase 1 - safe fixes
| ID | Change | Files |
|---|---|---|
| P1-1 | `.home-hint` and `.b-rank` text uses `--muted` (contrast >= 4.7) instead of `--faint` (2.1-3.2) | style/home/home.css, style/settings/settings.css |
| P1-2 | removed the dead `au-sweep` effect (hidden by flow.css anyway) | style/fx/motion-pack.css |
| P1-3 | removed the 14 losing duplicate `@keyframes` (the last-loaded one always won, so nothing changes visually); `uniq -d` now prints nothing | layout.css, components.css, chat.css, settings.css, clock-card.css |
| P1-4 | VERIFY FIRST: no `<img>` in the markup or in JS, so the `img` fade rule and `au-fade` were removed | style/fx/motion-pack.css |
| P1-5 | comments that named deleted files (premium.js, aurum.css, flow.js ...) now name the current files | flow.css, quality.js, motion-pack.js |
| P1-6 | global `prefers-reduced-motion` safety net for the 11 CSS files that had none | style/core/smooth.css |
| P1-7 | `<html lang>` follows the language chip (hi / en) | js/settings/settings.js |
| P1-8 | already done | - |

## Phase 2 - performance (measured)
Renderer CPU = share of one core, Balanced tier, 8 s samples.

| Situation | Before | After |
|---|---|---|
| Home, pointer moving | 27.9 % (style recalc 16.0 %) | **10.9 %** (0.1 %) |
| Settings, pointer moving | 82.8 % (style recalc 54.4 %) | **29.7 %** (3.9 %) |
| Home idle | 14.6 % | 12.5 % |
| Home, Sara active | 11.4 % | 9.4 % |
| GPU, Home idle, High tier (sum over WebView2 processes) | 15.1 % | **7.4 %** |
| GPU, Balanced / Low | - / - | 7.6 % / 4.0 % |

| ID | Change |
|---|---|
| P2-1 | The cause was `--mx/--my` being rewritten on `:root` at every pointer move (inherited by the whole document, and read by the settings buttons by accident). Removed the cursor-parallax block in `js/fx/fx.js` and its use in `ambient.css` / `theme-neon.css` (fixed gradient positions); `sara-active` now fades opacity instead of animating `filter`; the nebula drift is paused in the Low tier. Side effect: Settings buttons no longer drift a few pixels with the mouse position (that was unintended). |
| P2-2 | `backdrop-filter` removed from the permanent surfaces (dock and the Home cards); it stays on overlays, the palette, memory view and the opt-in "glass" clock theme. Measured by switching blur off live: GPU 15.1 % -> 4.6 %. |
| P2-3 | Balanced tier: magnetic buttons and click sparks are now High-only (notes updated). |
| P2-4 | The 42 s orbit ring around the orb runs only in the High tier. |
| P2-5 | **Conflict to know about:** the work order says not to touch the orb renderer. In the earlier "Part 3" round the orb loop was already capped at about 72 fps (it drew at the 144 Hz refresh rate and animated per frame): CPU on Home roughly halved. Visible effect: on a 144 Hz screen the orb moves at half its old speed. To undo: remove the `ORB_MIN_GAP_MS` lines in `js/home/home.js`. |
| P2-6 | not applicable (one script tag) |

## Phase 4 - sound engine (verified live, not heard)
`js/core/ui.js`: the old oscillator beeps were replaced by a Web Audio engine: master volume, soft low-pass, short synthetic reverb,
compressor, layered partials, 4 packs (Glass, Soft, Minimal, Sci-Fi), 21 events, per-event throttling, silent while the window is hidden.
The old API (`tap toggle wake sent received added done notify run saved error tone on set`) is unchanged.
- New events are played only on transitions: idle->listening, leaving listening, entering thinking/working, entering speaking
  (the first status after start-up only seeds the state), boot finished, page switch and theme switch (both wait for the first real click or
  key, so start-up stays silent), slider ticks on the mic/speech/volume sliders, a success sound for `SARA.ok()` only when no other sound
  just played (the saved/done/added call sites already have their own).
- Settings > Voice: "Sound volume" slider and "Sound pack" chips under "Sound effects" (stored per device in localStorage, like quality).
- Checked in the running app by counting oscillators: old API present; Glass wake = 9, Minimal wake = 3; 10 taps in a burst = 1 tap;
  sound off = 0; transitions and page switch as described; no console errors.
- Note: the legacy `tone()` no longer multiplies by the volume a second time (the bus already does).

## Phase 5 - themes
- P5-1: closest dark-blue pair was Midnight vs Cyber Blue (core + panel distance 58; Midnight vs Neon 87, Cyber vs Neon 81). Cyber Blue was removed; a saved `cyber` now maps to Deep Ocean (`RENAMED` in theme.js); unknown ids still fall back to Terrarium.
- P5-2: added Obsidian Gold, Deep Ocean, Sunset Glass, Forest Night (tokens.css, theme.js LIST, `ui_prefs.py` list, the theme test). Contrast against the panel, text / text-soft / muted: Obsidian 14.2 / 8.2 / 5.8, Ocean 13.7 / 7.5 / 6.0, Sunset 14.4 / 8.0 / 5.8, Forest 14.2 / 8.3 / 6.4 (limits 7 / 4.5 / 4.5). All 14 older themes also pass. Theme grid shows 18 cards with real mini-previews (screenshot checked).
- Not done: P5-3 optional themes (auto day/night, holo).

## Phase 6 - animations (verified live)
Most of the list already existed in this GUI, so only the gaps were added, as one new switchable group `p6` ("Motion polish" in
Settings > Appearance > Fine-tune effects; off in the Low tier, under the OS reduced-motion setting and under the app's own reduce-motion
accessibility setting). Everything uses transform / opacity (hover also the existing `--shadow-hover`), no new infinite loops.

| ID | Result |
|---|---|
| P6-1 orb state morph | already there: the canvas eases colour and weights per mode, halo fades .7 s, sweep .6 s. Nothing added. |
| P6-2 voice-reactive orb | already there end to end: backend `audio_level` ("mic" while listening, "tts" while speaking) -> `audioLevelTarget` -> orb energy and halo scale. Nothing added. |
| P6-3 staggered entrance | added: Apps grid tiles and list rows enter 40 ms apart (first 8) during the short `stagger-in` window; the window was lengthened from 450 to 700 ms so the 8th item finishes. Checked: delays 0 / 0.08 / 0.28 s on tiles 1 / 3 / 9; none in Low tier; none with `p6` off. Settings cards already had their own stagger. |
| P6-4 check draw | already there for toasts (`tCheck`). Not added to Save/Done buttons. |
| P6-5 streaming glow | not done on purpose: a colour fade is not transform/opacity (the work order's own rule), and the chunk fade (`chunkIn`) already exists. |
| P6-6 spring buttons | not done: buttons already use a spring curve (`--lx-spring`, overshoot) for press and release; swapping it for `linear()` would change many other elements. |
| P6-7 boot -> orb reveal | added: `.orb-enter` (scale .85 -> 1, fade, 600 ms) once after the boot overlay; class is removed after 800 ms. Checked the animation applies; the first real start-up with it was not watched by eye. |
| P6-8 page transition | already there (`pageIn/InR/InL`); no outgoing fade added (it could delay input). |
| P6-9 number count-up | already there (`.an-kpi b`, `.perf-num`); added the Today card temperature `.tc-temp`. |
| P6-10 hover depth | added for the Home cards (today, next, plan, activity): 2 px lift (`translate`, independent of the tilt) + `--shadow-hover`. Checked: hover = 0 -2px, leave = none. |

Note found while testing: in this PC's WebView profile the app's own reduce-motion setting is on and the groups `ax pm lx au` are switched
off (saved in the WebView's localStorage), so on this PC none of the entrance / hover animations are visible until that is changed in
Settings > Appearance / Accessibility. I tested with the setting temporarily off, without saving it.

## Phase 7 - loading states (verified live)
New `js/shared/skeleton.js` (listed in assets.json after empty-states.js): `SARA.skeleton.fill(el, n, kind, ms)` / `clear(el)`, reusing the existing `.skel` shimmer.
A skeleton can never stay forever: after 10 s it becomes "Still loading - this is taking longer than usual."; failed loads keep the existing error cards.

| Place | Result |
|---|---|
| Automation routines list | 3 shimmer rows while `list_routines` runs, only on the first load (a filled list is never blanked). Checked: 3 rows during the wait, 0 after the data, list kept during a refresh, "Still loading" line after 10 s for a call that never answers. |
| Today card (weather) | the "--" temperature shimmers while `get_weather` runs, restored to "--" if it fails or after 10 s (the weather renderer overwrites it when data comes). |
| Memory view | the plain "Loading..." word became 4 shimmer rows (same element, removed when `mem2_list` answers). Checked 4 rows during the wait, 0 after. |
| Apps grid | not done on purpose: it is built synchronously from a fixed list, there is nothing to wait for. |
| Chat history | not done on purpose: the chat does not fetch a history on start (the log starts empty), so a skeleton would be fake. |

## Phase 8 - features (verified live)
Two new fine-tune switches (Settings > Appearance > Fine-tune effects): `hoverpreview` and `hints` (both off in the Low tier). New files:
`js/fx/theme-extras.js`, `js/home/hints.js`, `js/shared/whats-new.js` (all in assets.json). Per-device flags are in localStorage (the backend only
stores theme / accent / clock theme).

| ID | Result |
|---|---|
| F1 sound packs + volume | done in Phase 4 |
| F2 theme hover preview | done: pointing at a theme card (140 ms) shows that theme on the whole app, nothing is saved; leaving, clicking, hiding the window or changing page restores it. Checked: hover Sunset -> page shows Sunset while the real theme stays; leave -> restored. |
| F3 idle hints | done: on Home, only while Sara sleeps / waits for the wake word, the hint line cycles through 7 things you can say plus the original line every 8 s with a fade; it adopts any text the backend writes, and does nothing while awake, hidden or on another page. Checked: rotates when idle, unchanged while listening. |
| F4 shortcuts overlay | already there: `?` opens a list of real shortcuts (js/shared/a11y.js). No change. |
| F9 mood / time-aware glow | not changed: the glow already follows time of day and brightens whenever Sara is active; a separate listening / speaking level would need a new animated layer. |
| F10 press feedback | not changed: buttons already scale on press with a spring curve and the click sounds already play from the existing handlers; a global press sound would double them. |
| F11 auto day / night | done as a toggle (not as a 19th theme card, so the saved theme list and the backend list stay as they are): Paper 06:00-18:00, your dark theme at night, applied without saving, re-checked every minute; picking a theme yourself turns it off. Checked: day/night switch with a simulated clock, saved theme untouched, user pick turns it off. |
| F12 what's new | done: a sheet shown once per release id (`RELEASE` in whats-new.js), 3 s after start-up; a fresh install shows nothing. Checked: 5 items, Esc closes, screenshot looked right. It will show once on your next start. |
| F7 usage mini-graph | done (`js/home/usage-card.js`): a "Last 7 days" card top right on Home, seven bars + the total, from the existing `get_analytics_dashboard` call (refreshed when Home opens, at most once a minute); hidden when there is nothing to show and on windows under 900 px; click / Enter opens Settings at the trend chart; bars grow with transform only. Checked with this PC's real data (71 commands, bars + weekday letters, screenshot looked right). Note: the analytics file counts the commands my test runs sent too, so the numbers for 28-31 Oct are inflated. |
| F8 drag-to-reorder apps | done (`js/apps/apps-order.js`): tiles in "Open" and "Quick tools" can be dragged, or moved with Alt + arrow keys; every change shows a toast with Undo (one toast per burst of key presses); "Reset order" in the hint line; saved per device by tile label (survives restarts, unknown / new tiles go to the end). Checked: drag handlers (synthetic drag events), Undo, keyboard moves with focus kept, one toast per burst, order restored after an app restart, reset. A real mouse drag with the native OS drag was not exercised. |
| F5 onboarding tour | done (`js/shared/tour.js`, styles in the new `style/shared/guide.css`): a 5-step spotlight tour of Home (orb, page dock, Today card, Settings, two shortcuts). Offered once after the first-run setup wizard closes (the wizard now emits `setup_done`; people who finished it earlier are not interrupted), skippable (Skip, Esc, click on the dark area), Back / Next / Done, keyboard + focus handled, replay from Settings > Window & setup ("Replay tour") or the palette ("Take the quick tour"). Checked: not offered for `first:false`, offered for `first:true`, 5 steps, Back, Done sets the flag, not offered again, replay, Esc; screenshot looked right. The real first-run path (wizard on a fresh profile) was triggered by emitting the event, not by clearing the wizard flag. |
| F6 radial menu on the orb | done (`js/home/radial.js`): press and hold the orb ~0.5 s, six actions fan out (Notepad, Calculator, Chrome, Screenshot, Spotify, Task Manager), run through the Apps page's own function (`SARA.apps`, exported from apps.js) so nothing new goes to the backend. A normal tap still wakes Sara; the click that ends a long press does not. Esc / tap outside / page change / hidden window close it; arrows + Enter work; palette entry "Quick actions"; Fine-tune switch `radial`. Checked with real mouse events: tap = 1 wake, long press = menu with 6 items and no wake, item click ran the action (stubbed so nothing real opened) and closed the menu, press + move cancels, switch off disables it; screenshot looked right. |

## Not done (and why)
- Nothing left from Phase 8.
- Backend hygiene (section 11): the work order says to do it on a separate branch and not to mix it with GUI work.
- Phase 0 as written (clean git, new branch, per-task commits): the working tree already holds earlier uncommitted work, so no branch or commit was made.

## Checks run
86 node tests pass (28 + 11 + 47), `node --check` on every JS file, `python -m sara.gui.build` rebuilt, Python suite 3760 passed.
Bundle sizes: app.css 307,051 -> 307,194 bytes; app.js 533,690 -> 540,900 bytes (the sound engine and settings controls).
