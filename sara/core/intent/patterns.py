"""
sara.core.intent.patterns
The regex pattern table and substring pre-filter gates for every fast-path
intent. Split into its own file since it is pure data (~1000 lines) and
changes far more often than the matching logic in engine.py.
"""

# ── Shared fragments for the "verb + free text" catch-all intents ─────
# (open_app, close_app, restart_application, switch_to_application) --
# see engine.py's _detect_intent_cached() for how these combine with the
# Layer-4 post-match rejection against _NON_APP_TOKENS below.
#
# LAYER 1 filler: a short, EXPLICIT list of polite/wake-word prefixes
# that may precede the actual command, anchored with `^` so the verb has
# to be the start of the (stripped) utterance -- not found anywhere
# inside it via .search(). Deliberately not a greedy `(.*)` prefix: an
# open-ended prefix would just reintroduce the exact "matches mid-
# sentence" bug this fix removes, since `.*` would happily eat any
# amount of leading chatter to reach the verb.
# D28 FIX (audit batch D22-a): the politeness words were here but the
# speech disfluencies were not, so "um restart chrome" and "uh switch to
# spotify" missed while "please restart chrome" worked. These are the
# fillers the golden set actually contains ("uh", "um"), plus their obvious
# lengthenings. Each alternative ends in a space and the whole group is
# anchored at ^, so this only ever strips leading words -- "um" cannot
# match inside "umbrella", and nothing mid-sentence is touched.
_DISFLUENCY = r"(?:uh+|um+|erm?|hmm+)\s+"
# Same fillers, but as an optional slot placed AFTER the command verb, so
# "uh open um notepad" captures "notepad" and not "um notepad".
_OPT_DISFL = r"(?:" + _DISFLUENCY + r")?"

# Calculator building blocks (E2E audit 2026-10-30): "what is 2400 divided by
# 8" answered "The answer is 2400." and "calculate 7 times 8 plus 3" answered
# "The answer is 7." -- the digit-only pattern captured the first number only
# and the answer was wrong without any warning.
_CALC_NUM = (
    r"(?:\d+(?:\.\d+)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety)"
)
_CALC_WORD_OP = r"(?:plus|minus|times|multiplied by|divided by|over|into|modulo|mod|to the power of|raised to the power of)"
_CALC_OP = r"(?:" + _CALC_WORD_OP + r"|\+|-|\*|/|\^|x)"
_CALC_EXPR = _CALC_NUM + r"(?:\s*" + _CALC_OP + r"\s*" + _CALC_NUM + r")+"

_FILLER = (
    r"^[\s,]*(?:please |sara |hey sara |can you |could you |zara |"
    r"ek baar |jara |" + _DISFLUENCY + r")*"
)

# LAYER 3 shape-bound app name: 1-4 whitespace-separated tokens, each
# starting with a letter/digit and otherwise made up only of letters,
# digits, `+`, `-`, or `.` -- covers real app names ("notepad++",
# "vs code", "7-zip", "microsoft word") while a token with any other
# punctuation, or a 5th+ token, simply can't be captured. This is
# enforced IN the regex (not as a post-match length/shape check) so a
# too-long or oddly-shaped phrase just fails to match here and falls
# through to "chat" or the next intent in _ROUTES, same as any other
# non-matching input.
_APP_TOKEN = r"[A-Za-z0-9][A-Za-z0-9+\-.]*"
# Repetition is LAZY ({0,3}?), not greedy: with an OPTIONAL connector
# elsewhere in some of these patterns (e.g. restart_application's
# "(?:ko )?"), a greedy capture backtracks from the top and happily
# accepts the first overall match it finds — which, for "chrome ko
# restart karo", is a 2-token capture ("chrome ko") once the optional
# "ko " simply matches zero times. Lazy repetition tries the SHORTEST
# capture first (1 token), succeeds against "chrome" + "ko " already
# there in the text, and only backtracks to a longer capture (e.g.
# "vs code") when the shorter one genuinely doesn't let the rest of the
# pattern match.
_APP_NAME = _APP_TOKEN + r"(?:\s+" + _APP_TOKEN + r"){0,3}?"

GENERIC_PLAY = r"(?:the |some |my |any |a )?(?:music|musics|song|songs|media|playback|track|tracks|gaana|gana|gaane)"

