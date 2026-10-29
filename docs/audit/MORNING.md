# Good morning — what changed while you slept

Everything below is already in `C:\project` and verified by byte-hash.
Backups are in `C:\project\backups\pre-batch-*\`.

**Suite: 2719 passed, 19 skipped, 0 failed.** Router bench **99.3% /
en 99.1% / hi 98.9% / hinglish 100% / 0-of-60 false triggers** — up from
98.8% / 98.2%, checked row-by-row after every change. Full detail for
every item is in `FINDINGS.md`.

**The latency you reported is fixed and reverted — see D71 below.** You were
right and I was wrong: I had cut your capture gate 2.6× on the strength of a
number measured in the wrong program. Your gate is back where it was.

---

## Already done, thanks to you

**The TTS GPU install worked.** Your log shows
`ORT provider : active=CUDAExecutionProvider`, so Kokoro is synthesising on
the GPU now. That was the largest controllable share of time-to-first-audio
after the LLM (`synthesis -> first audio` was p50 **1250 ms** on CPU). The
warning line about a missing `CUDAExecutionProvider` is gone, which is how
you can tell.

---

## Two things only you can do

### 1. Turn on telemetry — everything else is blocked behind this

Your `.env` has `TELEMETRY_ENABLED=False`, which beats the `True` default in
`config.py`. Change that one line:

```
TELEMETRY_ENABLED=True
```

Your GUI tracing switch was already **ON** — that is why the earlier
"turn it on in the GUI" advice was wrong, and why SARA's boot line now names
*both* sources and marks which one is doing the disabling (D68).

This matters more than it sounds: **no voice turn has ever been recorded.**
The 16 rows in the store are all `src=text`. So every latency claim in this
audit that is not a direct measurement from your pasted logs rests on
reasoning, not on data — including my D65 mistake. With this on, the next
change gets decided by two numbers instead of by my argument.

### 2. Test with the music off

In the session you sent, YouTube was playing at RMS **980** against a gate
of 278. That one fact explains most of what looked broken in it: the 5.58 s
`'Yes.'`, both Thai hallucinations, the 0.12 s capture, the wandering
language detection and the marginal 0.6586 wake score (D70). SARA now says
so in the log instead of calibrating silently and failing — but I cannot
separate music from a real latency problem from here.

### Then run it and read four lines

```powershell
python main.py
```

Say "Sara", then "open chrome":

| line | what it tells you |
| --- | --- |
| `[Telemetry] recording ON -> ...` | if still **OFF**, the line now names which of the two switches did it |
| `[Debug] Mic sensitivity 23% ... OVERRIDES ... 300 -> 793` | your saved slider beating `.env`; expect a start gate of **278**, then **225** after the 15 s calibration |
| `[STT] preview took NNNms -> endpoint fires ~NNNms ...` | now says per command what the preview was actually worth — `saved NNms`, `free`, or `changed nothing`. Expect a mix, and mostly small numbers (D72) |
| `[TTS v12] Ready \| ... AEC: wired` | `provided but DISABLED` would mean echo cancellation is not actually running |

Then, after 15-20 commands:

```powershell
python -m bench.voice_latency
```

That is the baseline. Once it exists, the adaptive gate can be settled by
measurement rather than reverted by feel.

---

## What I fixed

### The adaptive gate: switched on, then switched back off (D65 → D71)

**Current state: off.** Your gate is back to where it was when the pipeline
felt fast. You do not need to change anything.

I had turned on `VOICE_GATE_NOISE_RELATIVE`, which derives the start gate
from the measured noise floor instead of a fixed number. You then reported
the batch after it felt slower — and you were right. I had justified the
change with the wrong number.

**The mistake.** I argued the new rule was safe because it lands on 96–132
in your room, "within a few percent of the gate 105 your successful run
used". But 105 was what `bench/stt_doctor.py` ran at — and **the doctor does
not load your GUI preferences**. The app restored your saved slider (23% →
threshold 793) and ran at a gate of **278**. So I cut the app's gate by
**2.6×** using a number measured in a different program.

**Why that reads as lag.** A lower gate opens captures on typing, a click, a
chair, a breath. Each false start burns a whole listen window (480–800 ms),
returns nothing, and retries. Speak during one and your command gets clipped
and needs repeating. That feels like latency, not like a threshold being
wrong.

Both defaults had to move, because turning the rule off alone wasn't enough
— the fast first calibration skips the ±150 step clamp on purpose, so even
on the old formula it jumped the gate to ~98 anyway:

| setting | was | now |
| --- | --- | --- |
| `VOICE_GATE_NOISE_RELATIVE` | on | **off** |
| `STT_RECALIB_FIRST_FAST_S` | 1.5 s | **0** (disabled) |

What you get now: gate **278** at startup, **225** after the first
calibration at 15 s, drifting down gradually over long sessions. That is the
original behaviour. Worth knowing: `STT_RECALIB_FIRST_FAST_S` was *already*
1.5 before all this, but that pass always lost a race with the TTS warm-up,
so it never actually ran — setting it to 0 just makes explicit what was
already true by accident.

**Nothing was deleted.** The formula, the retry loop and all the tests stay.
Only the two defaults moved, because the margin argument was never tested
against the real thing on your machine. To settle it properly:
`TELEMETRY_ENABLED=True`, one session on the current gate, one with
`VOICE_GATE_NOISE_RELATIVE=True`, then compare the two numbers instead of
arguing from margins. Your `.env` `BARGE_IN_ENERGY_THRESHOLD=300` is back to
mattering, but the saved slider still overrides it — that's D58's log line.

### D72 — the endpoint is already nearly as fast as it can be

This is the other thing re-checking turned up, and it is good news
dressed as a correction.

I had claimed a **288 ms per command** saving was sitting unclaimed because
the preview transcribe was too slow. That number was wrong twice over, and
replaying the real decision code — rather than reasoning about it — is what
showed both.

**First:** a slow preview is not wasted. The endpoint re-checks for the
preview result every 32 ms, so a late result is still used the moment it
arrives. Being over the "budget" costs you the overshoot, not the whole
saving. My own log line said `TOO SLOW` seven times out of seven while the
fast path was in fact firing seven times out of seven.

**Second — and this is the one that matters:** the deadline the preview
races against is not fixed. SARA adapts it to how you have been speaking:

| how you have been speaking | deadline |
| --- | --- |
| first command of a session | 800 ms |
| **short commands, one after another** | **450 ms** |
| after long dictation | 900 ms |

When you fire "open chrome", "open vs code" back to back, SARA has
**already** dropped its own deadline to 450 ms — so the endpoint can never
take longer than 480 ms, and the absolute most a perfect preview could save
you is **128 ms**. Four of your seven measured previews saved nothing at
all, because that 450 ms deadline ended the turn before the preview even
came back.

So: **there is no large win hiding here.** I had two candidate speed-ups
for the preview decode (dropping its temperature-retry chain and its
325-character prompt, neither of which the warm-up path pays). I did **not**
ship either. The ceiling is 128 ms in your normal use, I cannot measure
either one from here — no `faster-whisper`, no GPU, no model weights in the
audit copy — and shipping an unmeasured speed-up against a small ceiling is
precisely the mistake that caused the latency you felt. Noted in
`FINDINGS.md` as an option if the baseline ever says it is worth it.

What you get instead is a log line that tells you the truth per command:

```
[STT] preview took 218ms -> endpoint fires ~384ms (ideal 352ms, +32ms);
      saved 96ms against the 450ms base rule
