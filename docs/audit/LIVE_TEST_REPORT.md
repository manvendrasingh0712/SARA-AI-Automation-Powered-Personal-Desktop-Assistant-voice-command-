# Live practical test - 2026-11-01

Everything below was run against the REAL app (`run_app_safe.py`: real apps, volume, brightness, notes, LLM, TTS; only power /
lock / restart / recycle-bin calls are replaced by recorders). Audio was not listened to, so speech quality and the wake word with a real
voice are NOT covered here.

## What ran
| Test | Result |
|---|---|
| GUI + bridge sweep (`sweep.py`): 32 read-only bridge methods, 6 pages, 13 Settings sections, 18 themes, 3 quality tiers, 19 sound events x 4 packs, palette, `?` list, tour, radial menu, What's new, usage card, apps reorder, hints, auto day/night, note / reminder / timer / stopwatch / setting round trips, bundle served | 93 / 94 pass; the one "fail" is `get_media_volume` -> "No active media session" while nothing plays (correct) |
| Live chat plan, 449 commands through the app's own chat (system, calculator, weather, volume, brightness, timers, reminders, calendar, notes, to-do, windows, browser, media, memory, modes, skills, compound, LLM) | 410 pass, 11 fail, 6 skip on the first full run; see below |
| Fixed-code re-run of the first 110 commands | 109 pass, 0 restarts, no crash dump; the only fail is a web search while DNS was down |
| Items 458-479 (never reached in the first run) | 21 pass; "bye" ends the app on purpose |

## Real bugs found and fixed (all covered by new tests)
1. **Native crash of the whole app** (4 times in one run; reproduced 3 of 3 at the first alarm command). Cause, captured with
   `faulthandler`: the garbage collector released a stale `comtypes` COM pointer left behind by earlier volume / brightness commands on
   another thread -> access violation in `_ctypes.pyd`. Fix: `sara/core/com_thread.py` - volume, brightness and audio ducking now run on one COM
   thread. After the fix: 110 commands, 0 restarts.
2. "सुबह छह बजे का अलार्म लगा दो" (and "साढ़े / सवा / पौने / डेढ़ / ढाई", "subah chhe baje") -> "couldn't understand the time". Fixed.
3. "why did I change the volume" answered with the unrelated theme change. Fixed (needs a shared content word).
4. "youtube pe X chalao" searched for "pe X chalao"; "search youtube for X" searched "for X". Fixed.
5. "are you connected to the internet" -> "No, I don't have internet access". The prompt now states the assistant's tools; re-checked with the real model.