_INTENT_PATTERNS = [
    # ── Modes / Personas (voice-triggerable, NEW) ─────────────────────
    # "switch to study mode" / "study mode on karo" -- applies a fixed
    # bundle of EXISTING preference toggles (focus_mode, assistant_active,
    # and -- Gaming Mode only -- mic_sensitivity) via
    # sara/orchestrator/intent_handlers.py's _h_switch_mode(). Placed
    # FIRST in this list, deliberately, because two other broad
    # patterns further down would otherwise swallow phrases like
    # "switch to study mode" or "start gaming mode" before this group
    # ever gets a chance to run:
    #   - switch_to_application's r"switch to (?:the )?(.+?)..." (Window
    #     Management section below) would capture "study mode" as an
    #     app name.
    #   - open_app's catch-all r"start (?!https?://)([a-zA-Z0-9][\w\s]
    #     {1,40}?)(?:\s+app)?$" (Apps section, near the bottom) would
    #     capture "gaming mode" the same way.
    # Being first in this list guarantees switch_mode is always tried,
    # and always wins, before either of those.
    ("switch_mode", [
        r"switch to (?:the )?(study|work|gaming|home|normal|default|focus) mode",
        r"go to (?:the )?(study|work|gaming|home|normal|default|focus) mode",
        r"(?:turn on|enable|activate) (?:the )?(study|work|gaming|home|normal|default|focus) mode",
        r"(study|work|gaming|home|normal|default|focus) mode (?:on karo|on kro|chalu karo|shuru karo)",
        r"(study|work|gaming|home|normal|default|focus) mode (?:mein jao|mein switch karo|activate karo)",
        r"^(study|work|gaming|home|normal|default|focus) mode$",
    ]),

    # ── Undo Setting Change (voice-triggerable, NEW) ──────────────────
    # "undo my last change" / "revert my last setting" / Hindi equivalents
    # -- reverts the most recent sara/core/memory.py decision_log entry
    # back to its old_value via db.set_preference(), same mechanism as
    # switch_mode above. Placed early in this list (same defense-in-depth
    # reasoning as switch_mode's placement comment above) so it is always
    # tried, and always wins, before it could ever reach the EXISTING,
    # UNRELATED "undo" intent much further down (Keyboard / Typing
    # section) -- r"undo(?: that)?", which sends a keyboard Ctrl+Z to
    # whatever app is currently focused. That intent is untouched by this
    # feature and must keep matching bare "undo" / "undo that" exactly as
    # before.
    #
    # None of the phrasings below are bare "undo" or "undo that" -- every
    # one requires "last change" / "last setting" (or the Hindi
    # equivalent), so a bare "undo that" never matches here and falls
    # through, completely unaffected, to the old Ctrl+Z "undo" intent
    # further down the list, exactly as it did before this feature
    # existed. Verified by inspection: "undo that" does not contain
    # "last", "wapas", or "pichla"/"pichli", so none of these patterns
    # can match it.
    ("undo_setting_change", [
        r"undo my last (?:change|setting|preference)",
        r"revert my last (?:change|setting|preference)",
        r"undo (?:the )?last (?:change|setting|preference)",
        r"revert (?:the )?last (?:change|setting|preference)",
        r"meri last setting wapas karo",
        r"meri last change wapas karo",
        r"pichla change undo karo",
        r"pichli setting wapas karo",
        r"wapas karo jo maine (?:change|badla) kiya(?: tha)?",
    ]),

    # ── Reminders ──────────────────────────────────────────────────────
    ("reminder_add", [
        r"remind me to (.+) (?:at|on|in) (.+)",
        # Time before task ("remind me in 20 minutes to check the oven");
        # the lookahead keeps the handler's (task, time) group order.
        r"\bremind me (?=(?:in|after|at) .+? to (.+)$)(?:in|after|at) (.+?) to ",
        # Day / part of day without a clock time: "remind me tomorrow
        # morning about the meeting", "remind me about the meeting tonight".
        r"\bremind me (?=(?:tomorrow|today|tonight|this|kal|aaj)\b.*?\b(?:about|to) (.+)$)((?:tomorrow|today|tonight|this|kal|aaj)\b(?: \w+){0,2}?) (?:about|to) ",
        r"\bremind me (?:about|to) (.+?) ((?:tomorrow|today|tonight|kal|aaj)(?: (?:morning|afternoon|evening|night|subah|shaam|raat))?)\W*$",
        r"(?:mujhe\s+)?(?=\d{1,2}(?::\d{2})?\s*baje\s+yaad\s+dila(?:na|o|\s+dena|\s+do)\s+(?:ki\s+)?(.+)$)(\d{1,2}(?::\d{2})?\s*baje)",
        r"set (?:a )?reminder (?:to|for) (.+) (?:at|on|in) (.+)",
        r"alert me (?:to )?(.+) (?:at|on|in) (.+)",
    ]),
    ("reminder_list", [
        r"(?:what are|show|list|do i have) (?:my )?reminders?",
        r"any reminders?(?: for me)?",
        r"upcoming reminders?",
    ]),
    ("reminder_cancel", [
        r"(?:cancel|clear|delete|remove) (?:all )?(?:my )?reminders?",
        r"(?:stop|disable) (?:all )?reminders?",
    ]),

    # ── Timer ──────────────────────────────────────────────────────────
    ("set_timer", [
        r"^\W*(?:set |start )?(?:a )?timer\s+(\d+(?:\.\d+)?\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?))\b",
        r"set (?:a )?timer (?:for|of) (.+)",
        r"start (?:a )?timer (?:for|of) (.+)",
        r"timer (?:for|of) (.+)",
        r"countdown (?:for|of) (.+)",
    ]),

    # ── Alarm (clock-time, NEW -- distinct from set_timer's duration-
    # based countdown above) ────────────────────────────────────────────
    ("set_alarm", [
        r"set (?:an )?alarm (?:for|at) (.+)",
        r"wake me (?:up )?(?:for|at) (.+)",
        r"alarm (?:for|at) (.+)",
    ]),

    # ── Stopwatch (NEW) ──────────────────────────────────────────────────
    ("start_stopwatch", [
        r"start (?:the |a )?stopwatch",
        r"begin (?:the |a )?stopwatch",
        r"start timing",
    ]),
    ("lap_stopwatch", [
        r"\b(?:lap|split)(?: time)?\b",
        r"stopwatch lap",
    ]),
    ("stop_stopwatch", [
        r"stop (?:the )?stopwatch",
        r"end (?:the )?stopwatch",
        r"stopwatch time",
    ]),

    # ── Notes ──────────────────────────────────────────────────────────
    ("take_note", [
        r"take (?:a )?note[:\s]+(.+)",
        r"note (?:this )?down[:\s]+(.+)",
        r"remember this[:\s]+(.+)",
        r"save (?:a )?note[:\s]+(.+)",
        r"write (?:this )?down[:\s]+(.+)",
        r"jot (?:this )?down[:\s]+(.+)",
    ]),
    ("read_notes", [
        r"(?:read|show|list|get) (?:my )?notes?",
        r"what (?:are|did i write in) (?:my )?notes?",
        r"any notes?(?: for me)?",
    ]),
    ("clear_notes", [
        r"(?:clear|delete|remove|wipe) (?:all )?(?:my )?notes?",
    ]),

    # ── To-Do list (NEW, separate from Notes above) ──────────────────────
    ("add_todo", [
        r"\badd (.+?) to (?:my |the )?to-?do(?: list)?\W*$",
        r"add (?:a )?to-?do[:\s]+(.+)",
        r"add (?:a |this |that )?task[:\s]+(.+)",
        r"add (?:this |that )?to (?:my )?to-?do list[:\s]+(.+)",
        r"to-?do list mein (.+) add karo",
        r"naya kaam add karo[:\s]+(.+)",
    ]),
    ("list_todos", [
        r"(?:show|read|list|get|what are) (?:my )?to-?dos?(?:\s+(all|everything))?",
        r"what'?s (?:still )?pending",
        r"any (?:pending )?to-?dos?",
        r"mere to-?do dikhao",
        r"kya (?:kaam )?pending hai",
    ]),
    ("complete_todo", [
        r"(?:mark|complete|finish|check off) (.+?) as (?:done|complete|finished)",
        r"(?:mark|complete|finish|check off) (?:my )?to-?do (.+?) (?:as )?done",
        r"(.+?) (?:wala kaam|to-?do) (?:complete|done) kar diya",
    ]),
    ("delete_todo", [
        r"(?:delete|remove|cancel) (?:the )?to-?do[:\s]+(.+)",
        r"(?:delete|remove) (.+?) from (?:my )?to-?do list",
        r"(.+?) (?:wala kaam|to-?do) (?:delete|hata) (?:kar )?do",
    ]),

    # ── Clipboard ──────────────────────────────────────────────────────
    ("clipboard_read", [
        r"what'?s (?:on |in )?(?:my )?clipboard",
        r"read (?:my |the )?clipboard",
        r"what did i copy",
        r"show (?:me )?(?:my )?clipboard",
    ]),
    ("clipboard_write", [
        r"copy (?:this|that)(?: for me)?:?\s+(.+)",
        r"copy to clipboard:?\s+(.+)",
        r"save to clipboard:?\s+(.+)",
    ]),

    # ── Vision / Screen ────────────────────────────────────────────────
    ("screenshot_describe", [
        r"^\W*(?:screen|my screen)\s+vision\W*$",
        r"^\W*(?:use |turn on |start )?(?:screen )?vision(?: mode)?\W*$",
        r"\bscreen\s?shot\s+(?:le|lo|lelo|le\s+lo|lena|le\s+do|kheecho|khicho|kheench\s+lo|khinch\s+lo)\b",
        r"स्क्रीनशॉट\s*(?:लो|ले\s*लो|लेना|ले\s*दो|खींचो|खींच\s*लो)",
        r"what'?s (?:on|showing on) (?:my |the )?screen",
        r"describe (?:my |the )?screen",
        # D78: was r"take a screenshot" -- the article was REQUIRED and the
        # compound spelling was not accepted, so three of the four ways this
        # is actually said went to chat:
        #     "take screenshot"    -> chat   (bench/golden/adversarial row
        #                                     32: "dropped article in
        #                                     speech-to-text")
        #     "take screen shot"   -> chat   (Whisper writes it as two words
        #                                     at least as often as one)
        #     "capture screen"     -> chat
        # Only "take a screenshot" worked, and dropping an unstressed "a"
        # is one of the most common things an STT engine does.
        r"(?:take|grab|capture) (?:a )?screen\s?shot",
        r"(?:take|grab|capture) (?:a |the |my )?screen\b(?!\s*(?:shot|settings|resolution))",
        # The bare noun, ANCHORED. Unanchored it would fire on any sentence
        # containing the word -- "where do screenshots go", "that
        # screenshot was blurry" -- so _FILLER (^ plus the wake/politeness
        # prefixes) and `$` bound it to the one-word command, the same
        # shape D73 and D76 needed.
        _FILLER + r"screen\s?shot$",
        r"what (?:is|am i looking at) (?:on screen|on my screen|here)",
        r"look at (?:my )?screen",
    ]),

    # ── Weather ────────────────────────────────────────────────────────
    ("weather", [
        r"(?:what(?:'?s| is)|how'?s|get|check|give)(?: me)? (?:the )?(?:weather|whether)(?: report)? (?:like )?(?:in|at|for|near|of) (.+)",
        # No place named: the empty group gives "" and get_weather("")
        # asks wttr.in for the caller's own location.
        r"^\W*(?:will it|is it going to) rain(?: today| tomorrow| now)?()\W*$",
        r"^\W*(?:the )?weather(?: report| today| now| outside)?()\W*$",
        r"^\W*(?:what(?:'?s| is)|how'?s) the weather(?: like)?(?: today| now| outside)?()\W*$",
        r"^\W*(?:(?:aaj|kal|abhi)\s+)?(?:ka\s+)?mausam\s+(?:kaisa|kya|kaise)\s*(?:hai|h|he|rahega|hoga)?()\W*$",
        r"^\W*(?:आज\s*)?(?:का\s*)?मौसम\s*(?:कैसा|क्या)\s*(?:है|रहेगा)?()\W*$",
        r"(?:weather|whether)(?: report)? (?:in|at|for|near|of) (.+)",
        r"(?:will it|is it going to) rain (?:in|at) (.+)",
        r"temperature (?:in|at|of) (.+)",
        r"forecast (?:for|in|at) (.+)",
    ]),

    # ── News ───────────────────────────────────────────────────────────
    ("news", [
        # Hinglish with a topic: "cricket ki latest news batao", "technology ke baare mein khabar"
        r"^(?:mujhe |mere liye )?(.+?) (?:ki|ke|ka) (?:taaza |latest |aaj ki |recent )?(?:news|khabar|khabren|khabrein|samachar)(?: batao| sunao| dikhao| do| de do)?$",
        r"^(?:mujhe |mere liye )?(.+?) (?:ke baare mein|ke bare me|par|pe) (?:taaza |latest )?(?:news|khabar|khabren|khabrein|samachar)(?: batao| sunao| dikhao)?$",
        r"(?:latest |recent |breaking )?news (?:about|on|regarding) (.+)",
        r"what'?s (?:happening|going on) (?:with|in|around) (.+)",
        r"headlines? (?:about|on|for) (.+)",
        r"(?:top |latest |breaking )?news",
        r"what'?s (?:in the news|happening today|the news today)",
        r"headlines? today",
    ]),

    # ── Context Follow-ups ─────────────────────────────────────────────
    # D28 FIX (audit batch D22-a): the bare ^ anchor meant any leading
    # filler broke the match -- "please what about tomorrow" missed. _FILLER
    # already carries its own ^[\s,]* anchor, so it replaces it directly.
    ("followup_query", [
        _FILLER + r"(?:and |aur )?what about (.+?)\??$",
        _FILLER + r"(?:and |aur )?how about (.+?)\??$",
        r"^aur (.+?) (?:ka|ki|mein|me) (?:kya|kaisa|kaisi)(?:\s+hai)?\??$",
    ]),

    # ── YouTube — MUST come before web_search and open_url ────────────
    ("play_youtube", [
        r"play (.+?) on youtube",
        r"(.+?)\s+(?:(?:song|gana|gaana|gaane|video)\s+)?(?:chalao|chala\s+do|bajao|baja\s+do|laga\s+do|lagao|play\s+(?:kro|karo|kar\s+do))\s+(?:on\s+|par\s+|per\s+|pe\s+|me\s+|mein\s+)?youtube(?:\s+(?:par|per|pe|me|mein))?\W*$",
        r"youtube (.+)",
        r"search youtube (?:for )?(.+)",
        r"play (?:the )?(?:song|video|music) (?:called |named )?(.+?) on youtube",
        r"open youtube and play (.+)",
        r"play (.+?) (?:song|video|music) on youtube",
        r"play (?:the )?(?:song|video|music) (?:called |named )?(?!.*\bspotify\b)(?!(?:again|please|now)\W*$)(.+)",
        r"play (?!(?:the|some|my|any|a) (?:song|video|music)$)(.+?) (?:song|video|music)$",
        # "play music" / "play the song" / "play" name no song: that is "resume what was paused"
        # (play_pause_media), not a YouTube search for the word "music".
        r"^play (?!.*\bspotify\b)(?!" + GENERIC_PLAY + r"(?: again| please| now)*\W*$)(.+?)$",
    ]),

    # ── Spotify ────────────────────────────────────────────────────────
    ("play_spotify", [
        r"play (.+?) on spotify",
        r"(.+?)\s+(?:(?:song|gana|gaana|gaane|music)\s+)?(?:chalao|chala\s+do|bajao|baja\s+do|laga\s+do|lagao|play\s+(?:kro|karo|kar\s+do))\s+(?:on\s+|par\s+|per\s+|pe\s+|me\s+|mein\s+)?spotify(?:\s+(?:par|per|pe|me|mein))?\W*$",
        # D24 FIX (audit batch D-local): this was a bare `^spotify (.+)$`
        # catch-all, so ANY sentence starting with "spotify " had the rest
        # captured as a track name -- including requests to close it:
        #     "spotify close karo"   -> play track "close karo"
        #     "spotify band kar do"  -> play track "band kar do"
        #     "spotify बंद करो"       -> play track "बंद करो"
        # play_spotify sits at index 30 and close_app at 139, so it always
        # won. Asking SARA to close Spotify started music instead, which is
        # a wrong ACTION, not just a wrong label.
        #
        # This is the same defect shape the `press_key` entry below already
        # records removing ("it was a bare catch-all with no end marker at
        # all"). Here the shorthand is worth keeping -- "spotify bohemian
        # rhapsody" is a real phrasing -- so instead of deleting the pattern
        # it now refuses to treat a window/app command verb as a track name.
        r"^spotify (?!(?:"
        r"band|bandh|close|closed|quit|exit|kill|stop|pause|off"
        r"|khol|kholo|kholdo|open|launch|start|run|chalu|shuru"
        r"|minimize|maximize|restart"
        r"|बंद|बन्द|क्लोज|खोल|खोलो|ऑफ|रोको|रुको|बदं"
        r")\b)(.+)$",
        r"play (.+?) (?:on |using )?spotify",
        r"open spotify and play (.+)",
    ]),
    
    # ── Web ────────────────────────────────────────────────────────────
    ("web_search", [
        # "find" only as the opening verb: unanchored, it turned "i want
        # to find my purpose in life" into a web search (E2E 2026-10-30).
        r"(?:search the web|search online) (?:for )?(.+)",
        _FILLER + r"find (?:out |me )?(.+?)(?:\s+for me)?$",
        # "search for" before "search", or the query keeps the "for".
        r"(?:search for|search|google|look up) (?!(?:for|the|a|an|me|it|up|something|anything)\W*$)(.+?)(?:\s+for me)?$",
    ]),
    ("summarize_url", [
        r"summarize (?:this |that |the )?(?:article|page|link|url)?:?\s*(\S+\.\S+)",
        r"(?:read|tell me about) (?:this |that |the )?(?:article|page|link):?\s*(\S+\.\S+)",
        r"what does (?:this |that |the )?(?:article|page|link) (?:say|about):?\s*(\S+\.\S+)",
        r"tl;?dr:?\s*(\S+\.\S+)",
    ]),
    ("open_url", [
        r"open (?:the )?(?:website |site )?([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z]{2,})+(?:/\S*)?)",
        r"(?:go to|visit|navigate to) ([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z]{2,})+(?:/\S*)?)",
        r"open (?:https?://\S+)",
    ]),

    # ── Calculator ─────────────────────────────────────────────────────
    ("calculator", [
        # Spoken operators, whole expression captured (see _CALC_* above).
        r"(?:what is|what'?s|calculate|compute|solve|evaluate)(?: the)? (" + _CALC_EXPR + r")(?: please)?\W*$",
        # No introduction, but at least one word operator (so phone numbers and
        # scores like "2-1" can never match).
        r"^\W*(" + _CALC_NUM + r"\s+" + _CALC_WORD_OP + r"\s+" + _CALC_NUM + r"(?:\s*" + _CALC_OP + r"\s*" + _CALC_NUM + r")*)\W*$",
        r"(?:what is|what'?s|calculate|compute)(?: the)? (square root of \d+(?:\.\d+)?|\d+(?:\.\d+)? (?:squared|cubed))\W*$",
        # E2E 2026-10-29: "what is 15 percent of 2400" captured only "15"
        # and answered "The answer is 15." Capture the whole percent phrase
        # first; _safe_calc() turns it into X/100*Y.
        r"(\d+(?:\.\d+)?\s*(?:%|percent|per cent|pratishat)\s+of\s+\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?\s+ka\s+\d+(?:\.\d+)?\s*(?:%|percent|per cent|pratishat))",
        r"(?:what is|calculate|compute|solve|evaluate)(?: the)? (\d[\d\s\+\-\*\/\(\)\.\^%]+)",
        # O1: bare "digit-digit" with a spaceless minus used to match
        # here on its own (no explicit keyword needed for this second
        # pattern), so "room 12-15", "9-5 job", "555-1234", and a score
        # like "2-1" were all misread as calculator input. Fix: every
        # occurrence of `-` as an operator in the chain now REQUIRES
        # whitespace on both sides; `+ * / ^ %` are unchanged (still
        # optionally spaced) since those symbols essentially never show
        # up in phone numbers/ranges/scores the way a bare hyphen does.
        #   MATCHES:     "2 + 2", "3*4", "10 / 2", "100^2", "50%20",
        #                "9 - 5", "1+2-3" (has a real '+' too)
        #   DOES NOT MATCH: "12-15", "9-5 job", "555-1234", "2-1"
        r"(\d+(?:\s*[+*/^%]\s*\d+|\s+-\s+\d+)+)",
        r"(?:what'?s) (\d[\d\s\+\-\*\/\(\)\.\^%]+)",
        r"open calculator",
        r"open calc",
    ]),

    # ── System ─────────────────────────────────────────────────────────
    ("system_info", [
        r"system (?:status|info|information|report)",
        r"\bhow much battery\b",
        r"\b(?:kitni battery|battery (?:kitni|kitna|percentage|percent))\b",
        r"how'?s my (?:pc|computer|laptop|system)",
        r"(?:battery|cpu|ram|disk|memory) (?:status|level|usage|info)",
        r"give me a status report",
        r"check my (?:pc|computer|laptop|system)",
    ]),
    ("set_volume", [
        # E2E held-out test 2026-10-29: these phrasings all fell to chat.
        # No number captured -> _h_set_volume() steps by the direction word.
        _FILLER + r"(?:turn|put)(?: the)? (?:volume|sound|audio) (?:up|down)(?: (?:a )?(?:bit|little|thoda))?$",
        r"^\W*(?:(?:please|sara|hey sara|can you|could you)[,\s]+)*(?:make (?:it|the sound|the volume) |speak )?(?:a (?:bit|little) |thoda )?(?:louder|quieter|softer)\b",
        r"\b(?:volume|awaaz|awaz|aawaz)\s+(?:(?:thoda|thodi|zara|aur|bahut)\s+)?(?:kam|badha|badhao|zyada|jyada|tez|dheere|dheemi|ghata|ghatao)\b",
        r"(?:आवाज़?|वॉल्यूम)\s*(?:(?:थोड़ा|थोड़ी|ज़रा|और)\s*)?(?:कम|बढ़ा|बढ़ाओ|ज़्यादा|तेज़|धीमी|धीमा|घटा)",
        r"(?:set|change|put) (?:the )?volume (?:to |at )?(\d{1,3})%?",
        r"volume (?:to |at )?(\d{1,3})%?",
        r"(?:increase|raise|turn up) (?:the )?volume(?: by (\d+)%?)?",
        r"(?:decrease|lower|turn down|reduce) (?:the )?volume(?: by (\d+)%?)?",
        # D73: object-first word order, which is how people actually say it
        # out loud. The four patterns above all require the VERB first
        # ("turn up the volume"), so bare "volume up" / "volume down" fell
        # through to `chat`. bench/golden/adversarial.jsonl records row 41
        # as "known miss: bare 'volume up' means set_volume, not chat", so
        # this was a recognised gap rather than a judgement call.
        #
        # Direction is not encoded in the intent (increase and decrease
        # both map to set_volume and are re-read downstream), so up/down
        # share one alternative, exactly as the verb forms do.
        #
        # ANCHORED, and that is the whole point. _detect_intent_cached uses
        # pattern.search(), so the obvious unanchored form
        #     r"(?:volume|sound) (?:up|down)..."
        # matches the substring "volume up" ANYWHERE. A probe caught it
        # taking over three phrases that correctly went to chat before:
        #     "don't turn the volume up"   -> set_volume   (a NEGATED command)
        #     "volume up mat karo"         -> set_volume   (negated, Hinglish)
        #     "what is the volume up to"   -> set_volume   (a question)
        # The negation guard does not save these: _ACTION_VERBS has no
        # "turn up", so is_negated_action_request() returns False and the
        # match stands. None of the three is in bench/golden, so the row
        # diff showed zero regressions -- the probe is what found them.
        #
        # _FILLER anchors at ^ and absorbs the wake/politeness prefixes
        # ("sara volume up", "please volume down"); the trailing $ is what
        # keeps the three phrases above out. This is the same shape the
        # D19 fix used on `unmute` for the same class of over-match.
        #
        # "sound up" is deliberately NOT included: the set_volume gate is
        # ("volume",), a plain substring test, so any `sound` alternative
        # here is unreachable. Widening the gate would change matching for
        # all five patterns and was not measured, so it was not done.
        _FILLER + r"volume (?:up|down)(?: by (\d+)%?)?$",
    ]),
    ("mute", [
        r"(?:mute|silence)(?: (?:the )?(?:volume|sound|audio|mic|microphone))?",
    ]),
    ("unmute", [
        # D19 FIX (audit batch C): the audio object used to be OPTIONAL for
        # every alternative -- `(?:unmute|restore|enable)(?: ...)?` -- so the
        # pattern reduced to a bare match on "unmute", "restore" OR "enable"
        # with no object at all. "enable" and "restore" are generic verbs, so
        # unmute silently swallowed other intents:
        #     "enable the bluetooth"         -> unmute (want bluetooth_on)
        #     "enable airplane mode"         -> unmute (want open_airplane_mode_settings)
        #     "restore my minimized windows" -> unmute (want restore_windows)
        # Bare "unmute" is unambiguous and stays. "restore"/"enable" now
        # REQUIRE the audio object, which is the only reading in which they
        # mean unmute at all. Still zero capturing groups, so this pattern
        # remains mergeable by engine._merge_groupless().
        r"\bunmute\b"
        r"|(?:restore|enable)\s+(?:the\s+)?(?:volume|sound|audio|mic|microphone)\b",
    ]),

    # ════════════════════════════════════════════════════════════════════
    # ── SYSTEM CONTROL — Power & Session ──────────────────────────────
    # ════════════════════════════════════════════════════════════════════
    ("lock_pc", [
        r"lock (?:the |my )?(?:pc|computer|laptop|screen|system)",
    ]),
    ("sleep_system", [
        r"(?:put|send) (?:the |my )?(?:pc|computer|laptop|system) to sleep",
        r"sleep (?:the |my )?(?:pc|computer|laptop|system)",
    ]),
    ("hibernate_system", [
        r"hibernate (?:the |my )?(?:pc|computer|laptop|system)",
        r"put (?:the |my )?(?:pc|computer|laptop|system) (?:in|into) hibernation",
    ]),
    ("log_off", [
        r"log\s?off(?: (?:the |my )?(?:pc|computer|laptop|system|user|account))?",
        r"sign out(?: (?:of|from) (?:the |my )?(?:pc|computer|laptop|system|account))?",
    ]),
    ("shutdown_system", [
        r"shut\s?down (?:the |my )?(?:pc|computer|laptop|system)",
        r"turn off (?:the |my )?(?:pc|computer|laptop|system)",
        r"power off (?:the |my )?(?:pc|computer|laptop|system)",
    ]),
    ("restart_system", [
        r"restart (?:the |my )?(?:pc|computer|laptop|system)",
        r"reboot (?:the |my )?(?:pc|computer|laptop|system)",
    ]),
    ("cancel_shutdown", [
        r"cancel (?:the )?shutdown",
        r"cancel (?:the )?restart",
        r"abort (?:the )?shutdown",
        r"stop (?:the )?shutdown",
    ]),

    # ── Volume (extremes) ─────────────────────────────────────────────
    ("max_volume", [
        r"(?:max(?:imum)?|full) volume",
        r"set (?:the )?volume to (?:max(?:imum)?|100%?|full)",
    ]),
    ("min_volume", [
        r"min(?:imum)? volume",
        r"set (?:the )?volume to (?:min(?:imum)?|0%?|zero)",
    ]),

    # ── Brightness ────────────────────────────────────────────────────
    ("set_brightness", [
        r"(?:set|change|put) (?:the )?(?:screen )?brightness (?:to |at )?(\d{1,3})%?",
        r"brightness (?:to |at )?(\d{1,3})%?",
    ]),
    ("increase_brightness", [
        r"(?:increase|raise|turn up) (?:the )?(?:screen )?brightness$",
        r"brighten (?:the )?screen",
        r"brightness up$",
    ]),
    ("decrease_brightness", [
        r"(?:decrease|lower|turn down|reduce|dim) (?:the )?(?:screen )?brightness$",
        r"dim (?:the )?screen",
        r"brightness down$",
    ]),
    ("max_brightness", [
        r"(?:max(?:imum)?|full) brightness",
    ]),
    ("min_brightness", [
        r"min(?:imum)? brightness",
        r"dim(?:mest)? brightness",
    ]),
    ("get_brightness_status", [
        r"what'?s (?:the )?(?:current )?(?:screen )?brightness",
        r"(?:check|show) (?:the )?(?:screen )?brightness",
    ]),

    # ── Window Management ─────────────────────────────────────────────
    ("show_desktop", [
        r"show (?:me )?(?:the )?desktop",
        r"minimize everything",
        r"go to (?:the )?desktop",
    ]),
    ("minimize_all_windows", [
        r"minimize all windows",
    ]),
    ("restore_windows", [
        r"restore (?:my |all )?(?:minimized )?windows",
        r"bring back (?:my |all )?windows",
    ]),
    ("maximize_active_window", [
        r"maximi[sz]e (?:this|the|current) (?:window|video|app|screen|page)",
        r"maximize (?:my )?window",
    ]),
    ("minimize_active_window", [
        # D21 FIX (audit batch C): the alternation allowed exactly ONE
        # determiner, so "minimize the current window" (determiner +
        # "current") missed and fell through to chat. Determiner and
        # qualifier are now independent optional groups. Still bounded by
        # the literal "window", and "minimize" is an unambiguous command
        # verb, so this cannot match ordinary chat.
        r"minimize\s+(?:(?:this|the|my)\s+)?(?:current\s+|active\s+)?window",
    ]),
    ("close_active_window", [
        # D76: was r"close (?:this|the|current) window" -- one required
        # determiner out of three, and no end boundary. Two defects:
        #
        # MISSED PHRASINGS, each falling through to close_app (position 139,
        # after this one at 55) with a nonsense app name:
        #     "close window"             -> close_app['window']
        #     "close active window"      -> close_app['active window']
        #     "close the active window"  -> close_app['active window']
        #     "close my window"          -> close_app['my window']
        # close_app then looks for a running process by that name. On
        # Windows that is not merely a no-op -- "window" is a substring of
        # real process names, so the wrong thing can be closed.
        # bench/golden/intents_en_noisy.jsonl row 55 is "close window".
        #
        # OVER-MATCH, which was already live: with no boundary after
        # "window", search() matched "close the window" INSIDE
        #     "close the windows explorer"  -> close_active_window
        # so asking to close Windows Explorer closed whatever was in front.
        # "close windows terminal" and "close windows security" escaped only
        # because they lack a determiner.
        #
        # The `$` is what fixes that half: "window" must end the utterance,
        # so "windows <anything>" cannot match and still reaches close_app.
        # Consistent with the ~39 other patterns in this file that anchor
        # with `$`. Trailing politeness ("close the window please") is NOT
        # covered -- it was not covered before either, and _FILLER only
        # absorbs leading prefixes.
        r"close (?:(?:this|the|my|current|active)\s+)*window$",
    ]),
    ("snap_window_left", [
        r"snap (?:this |the |current )?window (?:to the )?left",
        r"move (?:this |the |current )?window (?:to the )?left",
    ]),
    ("snap_window_right", [
        r"snap (?:this |the |current )?window (?:to the )?right",
        r"move (?:this |the |current )?window (?:to the )?right",
    ]),
    ("switch_window", [
        r"switch (?:to (?:the )?(?:next|other) )?window",
        r"alt tab",
    ]),
    # Same Layer 1/2/3 treatment as open_app/close_app above. The second
    # pattern here is Hinglish object-verb order ("chrome ko restart
    # karo"), so the app name is a PREFIX rather than following the verb
    # -- _APP_NAME still bounds it to 1-4 shaped tokens, and the trailing
    # `[\s.!]*$` anchor stops it from also swallowing trailing chatter
    # after "karo"/"kro" the way the old unanchored `.+?` effectively
    # could when re.search() was free to match anywhere.
    ("restart_application", [
        _FILLER + r"\brestart\b (?:the )?" + _OPT_DISFL + r"(" + _APP_NAME + r")(?:\s+app(?:lication)?)?$",
        _FILLER + r"(" + _APP_NAME + r") (?:ko )?restart (?:karo|kro)[\s.!]*$",
    ]),
    ("switch_to_application", [
        _FILLER + r"\bswitch\b to (?:the )?" + _OPT_DISFL + r"(" + _APP_NAME + r")(?:\s+app(?:lication)?)?$",
        _FILLER + r"(" + _APP_NAME + r") (?:pe|par) switch karo[\s.!]*$",
        _FILLER + r"(" + _APP_NAME + r") (?:pe|par) jao[\s.!]*$",
    ]),
    ("move_window", [
        r"move (.+?) (?:window )?to (?:the )?(left half|right half|top half|bottom half|top left|top right|bottom left|bottom right|center|full screen)$",
        r"(.+?) (?:ki window )?ko (left half|right half|top half|bottom half|top left|top right|bottom left|bottom right|center|full screen) (?:mein )?(?:le jao|move karo|kro)",
    ]),
    ("resize_window", [
        r"resize (.+?) (?:window )?to (?:the )?(left half|right half|top half|bottom half|top left|top right|bottom left|bottom right|center|full screen)$",
        r"(.+?) (?:ki window )?(?:ka size|resize)\s*(left half|right half|top half|bottom half|top left|top right|bottom left|bottom right|center|full screen) (?:kro|karo)",
    ]),
    ("always_on_top", [
        r"(?:make |set )?(.+?) always[- ]on[- ]top$",
        r"pin (.+?) (?:window )?on top$",
        r"toggle always on top for (.+)",
        r"(.+?) ko hamesha (?:upar|top pe) rakho",
        r"(.+?) (?:ko )?pin (?:kro|karo)",
    ]),
    ("toggle_fullscreen", [
        # No capture: the handler then toggles the ACTIVE window. The next
        # pattern used to read "exit fullscreen" as an app named "exit".
        r"^\W*(?:exit|leave|quit|close|turn off)\s+(?:the\s+)?full\s?screen(?:\s+mode)?\W*$",
        r"(?:make |set )?(.+?) fullscreen$",
        r"fullscreen (.+)",
        r"(.+?) ko fullscreen (?:kro|karo)",
        r"go fullscreen",
        r"toggle fullscreen",
        r"full ?screen (?:kro|karo|mode)?$",
    ]),

    # ── Media Controls ────────────────────────────────────────────────
    ("play_pause_media", [
        # Bare verbs and generic nouns: "pause", "resume", "play", "play music again", "pause the song".
        # dispatcher.py picks pause / resume from the words (tools.system.media_action_for).
        r"^\W*(?:please )?(?:pause|resume|unpause|continue|play)(?: (?:the|my|some|any|a))?(?: (?:music|musics|song|songs|media|playback|track|gaana|gana))?(?: (?:again|please|now))*\W*$",
        r"(?:play|pause)(?: the)? (?:music|media|song)",
        r"play(?:/| or )?pause",
        r"(?:resume|continue) (?:music|media|playback)",
    ]),
    ("next_track", [
        r"next (?:song|track)",
        r"skip (?:this |the )?(?:song|track)",
    ]),
    ("previous_track", [
        r"(?:previous|last|go back a) (?:song|track)",
    ]),
    ("stop_media", [
        r"stop (?:the )?(?:music|media|song|playback)",
    ]),

    # ── Keyboard / Typing ─────────────────────────────────────────────
    ("typing_text", [
        # D21 FIX (audit batch C): this read
        #     r"type (?:this )?(?:for me)?[:\s]+(.+)"
        # where the literal "type " already consumed the only space, so
        # [:\s]+ demanded a SECOND separator. "type this: X" therefore
        # missed entirely -- `(?:this )?` needs "this" + a space, but the
        # text has "this:", so it matched empty and then [:\s]+ failed on
        # "t". Only "type: X" or "type  X" (double space) ever worked.
        #
        # The colon is now mandatory, for exactly the reason the "write"
        # pattern below already requires one: "type" appears in ordinary
        # chat ("what type of file is this", "this type of problem"), and
        # a pattern matching bare "type <anything>" would capture the rest
        # of such a sentence as dictation text. So "type X" without a
        # colon is deliberately NOT matched -- same contract as "write".
        r"\btype\b(?: this)?(?: for me)?\s*:\s*(.+)",
        r"type out[:\s]+(.+)",
        # "write" (unlike "type") is an everyday word in ordinary chat
        # ("write my essay", "write a poem about..."), and the old
        # `[:\s]+` separator could in principle be satisfied by nothing
        # more than run-together whitespace, with no explicit marker
        # that this is a dictation command. Colon now mandatory here
        # (only for "write") so a real command reads "write: <text>" /
        # "write this for me: <text>" and a normal sentence containing
        # the word "write" can't accidentally satisfy it.
        r"\bwrite\b(?: this)?(?: for me)?:\s*(.+)",
    ]),
    ("press_key", [
        r"^\W*(?:(?:please|sara)[,\s]+)*(?:press|hit)\s+(enter|escape|esc|tab|space|spacebar|backspace|delete|home|end|page up|page down|up arrow|down arrow|left arrow|right arrow|f\d{1,2})\W*$",
        r"\bpress\b (?:the )?(.+?) key",
        r"\bhit\b (?:the )?(.+?) key",
        # Third pattern `r"press (.+)"` removed: it was a bare catch-all
        # with no end marker at all (not even "key"), so anything
        # starting with "press " -- "press on with the plan", "press
        # charges", etc. -- would be captured whole as a key name. The
        # first two patterns ("press X key" / "hit X key") are the only
        # phrasings that unambiguously name an actual key, and are
        # already gated on ("press", "hit", "key"); if a real command
        # needs "press <key>" without the trailing "key" word, it should
        # get its own bounded pattern (e.g. a fixed key-name alternation)
        # rather than a free-text catch-all.
    ]),
    ("copy_selection", [
        r"copy (?:this|that|selection|selected)(?: text)?$",
        r"copy (?:the )?selected (?:text|content)",
    ]),
    ("paste_clipboard", [
        # D21 FIX (audit batch C): the optional group accepted exactly ONE
        # of it/this/that/here, so the natural "paste it here" -- two of
        # them -- missed and fell through to chat with no later stage to
        # recover it (has_probable_tool_intent("paste it here") is False).
        # Split into two independent optional groups. Still anchored on $,
        # so "paste my essay into the doc" cannot match.
        r"paste(?:\s+(?:it|this|that))?(?:\s+here)?$",
        r"paste (?:from )?clipboard",
    ]),
    ("select_all", [
        r"select all(?: text)?",
        r"ctrl ?a",
        r"select everything",
    ]),
    ("undo", [
        r"undo(?: that)?",
        r"go back(?: one step)?",
        r"ctrl ?z",
    ]),
    ("redo", [
        r"redo(?: that)?",
        r"ctrl ?y",
    ]),

    # ── Browser Tab Controls ──────────────────────────────────────────
    ("new_tab", [
        r"(?:open|create) (?:a )?new tab",
        r"new tab",
    ]),
    ("close_tab", [
        r"close (?:this |current |the )?tab",
    ]),
    ("next_tab", [
        r"(?:go to |switch to )?next tab",
        r"tab right",
    ]),
    ("prev_tab", [
        r"(?:go to |switch to )?(?:previous|prev|last) tab",
        r"tab left",
    ]),
    ("reload_page", [
        r"(?:reload|refresh)(?: (?:this |the )?page)?",
        r"f5",
    ]),

    # ── Zoom ──────────────────────────────────────────────────────────
    ("zoom_in", [
        r"zoom in",
        r"(?:increase|make) (?:the )?(?:text|font|page) (?:larger|bigger)",
    ]),
    ("zoom_out", [
        r"zoom out",
        r"(?:decrease|make) (?:the )?(?:text|font|page) (?:smaller|tinier)",
    ]),
    ("zoom_reset", [
        r"reset zoom",
        r"(?:default|normal) zoom",
        r"zoom (?:to )?100%?",
    ]),

    # ── Scroll ────────────────────────────────────────────────────────
    ("scroll_up", [
        r"scroll up(?: (?:a bit|a little|more))?",
        r"go up (?:the )?page",
        r"page up",
    ]),
    ("scroll_down", [
        r"scroll down(?: (?:a bit|a little|more))?",
        r"go down (?:the )?page",
        r"page down",
    ]),
    # D26 FIX (audit batch D22-a): both of these were
    #     r"(?:go to |scroll to )?(?:the )?\btop\b(?: of (?:the )?page)?"
    # with the VERB optional and the OBJECT optional, so each reduced to a
    # bare \btop\b / \bbottom\b and fired on ordinary prose:
    #     "bottom line of the report is we lost money" -> scroll_bottom
    #     "top of the morning to you"                  -> scroll_top
    # The golden set carries both with the note "substring: 'bottom' must
    # not fire scroll_bottom", so this was a known-wrong behaviour.
    #
    # Same family as D19: when every part is optional the pattern matches a
    # bare keyword. Now at least one of the two must be present -- a scroll
    # verb, or the literal "of the page". "go two bottom of the page" (a
    # to/two homophone row) still matches on the second pattern.
    ("scroll_top", [
        r"(?:go|scroll|jump|move)\s+(?:to\s+)?(?:the\s+)?top\b(?:\s+of\s+(?:the\s+)?page)?",
        r"\btop\s+of\s+(?:the\s+)?page\b",
        r"beginning of (?:the )?page",
    ]),
    ("scroll_bottom", [
        r"(?:go|scroll|jump|move)\s+(?:to\s+)?(?:the\s+)?bottom\b(?:\s+of\s+(?:the\s+)?page)?",
        r"\bbottom\s+of\s+(?:the\s+)?page\b",
        r"end of (?:the )?page",
    ]),
    # ── Network Controls ──────────────────────────────────────────────
    ("wifi_on", [
        r"(?:turn on|enable|connect) (?:the )?wi-?fi",
        r"wi-?fi on",
    ]),
    ("wifi_off", [
        r"(?:turn off|disable|disconnect) (?:the )?wi-?fi",
        r"wi-?fi off",
    ]),
    ("bluetooth_on", [
        r"(?:turn on|enable) (?:the )?bluetooth",
        r"bluetooth on",
    ]),
    ("bluetooth_off", [
        r"(?:turn off|disable) (?:the )?bluetooth",
        r"bluetooth off",
    ]),

    # ── Display ───────────────────────────────────────────────────────
    ("dark_mode", [
        r"(?:enable|turn on|switch to|use) dark mode",
        r"dark (?:theme|mode)",
    ]),
    ("light_mode", [
        r"(?:enable|turn on|switch to|use) light mode",
        r"light (?:theme|mode)",
    ]),

    # ── File Operations ───────────────────────────────────────────────
    ("find_file", [
        r"\bfind (?:my |the )?(.+?)\s+(?:pdf|docx?|xlsx?|pptx?|txt|csv|zip)\b(?: file)?\W*$",
        r"find (?:the )?file (?:called |named )?(.+)",
        r"search (?:for )?(?:a )?file (?:called |named )?(.+)",
        r"where is (?:the )?file (?:called |named )?(.+)",
        r"locate (?:the )?file (?:called |named )?(.+)",
    ]),
    ("empty_recycle_bin", [
        r"empty (?:the )?recycle bin",
        r"clear (?:the )?recycle bin",
        r"delete (?:all )?(?:the )?(?:items in (?:the )?)?recycle bin",
    ]),
    
    ("notify_on_file", [
        r"tell me when (?:the |a |my )?download(?:s)? (?:is |are )?(?:finished|done|complete|ready)",
        r"let me know when (?:the |a |my )?download(?:s)? (?:is |are )?(?:finished|done|complete|ready)",
        r"notify me when (?:the |a |my )?download(?:s)? (?:is |are )?(?:finished|done|complete|ready)",
        r"tell me when (?:the |a |my )?file (?:is |has )?(?:finished|done|ready|downloaded)",
        r"let me know when (?:a |the |my )?(?:new )?file (?:shows up|appears|arrives|is ready)",
        r"notify me when (?:a |the |my )?(?:new )?file (?:shows up|appears|arrives|is ready)",
        r"jab download (?:complete|khatam|poora|done) ho jaye(?: to)? (?:mujhe )?batana",
        r"download (?:complete|khatam|poora) hone (?:par|pe) (?:mujhe )?batana",
        r"jab file (?:aa jaye|complete ho jaye)(?: to)? (?:mujhe )?batana",
        r"file (?:aane|complete hone) (?:par|pe) (?:mujhe )?batana",
    ]),

    ("open_downloads", [
        r"open (?:my )?downloads?(?: folder)?",
    ]),
    ("open_documents", [
        r"open (?:my )?documents?(?: folder)?",
    ]),
    ("open_desktop_folder", [
        r"open (?:my )?desktop folder",
    ]),
    ("open_pictures", [
        r"open (?:my )?pictures?(?: folder)?",
    ]),
    ("open_music", [
        r"open (?:my )?music(?: folder)?",
    ]),
    ("open_videos", [
        r"open (?:my )?videos?(?: folder)?",
    ]),
    # D28 FIX (audit batch D22-a): both patterns required the determiner,
    # so "uh open pc" missed on two counts -- the leading filler, and the
    # dropped "this" that speech-to-text routinely produces. Anchored on $
    # and limited to the two literal targets, so this cannot widen into
    # open_app's territory ("open pc" is this intent, "open notepad" is not).
    ("open_this_pc", [
        _FILLER + r"open (?:this |my )?(?:pc|computer)$",
    ]),
    ("open_recycle_bin", [
        r"open (?:the )?recycle bin",
        r"show (?:me )?(?:the )?recycle bin",
    ]),
    ("open_file_explorer", [
        r"open file explorer",
        r"open (?:windows )?explorer$",
    ]),
    ("open_control_panel", [
        r"open control panel",
    ]),
    ("open_task_manager", [
        r"open task manager",
    ]),

    ("open_display_settings", [
        r"open display settings",
        r"(?:change|adjust) (?:the )?display settings",
    ]),
    ("open_sound_settings", [
        r"open sound settings",
        r"(?:change|adjust) (?:the )?sound settings",
    ]),
    ("open_bluetooth_settings", [
        r"open bluetooth settings",
    ]),
    ("open_network_settings", [
        r"open network settings",
        r"open wifi settings",
    ]),
    ("open_update_settings", [
        r"open (?:windows )?update settings",
        r"check for (?:windows )?updates",
    ]),
    ("open_apps_settings", [
        r"open apps (?:and features )?settings",
        r"(?:uninstall|manage) (?:a |an )?(?:program|app)",
    ]),
    ("open_personalization_settings", [
        r"open personalization settings",
        r"change (?:my )?wallpaper",
        r"change (?:my )?background",
    ]),
    ("open_privacy_settings", [
        r"open privacy settings",
    ]),
    ("open_storage_settings", [
        r"open storage settings",
    ]),
    ("open_power_settings", [
        r"open power settings",
        r"open power (?:and|&) sleep settings",
    ]),
    ("open_about_settings", [
        r"open about settings",
        r"what windows version (?:am i running|do i have)",
    ]),
    
    ("open_nightlight_settings", [
        r"(?:turn on|enable|open|show) (?:the )?night ?light(?: settings)?",
        r"night ?light (?:on karo|chalu karo|kholo|on kro)",
        r"night ?light (?:setting|settings) (?:kholo|dikhao)",
    ]),
    ("open_airplane_mode_settings", [
        r"(?:turn on|enable|open|show) (?:the )?(?:airplane|flight) mode(?: settings)?",
        r"(?:airplane|flight) mode (?:on karo|chalu karo|kholo|on kro)",
        r"(?:airplane|flight) mode (?:setting|settings) (?:kholo|dikhao)",
    ]),

    ("disk_usage", [
        r"(?:disk|storage) (?:usage|space|status)",
        r"how much (?:disk space|storage) (?:do i have|is left|is free)",
    ]),
    ("uptime", [
        r"(?:system |pc |computer )?uptime",
        r"how long has my (?:pc|computer|laptop|system) been (?:on|running)",
    ]),
    ("local_ip", [
        r"(?:what'?s|show me|tell me) (?:my )?(?:local )?ip address",
        r"my ip address",
    ]),
    
    ("gpu_status", [
        r"gpu (?:usage|status|info|report)",
        r"how'?s (?:my )?gpu",
        r"graphics card (?:usage|status|info)",
        r"gpu (?:ka status|kaisa hai|batao)",
        r"graphics card (?:ka status|kaisa hai|batao)",
    ]),
    ("temperature_status", [
        r"(?:cpu |gpu |system |pc )?temperature(?: check| status)?$",
        r"how hot is (?:my )?(?:pc|computer|laptop|system|cpu|gpu)",
        r"temperature (?:kya hai|kitna hai|batao)",
        r"(?:pc|laptop|computer|system) kitna garam hai",
    ]),
    ("process_list", [
        r"(?:show|list) (?:running |top )?processes",
        r"top processes",
        r"what(?:'?s| is) running(?: on my (?:pc|computer|laptop|system))?",
        r"processes (?:dikhao|batao|ki list dikhao)",
        r"kya kya chal raha hai",
    ]),
    ("list_services", [
        r"(?:list|show) (?:running |windows )?services",
        r"what services are running",
        r"services (?:dikhao|batao|ki list dikhao)",
        r"kaunsi services chal rahi (?:hai|hain)",
    ]),
    ("start_service", [
        r"start (?:the )?(.+?) service",
        r"start service (.+)",
        r"(.+?) service (?:start karo|chalu karo|chalao)",
        r"service (.+?) (?:start karo|chalu karo)",
    ]),
    ("stop_service", [
        r"stop (?:the )?(.+?) service",
        r"stop service (.+)",
        r"(.+?) service (?:band karo|bnd karo|roko)",
        r"service (.+?) (?:band karo|roko)",
    ]),

    # D26 FIX (audit batch D22-a): the first pattern had no end anchor, so
    # any sentence containing "what is the time" matched regardless of what
    # followed:
    #     "what is the time complexity of quicksort" -> time_query
    # The golden set carries this with the note "'what is ... time' is not
    # time_query". SARA would answer the clock instead of the question.
    # "time" must now end the request (optionally followed by "now"), which
    # keeps "what's the time", "what's time" and "what is the current time".
    ("time_query", [
        r"what(?:'?s| is) (?:the )?(?:current )?time\b(?:\s+(?:now|right\s+now))?\s*\??$",
        r"what time is it",
        r"tell me the time",
    ]),
    ("date_query", [
        r"what(?:'?s| is) (?:today'?s )?date",
        r"what day is (?:it|today)",
        r"tell me (?:today'?s )?date",
        r"what(?:'?s| is) (?:the )?day today",
    ]),

    ("calendar_today", [
        r"(?:is|are) there (?:any )?(?:events?|meetings?|appointments?|plans?)(?: scheduled| planned)?(?: for)?(?: today| tonight)?\W*$",
        r"do i have (?:any )?(?:events?|appointments?|plans?)(?: today| tonight)?\W*$",
        r"what (?:events?|appointments?|plans?) do i have(?: today)?\W*$",
        r"aaj koi (?:event|meeting|appointment) hai",
        r"(?:what'?s|show|tell me) (?:on )?(?:my )?(?:calendar|schedule) (?:for )?today",
        r"(?:what'?s|show|tell me) (?:my )?(?:calendar|schedule)$",
        r"aaj (?:ka|ke) (?:schedule|calendar) (?:batao|dikhao)",
        r"aaj kya (?:kya )?hai",
        r"do i have (?:any )?meetings? today",
        r"what meetings? do i have today",
    ]),
    ("calendar_create", [
        r"(?:set|schedule|create) (?:a |an )?meeting (?:called|named) (?P<title>.+?) (?:for|at|on) (?P<when>.+)",
        r"(?:create|add|schedule) (?:a |an )?(?:calendar )?event (?:called|named) (?P<title>.+?) (?:for|at|on) (?P<when>.+)",
        r"(?:set|schedule|create) (?:a |an )?meeting (?:for|at|on )?(?P<when>.+?) called (?P<title>.+)",
        r"(?:create|add|schedule) (?:a |an )?(?:calendar )?event (?:for|at|on )?(?P<when>.+?) called (?P<title>.+)",
        r"(?:set|schedule|create) (?:a |an )?meeting (?:for|at|on) (?P<when>.+)",
        r"(?:create|add|schedule) (?:a |an )?(?:calendar )?event (?:for|at|on) (?P<when>.+)",
        r"(?P<when>.+?) (?:baje|ko) meeting (?:set|schedule) karo(?: (?:naam|called) (?P<title>.+))?",
        r"meeting (?:set|schedule) karo (?P<when>.+? baje)",
    ]),

    ("why_proactive", [
        r"why did you say (?:that|this)",
        r"why did you (?:tell|mention) (?:me )?(?:that|this)",
        r"why were you proactive",
        r"why (?:was|is) that\b",
        r"explain (?:that|your) (?:message|suggestion|notification)",
        r"what made you say (?:that|this)",
        r"kyu(?:n)? bola",
        r"kyu(?:n)? kaha",
        r"aisa kyu(?:n)? bola",
        r"vo kyu(?:n)? bola tha",
    ]),

    ("why_decision", [
        r"why did i change (.+)",
        r"why did i (?:set|update|adjust) (.+)",
        r"maine (.+?) kyu(?:n)? change kiya (?:tha)?$",
        r"maine (.+?) kyu(?:n)? badla (?:tha)?$",
    ]),

    ("memory_forget_all", [
        r"forget everything you know about me",
        r"forget all (?:my )?(?:memories|long[- ]term memories)",
        r"wipe all (?:my )?memories",
        r"(?:delete|clear|erase) all (?:my )?(?:memories|long[- ]term memories)",
        r"saari yaad(?:ein|en)? (?:delete|mita|khatam) (?:kar do|karo)",
        r"mujhe (?:puri tarah|bilkul) bhool jao",
        r"sab kuch bhool jao jo tumhe mere baare mein pata hai",
    ]),
    ("memory_forget_specific", [
        r"forget that i (?:like|said|told you|mentioned) (.+)",
        r"forget (?:the fact )?that (.+)",
        r"please forget (.+)",
        r"ye bhool jao ki (.+)",
        r"(.+?) (?:wali baat|wala fact) bhool jao",
        r"ye yaad mat rakho ki (.+)",
    ]),
    ("memory_recall", [
        r"what (?:all |else )?(?:do|can) you (?:know|remember) about me",
        r"what (?:do )?you know about me",
        r"(?:tum|aap)?\s*mere (?:baare|bare)\s*(?:me|mein|main)\s*(?:kya|kuch)(?:\s+kya)?\s*(?:jaante|jante|jaanti|janti|jnte|janate|jaanate|pata|yaad)(?:\s*(?:ho|hai|hain|hain tumhe))?",
        r"batao kya kya (?:jaante|janti|jante|jaanti) ho",
        r"what do you remember about me",
        r"what (?:do you|can you) recall about me",
        r"tell me what you (?:know|remember) about me",
        r"mere baare mein kya yaad hai",
        r"tumhe mere baare mein kya pata hai",
        r"mere baare mein kya (?:pata|yaad) hai tumhe",
    ]),

    ("run_routine", [
        r"run (?:my |the )?(.+?) routine\b",
        r"start (?:my |the )?(.+?) routine\b",
        r"kick off (?:my |the )?(.+?) routine\b",
        r"trigger (?:my |the )?(.+?) routine\b",
        r"(.+?) routine chalao",
        r"routine (.+?) chalao",
        r"chalao (?:mera )?(.+?) routine",
    ]),

    # LAYER 1 (start-anchor + filler), LAYER 2 (\b on the trigger verb),
    # and LAYER 3 (shape-bound _APP_NAME capture) all applied below.
    # Previously these were bare `.search()` patterns with a `.+`-style
    # capture and no anchor at all, so "kuch nahi bss apna routine start
    # kerne ki soch raaha hu" matched open_app with group(1) =
    # "kerne ki soch raaha hu", and "...restart kerna chahiye..." matched
    # via the literal substring "start" embedded inside "reSTART" (no
    # word boundary). Layer 4's rejection (engine.py) is the last line
    # of defense for the remaining Hinglish-verb-phrase case that shape-
    # bounding alone can't rule out (e.g. "gym start karna hai" -- "gym"
    # and "karna" and "hai" are each individually app-name-shaped).
    ("open_app", [
        _FILLER + r"\bopen\b " + _OPT_DISFL + r"(?!https?://)(" + _APP_NAME + r")(?:\s+app)?$",
        _FILLER + r"\blaunch\b " + _OPT_DISFL + r"(?!https?://)(" + _APP_NAME + r")(?:\s+app)?$",
        _FILLER + r"\bstart\b " + _OPT_DISFL + r"(?!https?://)(" + _APP_NAME + r")(?:\s+app)?$",
        _FILLER + r"\brun\b " + _OPT_DISFL + r"(?!https?://)(" + _APP_NAME + r")(?:\s+app)?$",
    ]),
    # "end" deliberately dropped from the verb alternation below (it was
    # present in the original: close|quit|exit|kill|end). Even fully
    # anchored + word-boundaried, "end" collides with ordinary sentences
    # that are *actually* about ending something other than an app --
    # "end the call", "end the meeting", "end this conversation" -- and
    # those really do start with "end <noun phrase>", so anchoring can't
    # disambiguate them the way it can for "close"/"quit"/"exit"/"kill",
    # which are far more specifically about terminating a program in
    # everyday usage. If a genuine "end <app>" voice command is needed
    # later, it should go through the Layer-4 rejection list too rather
    # than being re-added bare.
    ("close_app", [
        _FILLER + r"\b(?:close|quit|exit|kill)\b(?: the)? " + _OPT_DISFL + r"(" + _APP_NAME + r")(?:\s+app)?$",
        _FILLER + r"\b(?:force quit|force close)\b(?: the)? (" + _APP_NAME + r")(?:\s+app)?$",
    ]),
]