[STT] preview took 520ms -> endpoint fires ~480ms (ideal 352ms, +128ms);
      the 450ms base rule fired first, so this preview changed nothing
```

### D74 — the big one: "don't do X" was doing X

I went looking for accuracy gaps in the command router and found this. It is
the most serious thing in the whole audit.

SARA has a guard that catches negated commands — you say *"don't open
chrome"* and it should refuse. The guard works by checking the verb against a
list of about 30 words. But SARA has **~130 commands**, and the verbs they use
were never added to that list. Every new command shipped its pattern and not
its verb.

I measured it with your own golden command set: 840 negated phrasings, built
by putting "don't", "do not" and "never" in front of every command.
**558 of them executed anyway.**

Including these:

| | |
| --- | --- |
| `do not empty recycle bin` | **emptied the recycle bin** |
| `do not hibernate my pc` | hibernated |
| `do not put the computer to sleep` | slept |
| `do not lock my laptop` | locked |
| `do not press the enter key` | pressed enter |
| `do not paste it` | pasted |
| `do not cancel all my reminders` | cancelled them |

**Now 0 of 840.**

The fix stops using a word list. When an utterance starts with a negation,
SARA strips it and asks its own router whether what remains is a command — so
it can never fall behind again, including for commands added later:

```
"do not empty recycle bin"  ->  "empty recycle bin"  ->  that IS a command
                            ->  so this is a refusal ->  don't run it
