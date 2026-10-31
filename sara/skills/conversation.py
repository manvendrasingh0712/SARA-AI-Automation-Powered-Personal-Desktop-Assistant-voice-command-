"""
sara.skills.conversation
Deterministic conversational commands that used to fall through to the chat model (audit part 2, D1-D3, D8):

  * cancel_timer_alarm   "cancel timer", "alarm band karo", "alarm hata do", "kal ka alarm cancel"
  * repeat_last_reply    "repeat that", "dobara bolo", "phir se batao"      (no LLM call)
  * continue_last_reply  "aur batao", "aage batao", "go on", "tell me more" (one LLM call)
  * set_speech_rate      "tez bolo", "dheere bolo", "speak slower"
  * set_reply_language   "hindi me bolo", "english me bolo", "hinglish me baat karo", "switch to hindi"
  * stop_speaking        "stop", "stop it", "ruko", "chup ho ja"            (when idle; while speaking, barge-in handles it)
  * unsupported_device   "fan chalao", "tv chalao", "ac chalao" -> an honest "I can't do that", never an LLM guess
  * incomplete_command   "volume", "brightness", "timer" -> asks for the missing part

The skills sit at the FRONT of the intent table, so "alarm band karo" is read here before the generic close_app pattern.
"""
from __future__ import annotations

from ._framework import SkillContext, SkillResult, skill

_DONE = r"(?:\s+(?:karo|kro|kar|do|de|dena|kardo|kar\s+do))*"

# ── D1: cancel a timer / alarm ───────────────────────────────────────────
_TA = r"(?:timers?|alarms?|टाइमर|अलार्म)"
_CANCEL = r"(?:cancel|band|bandh|stop|delete|remove|clear|off|hata|hatao|hata\s+do|rok|roko|बंद|कैंसल|हटा)"


@skill(
    name="cancel_timer_alarm",
    patterns=[
        r"^\W*(?:please\s+)?(?:cancel|stop|delete|remove|clear|turn\s+off|switch\s+off)\s+(?:the\s+|my\s+|this\s+|that\s+|kal\s+ka\s+|aaj\s+ka\s+)?" + _TA + r"\W*$",
        r"^\W*(?:(?:\w+\s+){0,2}(?:ka|ki|ke)\s+)?" + _TA + r"\s+" + _CANCEL + _DONE + r"\W*$",
        r"^\W*(?:cancel|stop|delete)\s+(?:(?:\w+\s+){0,2}(?:ka|ki|ke)\s+)" + _TA + r"\W*$",
    ],
    gate=("timer", "alarm", "टाइमर", "अलार्म"),
    description="Cancels the running timer or alarm (alarms are timers)",
    category="timers",
    examples=("cancel timer", "alarm band karo", "alarm hata do"),
)
def handle_cancel(match, ctx: SkillContext):
    from sara.tools import system as system_tools

    msg = system_tools.cancel_timer()
    if msg.startswith("No active"):
        return ctx.t("There's no timer or alarm running.", "Koi timer ya alarm chal nahi raha.")
    return ctx.t("Timer cancelled.", "Timer band kar diya.")


# ── D3: repeat / continue ─────────────────────────────────────────────────
@skill(
    name="repeat_last_reply",
    patterns=[
        r"^\W*(?:please\s+)?(?:repeat|say)\s+(?:that|it|this)(?:\s+(?:again|once\s+more))?\W*$",
        r"^\W*(?:please\s+)?(?:say\s+(?:that\s+)?again|repeat\s+(?:please|again)|once\s+more|come\s+again)\W*$",
        r"^\W*what\s+did\s+you\s+(?:just\s+)?say\W*$",
        r"^\W*(?:dobara|phir\s+se|fir\s+se|ek\s+baar\s+(?:aur|phir)|wapas)\s+(?:bolo|batao|sunao|kaho|bol\s+do|suna\s+do)\W*$",
        r"^\W*(?:repeat\s+karo|kya\s+kaha|kya\s+bola)\W*$",
    ],
    gate=("repeat", "again", "once more", "come again", "dobara", "phir se", "fir se", "ek baar", "wapas", "kya kaha", "kya bola", "what did you"),
    description="Says the last reply again without asking the model",
    category="conversation",
    examples=("repeat that", "dobara bolo"),
)
def handle_repeat(match, ctx: SkillContext):
    brain = ctx.raw.get("brain")
    last = ""
    try:
        for _user, reply in reversed(brain.get_history_snapshot()):
            if (reply or "").strip():
                last = reply.strip()
                break
    except Exception:  # noqa: BLE001
        last = ""
    if not last:
        return ctx.t("I haven't said anything yet.", "Maine abhi tak kuch bola hi nahi.")
    return last