# ── Layer 4: Hinglish verb-phrase / function-word rejection set ───────
# Consumed by engine.py's post-match rejection step, applied ONLY to
# open_app, close_app, restart_application, and switch_to_application:
# if the captured "app name" contains any of these tokens, the match is
# discarded and matching continues with the next pattern/intent instead
# of returning immediately (see _detect_intent_cached in engine.py).
# These are Hinglish verb endings and function words that show up when
# someone is talking ABOUT starting/restarting/switching/closing
# something as a future intention or passing thought -- e.g. "apna
# routine start kerne ki soch raaha hu" -- and never as part of an
# actual app name.
_NON_APP_TOKENS = frozenset({
    # "exit full screen" is the fullscreen toggle, not an app called "full screen".
    "full", "fullscreen",
    "kerne", "karne", "karna", "karni", "karunga", "karungi", "karta",
    "karti", "karke", "kar",
    "raha", "rahi", "rahe", "raaha", "rha",
    "soch", "sochta", "sochti", "chahta", "chahti", "chahiye",
    "hu", "hun", "hoon", "tha", "thi", "the", "hai", "ho",
    "ki", "ka", "ke", "ko", "se", "me", "mein", "nahi",
    "kuch", "bss", "bas", "apna", "apni", "mera", "meri",
    "wala", "wali", "liye", "jab", "time", "mile",
    # ── D23 (audit batch D-local): Devanagari equivalents ───────────────
    # Layer 4 (_captured_app_name_is_rejected in engine.py) is the last
    # defence for the four app-name intents, and it worked only on
    # ROMANISED function words. The Devanagari open_app/close_app patterns
    # added in patterns_multilingual.py capture a free app name in
    # Devanagari too, so the same guard needs the same vocabulary in that
    # script -- otherwise "कुछ काम करना है" shapes would be accepted as
    # app names by a guard that is only watching for "kuch kaam karna hai".
    "करने", "करना", "करनी", "करूंगा", "करूँगा", "करूंगी", "करूँगी",
    "करता", "करती", "करके", "कर",
    "रहा", "रही", "रहे",
    "सोच", "सोचता", "सोचती", "चाहता", "चाहती", "चाहिए",
    "हूं", "हूँ", "था", "थी", "थे", "है", "हो", "हैं",
    "की", "का", "के", "को", "से", "में", "नहीं",
    "कुछ", "बस", "अपना", "अपनी", "मेरा", "मेरी",
    "वाला", "वाली", "लिए", "जब", "समय", "मिले", "काम",
})