```

And it can't over-trigger: *"don't worry about it"* leaves "worry about it",
which isn't a command, so nothing happens. I checked 27 phrases like that,
plus `do not disturb`, `never mind`, `type i don't like it` and
`play never gonna give you up` — all still fine. Negative tests still
0-of-60 false triggers, and the redteam bench is still 100% recall / 0%
attack success.

**One half is deliberately not fixed.** Hinglish puts the negation at the end
("… mat karo"), and my first attempt broke a real command immediately: the
"ना" in *"बिल भरना"* ("pay the bill") got read as negation. Worse, a trailing
"na" in Hinglish usually means *please* — "volume up kar do na" is you asking
more firmly, not refusing — so treating it as negation would refuse commands
you actually want. That needs its own measurement; it's recorded, not guessed
at.

### D78 — "take screenshot" didn't work unless you said "take **a** screenshot"

The only pattern was `take a screenshot`, with the "a" required. So:

| you said | before |
| --- | --- |
| `take a screenshot` | ✓ |
| `take screenshot` | chat |
| `take screen shot` | chat |
| `capture screen` | chat |

Dropping an unstressed "a" is about the most common thing speech-to-text
does, and Whisper writes "screen shot" as two words as often as one. Your
bench had this flagged too ("dropped article in speech-to-text").

All four work now, plus bare "screenshot". Bench **99.2% → 99.3%**
(English 98.8% → 99.1%).

### One I decided NOT to fix, and why (D79)

`type hello world` goes to chat — dictation needs a colon
(`type this: hello world`). Your bench has a row for it, so fixing it would
have been another easy point.

I simulated the fix before writing it, and it would have typed the wrong
thing on 5 of 12 test phrases:

| you'd say | SARA would type |
| --- | --- |
| **`type faster`** | "faster" |
| **`type a letter for me`** | "a letter for me" |
| **`type the following`** | "the following" |
| **`type up my notes`** | "up my notes" |

And typing isn't like answering — it goes into whatever window has focus,
so wrong text can land in a document or a terminal. `type the following` is
the worst one: you're about to dictate, and SARA types your preamble.

I could have added a list of words to exclude, but that's a hand-kept list
standing in for judgement — which is precisely what caused D74 (the
"don't do X" bug), and that took 558 broken commands to surface. One bench
point isn't worth it. The colon is cheap to say.

### D77 — "set a timer for ten minutes" never worked

This one is my favourite, because of *why* nobody could have spotted it from
the bench.

The duration parser only understood digits. So:

```
"set a timer for ten minutes"   ->  "Sorry, I couldn't understand
                                     the duration 'ten minutes'."
"set a timer for half an hour"  ->  same
```

Which means the only way you can actually give SARA a duration — by saying
it — didn't work.

**And the bench scored it as a pass.** The bench checks whether the right
*command* was recognised, not whether the *number* inside it was understood.
SARA recognised "set a timer" perfectly every time and then threw the
duration away.

Your own bench has a row for a rambling sentence with a command buried in
it — *"...anyway can you please set a timer for fifteen minutes because the
tea is on the stove"* — marked as passing. No timer was ever set.