## Not bugs (test or environment)
- Notes Q&A (#133-137): that skill answers only from the folder `sara_class_notes/`, which does not exist on this PC ("0 files indexed").
- "forget zzqx" (#355): nothing was stored, so "couldn't find a memory" is the right answer.
- "open calculator" (#223): works; the plan expected another action name.
- Two Hindi / Google search items and one English one: the internet was flaky during the run (DNS errors in the log).
- "bye" / "goodbye" / "quit" end SARA by design.
- Browser keys (#247-253): skipped because Chrome was not the foreground window.

## Still open
- "who made you" -> the model answers "Alibaba Group" (the underlying model); no persona line was added for that on purpose.
- No microphone / speaker / wake-word test with a human voice.
- Reminder dates from the bridge are now validated (fixed, 13 tests).

## Final end-to-end run on the final code (18:00-18:27, one run, 447 of 449 commands)
Two items (#125, #126) were skipped on purpose: they create REAL events in your Google Calendar (a `token.json` is connected).
Result: 400 pass, 47 flagged. By cause:
- **35 are "shall I go ahead? Say yes or cancel."**: your saved Settings value `ui:security_mode` is **strict** (and `ui:theme` is Obsidian Gold),
  so every risky command (type, press key, close app, wifi off, forget memory, delete todo, run routine, cancel reminders ...) asks first.
  That is the designed behaviour of strict mode, and it shows the confirmation works; the same commands executed without a prompt in the
  morning run under the normal mode. They were NOT re-run in normal mode (that would be another 45-minute run).
- **#2 "hello sara"** (first chat after start-up): no reply within 75 s in the full run; in a separate single check it answered after 19.5 s.
  The window says "systems online" at about 35 s while the model is still warming up and the TTS phrase cache is being built (47 s of CPU).
  **Open:** the first turn after a cold start is slow; not changed.
- **Notes Q&A with a real notes folder** (temporary `sara_class_notes/sara_test_milk.md`, removed again): "what do my notes say about milk" and
  "summarize my notes on milk" answered from the note (pass); "quiz me on milk" -> "I couldn't make a quiz from that right now" (fail, the
  small local model did not return a usable quiz); "the answers" followed. **Open.**
- #53 "आज की ताज़ा खबरें सुनाओ" was sent to Spotify ("Opening volume 100 in Spotify") instead of the news. **Open bug** (Hindi news phrase).
- #54 web search: internet flaky (DNS). #223 and #381: test expectations (command works). #370/#371 "why did I change volume / brightness": now
  correctly say "I couldn't find a recent change", because volume and brightness changes are not recorded as settings decisions (the plan expected a record).
- **#395, #409** and the "bye" item #463: connection to the test page dropped while the python process was alive (the PC went to sleep for 27
  minutes around #409); "bye" ends SARA by design. **No native crash, `fault.log` empty**, whereas the first run had 4 real crashes.

## Side effects of testing on your PC (cleaned up afterwards)
- Settings the test commands had left behind were put back from the decision log (database backup taken first): muted, notifications,
  telemetry, the four proactive nudges, study / focus mode, mic sensitivity (gaming mode had left it at 20), speech speed, language mode.
  Not touched: the saved GUI theme.
- Today's test conversation turns (2,570 rows) and their 167 memory rows were removed; turns that are not in the test plan stayed.
  Older test days (29-31 Oct), the consolidated memory facts and the analytics counters were left alone.
- Two "project review" events (2 Nov, 4:00 PM) created by the test runs are still in Google Calendar: deleting them was blocked as an
  external write, so they need to be removed by hand.
- The plan's "delete all my notes" test empties `sara_notes.txt`; there is no notes file now.
- Volume was put back to 80 % and brightness to 70 %.

## Open issues round (after the final run)
| Issue | Result |
|---|---|
| #53 Hindi news phrase "went to Spotify" | The phrase itself routes to `news` (checked on the exact plan string). The Spotify reply belonged to a SEPARATE turn "spotify volume 100" that was logged 30 s earlier (microphone / ambient speech while the test ran). That turn exposed a **real bug, now fixed**: "spotify volume 100 / up / next / mute / ka volume ..." were taken as a track name and played; and "spotify pe X chalao" searched for "pe X chalao". Control words and Hinglish "pe/par/ka" are no longer track names (12 new tests). |
| Notes quiz "couldn't make a quiz" | **Fixed.** The model answered with all questions and answers correctly but on ONE line ("Q1: ... A1: ... Q2: ..."), and the parser needed line breaks. It now splits on the Q / A markers (3 new tests; the real model output of 3 tries parses to 3 pairs). Not re-run through the GUI. |
| Slow first reply after a cold start | **Measured, not fixed.** Timeline of the isolated run: command at 19:48:43, "Ollama started successfully" 19:48:46 (about 41 s after launch, while Whisper, Kokoro and the 52-phrase TTS cache load), the first model answer at 19:49:03: 18-23 s. The start-up greeting ("systems are online") is spoken before the local model is ready. My first idea (delay background memory work) did NOT help (23.1 s vs 19.5 s) and was reverted. **Fixed afterwards:** the start-up screen now waits for the model (message "Warming up the language model...", at most `STARTUP_WARM_WAIT_S` = 45 s, 0 = old behaviour) before "Ready" and the spoken greeting. Re-measured once: the greeting now comes at 50 s instead of 35 s and the first reply took **4.2 s** (was 19.5 and 23.1 s). |