@skill(
    name="continue_last_reply",
    patterns=[
        r"^\W*(?:aur|thoda\s+aur)\s+(?:batao|sunao|bolo|bataiye)\W*$",
        r"^\W*aage\s+(?:batao|bolo|sunao|badho)\W*$",
        r"^\W*(?:go\s+on|keep\s+going|tell\s+me\s+more|carry\s+on|and\s+then|more\s+please)\W*$",
        r"^\W*continue\s+(?:talking|speaking|please|the\s+(?:answer|story)|with\s+that)\W*$",
    ],
    gate=("aur", "aage", "go on", "keep going", "tell me more", "carry on", "and then", "more please", "continue"),
    description="Continues the previous answer with new information",
    category="conversation",
    examples=("aage batao", "go on"),
)
def handle_continue(match, ctx: SkillContext):
    brain = ctx.raw.get("brain")
    try:
        has_history = bool(brain.get_history_snapshot())
    except Exception:  # noqa: BLE001
        has_history = False
    if not has_history:
        return ctx.t("There's nothing to continue yet. Ask me something first.", "Abhi continue karne ko kuch nahi hai. Pehle kuch poochho.")
    prompt = (
        f"{ctx.user_input} (Continue from your previous answer: add new information in three or four short sentences "
        "and do not repeat anything you already said.)"
    )
    text = brain.generate_response(prompt)
    return (text or "").strip() or ctx.t("I have nothing more to add.", "Isse zyada mere paas batane ko nahi hai.")


# ── D3: speech rate / reply language / stop ────────────────────────────────
@skill(
    name="set_speech_rate",
    patterns=[
        r"^\W*(?:thoda\s+)?(?:tez|jaldi|fast|faster|quick|quickly)\s+(?:bolo|bol|boliye|kaho)\W*$",
        r"^\W*(?:thoda\s+)?(?:dheere|dhire|slow|slowly|aaram\s+se)\s+(?:bolo|bol|boliye|kaho)\W*$",
        r"^\W*(?:please\s+)?speak\s+(?:a\s+bit\s+|a\s+little\s+)?(?:faster|quicker|more\s+quickly|slower|more\s+slowly|slowly|quickly)\W*$",
        r"^\W*(?:please\s+)?(?:talk|speak)\s+(?:at\s+)?(?:normal|regular)\s+(?:speed|pace)\W*$",
        r"^\W*(?:slowly|slower|faster|quickly)\W*$",
        r"^\W*(?:normal|aam)\s+(?:speed|raftaar)\s+(?:se\s+)?(?:bolo)?\W*$",
    ],
    gate=("tez", "jaldi", "fast", "quick", "dheere", "dhire", "slow", "aaram", "speak", "talk", "normal speed", "raftaar"),
    description="Makes SARA speak faster or slower",
    category="voice",
    examples=("tez bolo", "dheere bolo", "speak faster"),
)
def handle_rate(match, ctx: SkillContext):
    low = ctx.user_input.lower()
    api = ctx.raw.get("context_state", {}).get("control_api")
    if api is None or not hasattr(api, "set_speech_speed"):
        return ctx.t("I can't change my speaking speed from here.", "Main yahan se apni bolne ki speed nahi badal sakti.")
    try:
        current = int(float(ctx.pref("speech_speed", "50") or 50))
    except (TypeError, ValueError):
        current = 50
    if any(w in low for w in ("normal", "regular", "aam")):
        new = 50
    elif any(w in low for w in ("tez", "jaldi", "fast", "quick")):
        new = min(100, current + 20)
    else:
        new = max(0, current - 20)
    api.set_speech_speed(new)
    if new == current:
        return ctx.t("That's already as far as I can go.", "Isse zyada nahi ja sakti.")
    if new == 50 and any(w in low for w in ("normal", "regular", "aam")):
        return ctx.t("Back to my normal speed.", "Normal speed par aa gayi.")
    return ctx.t("Speaking faster now." if new > current else "Speaking slower now.",
                 "Ab tez bolungi." if new > current else "Ab dheere bolungi.")


@skill(
    name="set_reply_language",
    patterns=[
        r"^\W*(?:please\s+)?(hindi|english|hinglish)\s+(?:me|mein|main)\s+(?:bolo|baat\s+karo|baat\s+kijiye|jawab\s+do|bol|batao)\W*$",
        r"^\W*(?:please\s+)?(?:speak|talk|reply|answer|respond)\s+(?:to\s+me\s+)?in\s+(hindi|english|hinglish)\W*$",
        r"^\W*(?:switch|change)\s+(?:the\s+)?(?:language\s+)?to\s+(hindi|english|hinglish)\W*$",
        r"^\W*(?:switch|change)\s+(?:the\s+)?(?:reply\s+)?language\s+to\s+(hindi|english|hinglish)\W*$",
        r"^\W*(?:ab\s+)?(hindi|english|hinglish)\s+(?:me|mein)\s+(?:hi\s+)?(?:baat|bolna|bolo)\b.*$",
    ],
    gate=("hindi", "english", "hinglish"),
    description="Sets the reply language (Hindi, English or Hinglish = follow the user)",
    category="voice",
    examples=("hindi me bolo", "english me bolo", "hinglish me baat karo"),
)
def handle_language(match, ctx: SkillContext):
    which = (match.group(1) or "").lower()
    api = ctx.raw.get("context_state", {}).get("control_api")
    if api is None or not hasattr(api, "set_language"):
        return ctx.t("I can't change the language from here.", "Main yahan se language nahi badal sakti.")
    code = {"hindi": "hi", "english": "en", "hinglish": "auto"}[which]
    api.set_language(code)
    if which == "hinglish":
        return ctx.t("Okay, I'll talk the way you do.", "Theek hai, ab jaise aap bologe waise hi baat karungi.")
    if which == "hindi":
        return "ठीक है, अब मैं हिंदी में बात करूँगी।"
    return "Okay, I'll speak English."