Now these all work: `ten minutes`, `forty five minutes`, `an hour`,
`half an hour`, `one hour thirty minutes`, `ninety seconds`. Digits are
untouched.

I did **not** apply the same conversion to command recognition, even though
it would fix one more bench row (`set volume to fifty`). It would also turn
"open one note" into "open 1 note", and one bench row isn't worth risking
your app names on.

### D76 — "close window" was closing the wrong thing. So was "close windows explorer"

Same pattern, two bugs pointing opposite ways.

It only accepted "close **this/the/current** window". So:

| you said | SARA looked for an app called |
| --- | --- |
| `close window` | "window" |
| `close active window` | "active window" |
| `close the active window` | "active window" |

On Windows that isn't a harmless miss — "window" is part of real process
names, so it could close something you didn't mean.

And in the other direction, because the pattern had no end boundary,
**"close the windows explorer"** matched "close the window" *inside* it —
so asking to close Windows Explorer closed whatever you were looking at.
"close windows terminal" and "close windows security" escaped only because
they don't have a "the" in them.

Both fixed; bench **99.0% → 99.2%** (English 98.5% → 98.8%). Nothing in the
590-row bench covered the second one — found by probing nearby phrasings,
same as D73.

### D75 — "turn down the volume by 20" was making it louder

Found by checking D73 all the way through to what actually runs, instead of
stopping at "the router recognised it".

`set_volume` handles both "set it to 40" and "turn it up by 10", and the
handler treated **every** number as a target:

| you said | SARA did |
| --- | --- |
| `turn up the volume by 10` | set it to 10 — **quieter** |
| `turn down the volume by 20` | set it to 20 — from 10, **louder** |
| `increase the volume by 15` | set it to 15 |

So asking for a nudge one way gave you a jump the other way. Now a number
counts as a change only when you say "by" — "to 40" still means 40.

Two things worth knowing: this handler had **no tests at all**, which is how
a wrong-direction action survived in a shipped command. And brightness never
had the bug, because there "increase brightness" is its own separate
command — which is the shape volume probably should have had.

### D73 — "volume up" didn't work

Your `set_volume` patterns all want the verb first ("turn up the volume"), so
bare **"volume up"** went to chat. Your own bench file had it flagged as a
"known miss". Fixed, and the bench went **98.8% → 99.0%** (English 98.2% →
98.5%).

Worth mentioning because the obvious fix was wrong: written the simple way it
matched "volume up" *inside* other sentences, so **"don't turn the volume
up"** started turning the volume up. The 590-row bench reported zero
regressions while that was broken — those phrases just aren't in it. I found
them by hand-probing nearby phrasings, which is now the habit.

### Two bugs I caught in my own work

**D63 — enabling the gate made interrupting SARA impossible in one room.**
Barge-in multiplied the *same* threshold, and under D65 that threshold
tracked room noise. At noise floor 164 the barge-in gate landed at 2971
while your speech measured 2337 — above your own voice. Barge-in is now
capped at `BARGE_IN_ENERGY_THRESHOLD`: it can get *easier* in a quiet room,
never harder. Floor 164 goes from 0.79× (impossible) to 2.43×.

D71 switched off the condition that triggered this, so it is dormant rather
than live — but the cap stays, because the trigger comes back the moment the
adaptive gate is re-enabled, and the cap is correct either way.

**D64 — SARA could interrupt herself.** `barge_watch._aec_available()` tested
that an AEC object exists, not that it works — the same defect I fixed in
`core_wiring` as D42, in a file D42 did not touch. It decides whether to
apply the no-AEC barge-in boost that stops SARA's own voice triggering
barge-in, so it was skipped exactly when it was needed. Your machine has
working AEC, so this was latent for you.

### Smaller

* **D40** — `VAD_AGGRESSIVENESS` is a real `Config` field at last. Two code
  comments claimed it already was; it was declared nowhere, so `.env` was
  silently ignored. Clamped to 0–3, because `webrtcvad` raises outside that
  and is constructed at boot.