# ── Cheap pre-filter gates ──────────────────────────────────────────────
_INTENT_GATES = {
    "switch_mode": ("mode",),
    "undo_setting_change": ("undo", "revert", "wapas", "badla"),
    "run_routine": ("routine",),
    "reminder_add": ("remind", "reminder", "alert"),
    "reminder_list": ("reminder",),
    "reminder_cancel": ("reminder",),
    "set_timer": ("timer", "countdown"),
    "set_alarm": ("alarm", "wake me", "wake up"),
    "start_stopwatch": ("stopwatch",),
    "lap_stopwatch": ("lap", "stopwatch"),
    "stop_stopwatch": ("stopwatch",),
    "take_note": ("note", "remember", "write", "jot"),
    "read_notes": ("note",),
    "clear_notes": ("note",),
    "add_todo": ("to-do", "todo", "task", "kaam"),
    "list_todos": ("to-do", "todo", "task", "pending", "kaam"),
    "complete_todo": ("done", "complete", "finish", "mark", "check off"),
    "delete_todo": ("delete", "remove", "cancel", "to-do", "todo", "hata"),
    "clipboard_read": ("clipboard", "copy"),
    "clipboard_write": ("copy", "clipboard"),
    "screenshot_describe": ("screen",),
    "weather": ("weather", "whether", "rain", "temperature", "forecast"),
    "news": ("news", "headline", "happening"),
    "followup_query": ("what about", "how about", "aur "),
    "play_youtube": ("play", "youtube"),
    "play_spotify": ("play", "spotify"),
    "web_search": ("search", "google", "look up", "find"),
    "summarize_url": ("summar", "tl", "article", "page", "link"),
    "open_url": ("open", "go to", "visit", "navigate"),
    "system_info": ("system", "pc", "computer", "laptop", "status",
                     "battery", "cpu", "ram", "disk", "memory"),
    "set_volume": ("volume", "louder", "quieter", "softer", "sound", "audio"),
    "mute": ("mute", "silence", "band"),   # "band" = Hinglish "awaaz band karo"
    "unmute": ("unmute", "restore", "enable"),
    "lock_pc": ("lock",),
    "sleep_system": ("sleep",),
    "hibernate_system": ("hibernat",),
    "log_off": ("log off", "logoff", "sign out"),
    "shutdown_system": ("shut", "turn off", "power off"),
    "restart_system": ("restart", "reboot"),
    "cancel_shutdown": ("cancel", "abort", "stop"),
    "max_volume": ("volume", "max", "full"),
    "min_volume": ("volume", "min", "zero"),
    "set_brightness": ("brightness",),
    "increase_brightness": ("brightness", "brighten"),
    "decrease_brightness": ("brightness", "dim"),
    "max_brightness": ("brightness",),
    "min_brightness": ("brightness", "dim"),
    "get_brightness_status": ("brightness",),
    "show_desktop": ("desktop", "minimize"),
    "minimize_all_windows": ("minimize",),
    "restore_windows": ("restore", "window"),
    "maximize_active_window": ("maximize",),
    "minimize_active_window": ("minimize",),
    "close_active_window": ("close", "window"),
    "snap_window_left": ("snap", "window", "left"),
    "snap_window_right": ("snap", "window", "right"),
    "switch_window": ("switch", "window", "alt tab"),
    "restart_application": ("restart",),
    "switch_to_application": ("switch", "pe jao", "par jao"),
    # LAYER 5: open_app/close_app previously had no gate at all ("always
    # run" -- see the removed comment that used to sit at the bottom of
    # this dict). "open"/"start"/"run" are all < _TYPO_MIN_WORD_LEN (5)
    # so only "launch" and "close" feed the typo-correction vocabulary;
    # both are genuine, unambiguous trigger words for this feature, so
    # that's fine. "force quit"/"force close" are multi-word and are
    # automatically excluded from the typo vocab by _rebuild_routes().
    # "end" is intentionally NOT included here -- it was dropped from
    # close_app's own pattern above, so gating on it would only widen
    # the pre-filter for a word that can never lead to an actual match.
    "open_app": ("open", "launch", "start", "run"),
    "close_app": ("close", "quit", "exit", "kill", "force quit", "force close"),
    "move_window": ("half", "center", "full screen", "top left", "top right", "bottom left", "bottom right"),
    "resize_window": ("half", "center", "full screen", "top left", "top right", "bottom left", "bottom right"),
    "always_on_top": ("always on top", "pin", "upar"),
    "toggle_fullscreen": ("fullscreen", "full screen"),
    "play_pause_media": ("play", "pause", "music", "media", "resume", "continue", "unpause"),
    "next_track": ("next", "skip"),
    "previous_track": ("previous", "last", "back"),
    "stop_media": ("stop",),
    "typing_text": ("type", "write"),
    "press_key": ("press", "hit", "key"),
    "copy_selection": ("copy",),
    "paste_clipboard": ("paste",),
    "select_all": ("select", "ctrl"),
    "undo": ("undo", "back", "ctrl"),
    "redo": ("redo", "ctrl"),
    "new_tab": ("tab",),
    "close_tab": ("tab",),
    "next_tab": ("tab",),
    "prev_tab": ("tab",),
    "reload_page": ("reload", "refresh", "f5"),
    "zoom_in": ("zoom", "larger", "bigger"),
    "zoom_out": ("zoom", "smaller", "tinier"),
    "zoom_reset": ("zoom", "default", "normal"),
    "scroll_up": ("scroll", "up", "page up"),
    "scroll_down": ("scroll", "down", "page down"),
    "scroll_top": ("top", "beginning"),
    "scroll_bottom": ("bottom", "end"),
    "wifi_on": ("wifi", "wi-fi", "wi fi"),
    "wifi_off": ("wifi", "wi-fi", "wi fi"),
    "bluetooth_on": ("bluetooth",),
    "bluetooth_off": ("bluetooth",),
    "dark_mode": ("dark",),
    "light_mode": ("light",),
    "find_file": ("file", "find", "locate", "where"),
    "empty_recycle_bin": ("recycle",),
    "notify_on_file": ("download", "file", "batana", "notify", "let me know", "tell me when"),
    "open_downloads": ("download",),
    "open_documents": ("document",),
    "open_desktop_folder": ("desktop",),
    "open_pictures": ("picture",),
    "open_music": ("music",),
    "open_videos": ("video",),
    # D28: the gate required the determiner too ("this pc" / "my computer"),
    # so the widened pattern above was still never reached for "open pc" or
    # "open computer" -- the regex matched but the pre-filter had already
    # skipped it. Same shape as the calculator gate problem (D27): a gate
    # narrower than its own patterns. The pattern is anchored on $ and
    # limited to two literal targets, so the broader gate costs nothing.
    "open_this_pc": ("this pc", "my computer", " pc", "computer"),
    "open_recycle_bin": ("recycle",),
    "open_file_explorer": ("explorer",),
    "open_control_panel": ("control panel",),
    "open_task_manager": ("task manager",),
    "open_display_settings": ("display",),
    "open_sound_settings": ("sound",),
    "open_bluetooth_settings": ("bluetooth",),
    "open_network_settings": ("network", "wifi", "wi-fi"),
    "open_update_settings": ("update",),
    "open_apps_settings": ("apps", "program", "uninstall", "manage"),
    "open_personalization_settings": ("personalization", "wallpaper", "background"),
    "open_privacy_settings": ("privacy",),
    "open_storage_settings": ("storage",),
    "open_power_settings": ("power",),
    "open_about_settings": ("about", "windows version"),
    "open_nightlight_settings": ("night light", "nightlight"),
    "open_airplane_mode_settings": ("airplane", "flight mode"),
    "disk_usage": ("disk", "storage"),
    "uptime": ("uptime", "how long"),
    "local_ip": ("ip",),
    "gpu_status": ("gpu", "graphics card"),
    "temperature_status": ("temperature", "hot", "garam"),
    "process_list": ("process", "running", "chal raha"),
    "list_services": ("service", "services"),
    "start_service": ("service",),
    "stop_service": ("service",),
    "time_query": ("time",),
    "date_query": ("date", "day"),
    "why_proactive": ("why", "kyu", "kyun", "made you say", "explain"),
    "why_decision": ("why", "kyu", "kyun", "change", "badla", "badli"),
    "memory_forget_all": ("forget", "wipe", "delete", "erase", "clear", "bhool", "yaad", "mita"),
    "memory_forget_specific": ("forget", "bhool", "yaad"),
    "memory_recall": ("remember", "recall", "yaad", "pata", "know about me", "baare", "bare", "kya kya"),
    "calendar_today": ("calendar", "schedule", "aaj", "meeting", "event", "appointment", "plans"),
    "calendar_create": ("meeting", "event", "baje", "calendar"),
    # calculator: no safe substring gate — always run (its own patterns
    # provide the necessary keywords/digit-shape; unaffected by this pass).
}


# ── Hinglish / Hindi extensions (additive; see patterns_multilingual.py) ──
try:
    from .patterns_multilingual import apply as _apply_multilingual

    _apply_multilingual(_INTENT_PATTERNS, _INTENT_GATES)
except Exception:  # optional layer: never let it break the English patterns
    pass