@skill(
    name="stop_speaking",
    patterns=[r"^\W*(?:please\s+)?(?:stop|stop\s+it|stop\s+talking|stop\s+speaking|quiet|be\s+quiet|shut\s+up|silence|ruko|ruk\s+ja|ruk\s+jao|chup|chup\s+ho\s+ja|chup\s+ho\s+jao|chup\s+raho|bas)\W*$"],
    gate=("stop", "quiet", "shut up", "silence", "ruko", "ruk", "chup", "bas"),
    description="Stops SARA's speech immediately",
    category="voice",
    examples=("stop", "ruko", "chup ho ja"),
)
def handle_stop(match, ctx: SkillContext):
    tts = ctx.raw.get("tts")
    try:
        tts.stop()
    except Exception:  # noqa: BLE001
        pass
    return "Okay."


# ── D8: unsupported devices and bare verbs ─────────────────────────────────
_DEVICES = r"(fan|tv|television|torch|flashlight|ac|a\.c\.|air\s+conditioner|light|lights|bulb|geyser|heater|cooler|pc|computer)"
_ON_OFF = r"(?:chalao|chala\s+do|on\s+karo|on\s+kar\s+do|off\s+karo|off\s+kar\s+do|band\s+karo|band\s+kar\s+do|bandh\s+karo|start\s+karo|lagao|laga\s+do|jalao|jala\s+do|bujhao|bujha\s+do|on|off)"


@skill(
    name="unsupported_device",
    patterns=[
        r"^\W*(?:please\s+)?(?:the\s+|my\s+)?" + _DEVICES + r"\s+(?:ko\s+)?" + _ON_OFF + r"\W*$",
        r"^\W*(?:please\s+)?(?:turn|switch)\s+(?:on|off)\s+(?:the\s+|my\s+)?" + _DEVICES + r"\W*$",
    ],
    gate=("fan", "tv", "television", "torch", "flashlight", "ac", "a.c.", "air conditioner", "light", "bulb", "geyser", "heater", "cooler", "pc", "computer"),
    description="Says plainly that SARA only controls this PC, instead of letting the chat model pretend",
    category="honesty",
    examples=("fan chalao", "tv chalao"),
)
def handle_device(match, ctx: SkillContext):
    device = (match.group(1) or "").lower()
    if device in ("pc", "computer"):
        return ctx.t("The PC is already on. I can lock it, put it to sleep or shut it down.",
                     "PC to pehle se chalu hai. Main ise lock, sleep ya shutdown kar sakti hoon.")
    return ctx.t(f"I can't control the {device}. I can only control this PC.",
                 f"Main {device} control nahi kar sakti. Main sirf is PC ko control kar sakti hoon.")


_BARE = {
    "volume": ("What volume should I set? Say something like 'volume 50'.", "Volume kitna karun? Bolo, jaise 'volume 50 kar do'."),
    "awaaz": ("What volume should I set? Say something like 'volume 50'.", "Awaaz kitni karun? Bolo, jaise 'volume 50 kar do'."),
    "brightness": ("What brightness should I set? Say something like 'brightness 60'.", "Brightness kitni karun? Bolo, jaise 'brightness 60 kar do'."),
    "timer": ("For how long? Say something like 'timer 5 minute'.", "Kitni der ka timer? Bolo, jaise '5 minute ka timer'."),
    "alarm": ("For what time? Say something like 'alarm at 7 am'.", "Kitne baje ka alarm? Bolo, jaise 'subah 7 baje ka alarm'."),
    "reminder": ("What should I remind you about, and when?", "Kya yaad dilana hai, aur kab?"),
    "open": ("What should I open?", "Kya kholun?"),
    "close": ("What should I close?", "Kya band karun?"),
    "search": ("What should I search for?", "Kya search karun?"),
}


@skill(
    name="incomplete_command",
    patterns=[r"^\W*(?:please\s+)?(?:set\s+(?:a\s+|the\s+)?)?(volume|awaaz|brightness|timer|alarm|reminder|open|close|search)\W*$"],
    gate=("volume", "awaaz", "brightness", "timer", "alarm", "reminder", "open", "close", "search"),
    description="Asks for the missing part of a bare command instead of guessing",
    category="honesty",
    examples=("volume", "timer"),
)
def handle_incomplete(match, ctx: SkillContext):
    en, hi = _BARE[match.group(1).lower()]
    return ctx.t(en, hi)