* **D51 → D72** — the preview transcribe is now timed, and your run gave
  the distribution: **218, 282, 313, 344, 375, 390, 485 ms**. My reading of
  those numbers was wrong twice, and correcting it is the most useful thing
  that came out of re-checking. See the D72 section below — short version:
  the endpoint is already close to optimal when you fire commands one after
  another, so there is much less to win here than I claimed.
* **D66** — three startup log lines each reported a different threshold and
  none said which was live. They now agree. One of the misleading lines was
  mine from the previous batch.

---

## The self-review you asked for — it found four of my own errors

| I had written | the truth |
| --- | --- |
| D46/D47/D49/D52 "OPEN" | all already **fixed** — a context compaction dropped the record and I wrote over it |
| D51 "the reported rule is not the rule applied" | **wrong** — the log was honest, the preview was just slow |
| D53b "the last turn of every session is lost" | **wrong** — tested properly, a tool-only turn persists at t+23 s via the idle sweep |
| a new multiplier of K=2.0 for the gate | **worse** than the K=4.0 already in the code — I had paired each noise floor with speech from a *different* session |

I also reused three finding numbers (D60/D61/D62) that earlier work already
owned; renumbered to **D65/D63/D64**. All of this is recorded in
`FINDINGS.md` rather than quietly corrected.

### Verified sound rather than assumed

* every remaining webrtcvad call site is **energy-first** — after D39/D45 the
  VAD can no longer open or hold a capture by itself, anywhere
* the telemetry sweep and `atexit` flush both work (tested to t+28 s)
* the TTS prewarm **does** yield to playback and leaves a 30 ms gap — no
  defect there, despite looking like one
* ollama is **genuinely** incremental streaming, with `think=False` set
* 7 full-suite runs, 3 under deliberate saturation of every CPU core

---

## Still open, and why

| item | blocked on |
| --- | --- |
| ~~**D55** TTS on CPU (1250 ms)~~ | **done** — your `onnxruntime-gpu` install took |
| ~~**D51** the 288 ms endpoint saving~~ | **withdrawn** — the figure was wrong, see D72. The real ceiling is 128 ms in your normal use, and not worth the risk |
| **D65** the adaptive gate | reverted by D71; needs a telemetry baseline before it can be tried again |
| loud-room calibration | recorded, unfixed: the gate can be lowered in a quiet room but never raised in a loud one, because calibration bails when the room averages over half the threshold while the noise floor only samples below 0.4× it (D70) |
| **D57** Latin-script hallucinations (`'Kanu hermekleyenli.'`) | the `medium` model — you are at 644 MB of ~1.5 GB. The obvious fix was **disproved**: gating on detected language would have discarded a correct `'Open Chrome.'` that Whisper labelled German |
| **D54** Gemini quota | free tier is 20/day; resets ~6 h after you hit it. SARA no longer wastes ~4 s per turn retrying it |
| voice latency baseline | telemetry recording ON, then 15-20 commands |
| **D74** Hinglish negation ("… mat karo") | has the same word-list gap as the English side, but needs its own measurement — a first attempt read the "ना" in "भरना" as a negation, and trailing "na" usually means *please* |
| the other 6 bench misses | `type see you tomorrow`, `close window` → close_app, `why did you say`, `set volume to fifty` (spoken number words — a whole class: Whisper writes "fifty", patterns want digits), `take screenshot`, mixed-script Hindi alarm. Each has a real speech-origin story; none fixed yet |

### One decision I did not make for you

The phonemizer logs `language switch flags have been kept` on every
synthesis, which leaves language-switch markers in the phoneme stream and
could plausibly hurt Hinglish pronunciation. SARA does not configure
phonemizer — `kokoro_onnx` does, internally — so changing it means patching
a third-party library, and the result is only verifiable **by listening**,
which I cannot do. The experiment, if you want it: set
`language_switch="remove-flags"` in kokoro_onnx's phonemizer call and
compare Hinglish playback by ear.
