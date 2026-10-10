# -*- coding: utf-8 -*-
"""
sara.core.intent.patterns_multilingual
Hinglish (romanised Hindi) and Hindi (Devanagari) command patterns, ADDED to the
existing intents (nothing in patterns.py is edited).

* apply(intent_patterns, intent_gates) appends the extra patterns to each intent's
  own pattern list (keeping that intent's position and group layout) and extends
  its gate. It is called once from the bottom of patterns.py.
* Slot intents keep the SAME capture-group layout as their English patterns, so the
  existing handlers work unchanged. Slots that a handler cannot parse from Devanagari
  (app names, free-text titles) are covered for Latin-script / digit values only; the
  LLM tool router still handles the rest.
* reorder() fixes the few English priority bugs the benchmark found
  (for example "switch to dark mode" being read as switch_to_application).
"""
from __future__ import annotations

import re
import unicodedata

# ── Devanagari helpers ────────────────────────────────────────────────────
_NUKTA = "\u093c"
_PRECOMPOSED = {"\u0958": "\u0915", "\u0959": "\u0916", "\u095a": "\u0917", "\u095b": "\u091c",
                "\u095c": "\u0921", "\u095d": "\u0922", "\u095e": "\u092b", "\u095f": "\u092f"}


def _dv(pattern: str) -> str:
    """Make a Devanagari pattern accept precomposed AND decomposed nukta letters."""
    # a letter followed by "?" means "nukta optional": keep the base letter required
    pattern = re.sub("([\u0915-\u0939])" + _NUKTA + r"\?", "\\1[" + _NUKTA + "]?", pattern)
    for pre, base in _PRECOMPOSED.items():
        pattern = pattern.replace(pre + "?", base + "[" + _NUKTA + "]?")
    out = []
    for ch in pattern:
        out.append(_PRECOMPOSED[ch] + _NUKTA if ch in _PRECOMPOSED else ch)
    text = "".join(out)
    for pre, base in _PRECOMPOSED.items():
        text = text.replace(base + _NUKTA, f"(?:{pre}|{base}{_NUKTA})")
    return text


# ── Hinglish verb fragments ───────────────────────────────────────────────
DO = r"(?:kar(?:o|iye|ie|na|ke)?(?:\s+(?:do|de|dena|dijiye|diya|lo|le))?|kijiye|kro|kr\s+do)"
OPEN = r"(?:khol(?:o|na|iye|ie)?(?:\s+(?:do|de|dena|dijiye))?|open\s+" + DO + r")"
CLOSE = r"(?:band(?:h)?\s+" + DO + r"|close\s+" + DO + r"|off\s+" + DO + r"|bund\s+" + DO + r")"
ON = r"(?:chalu\s+" + DO + r"|on\s+" + DO + r"|enable\s+" + DO + r"|shuru\s+" + DO + r"|start\s+" + DO + r")"
INC = r"(?:badha(?:o|\s+do|\s+de|na)|zyada\s+" + DO + r"|increase\s+" + DO + r"|up\s+" + DO + r"|tez\s+" + DO + r")"
DEC = r"(?:kam\s+" + DO + r"|ghata(?:o|\s+do|\s+de|na)|decrease\s+" + DO + r"|down\s+" + DO + r"|dim\s+" + DO + r"|dheema\s+" + DO + r")"
SHOW = r"(?:dikha(?:o|\s+do|\s+de|na)|bata(?:o|\s+do|\s+de|na)|sunao|suna\s+do|dekho|show\s+" + DO + r")"
DEL = r"(?:hata\s+(?:do|de)|hatao|mita\s+(?:do|de)|mitao|delete\s+" + DO + r"|clear\s+" + DO + r"|remove\s+" + DO + r"|saaf\s+" + DO + r"|khali\s+" + DO + r"|cancel\s+" + DO + r")"
SET = r"(?:laga(?:o|\s+do|\s+de|na)|lagao|set\s+" + DO + r"|rakh\s+do)"
FULL = r"(?:full|maximum|max|poori|puri|sabse\s+(?:zyada|jyada|upar|high))"
LOW = r"(?:zero|minimum|min|bilkul\s+(?:zero|kam)|sabse\s+(?:kam|neeche|low)|mute)"
THIS_WIN = r"(?:(?:ye|yeh|is|iss|current|active)\s+(?:window|app|screen)(?:\s+ko)?)"
TIME = r"(?:(?:kal|aaj|parso|subah|shaam|raat|dopahar)\s+)*\d{1,2}(?::\d{2})?\s*(?:baje|am|pm)"

# ── Devanagari verb fragments ────────────────────────────────────────────
H_DO = r"(?:कर(?:ो|ना|िए|ें)?(?:\s*(?:दो|दे|दीजिए|लो|लें))?|कीजिए)"
H_OPEN = r"(?:खोल(?:ो|िए|ना)?(?:\s*(?:दो|दे|दीजिए))?|ओपन\s*" + H_DO + r")"
H_CLOSE = r"(?:बंद\s*" + H_DO + r"|बन्द\s*" + H_DO + r"|क्लोज़?\s*" + H_DO + r"|ऑफ़?\s*" + H_DO + r")"
H_ON = r"(?:चालू\s*" + H_DO + r"|ऑन\s*" + H_DO + r"|शुरू\s*" + H_DO + r"|चला\s*(?:ओ|दो|दे))"
H_INC = r"(?:बढ़ा(?:ओ|\s*दो|\s*दे)|ज़्यादा\s*" + H_DO + r"|इन्क्रीज़?\s*" + H_DO + r")"
H_DEC = r"(?:कम\s*" + H_DO + r"|घटा(?:ओ|\s*दो|\s*दे)|डिक्रीज़?\s*" + H_DO + r")"
H_SHOW = r"(?:दिखा(?:ओ|\s*दो|\s*दे)|बता(?:ओ|\s*दो|\s*दे)|सुना(?:ओ|\s*दो|\s*दे)|देखो)"
H_DEL = r"(?:हटा(?:ओ|\s*दो|\s*दे)|मिटा(?:ओ|\s*दो|\s*दे)|डिलीट\s*" + H_DO + r"|साफ़?\s*" + H_DO + r"|खाली\s*" + H_DO + r"|कैंसल\s*" + H_DO + r")"
H_SET = r"(?:लगा(?:ओ|\s*दो|\s*दे)|सेट\s*" + H_DO + r")"
H_FULL = r"(?:फुल|फ़ुल|अधिकतम|मैक्स|पूरा|पूरी)"
H_LOW = r"(?:शून्य|ज़ीरो|जीरो|न्यूनतम|मिनिमम|म्यूट)"

_SPECS: list = []   # (intent, [patterns], gate_tuple)


def _add(intent: str, patterns, gate=()) -> None:
    _SPECS.append((intent, list(patterns), tuple(gate)))


def _hi(*pats: str) -> list:
    return [_dv(p) for p in pats]


# ── modes / settings / reminders / alarms ────────────────────────────────
_add("switch_mode", [
    r"\b(study|work|gaming|home|normal|default)\s+mode\s+(?:me|mein|par|pe|on)\s+(?:switch|shift|change|chalo|jao)\b",
    r"\b(study|work|gaming|home|normal|default)\s+mode\s+(?:lagao|laga\s+do|" + ON + r")",
], ("mode",))
_add("undo_setting_change", [
    r"\bsetting(?:s)?\b.{0,25}\b(?:wapas|undo|revert|pehle\s+jaisa|pehle\s+wali)\b",
    r"\b(?:undo|revert|wapas)\b.{0,25}\bsetting",
], ("wapas", "revert"))
_add("reminder_add", [
    r"^[\s,]*(?:(?:mujhe|mere\s+liye)\s+)?(?=(?:[^\s]+\s+){0,3}?(?:baje|am|pm)\s+(.+?)\s+(?:ka|ki|ke\s+liye)\s+reminder)((?:[^\s]+\s+){0,3}?\d{1,2}(?::\d{2})?\s*(?:baje|am|pm))\s+.+?\s+(?:ka|ki|ke\s+liye)\s+reminder\s+" + SET,
    r"^[\s,]*(?:(?:mujhe|mere\s+liye)\s+)?(?=(.+?)\s+(?:ka|ki|ke\s+liye)\s+reminder\s+(?:" + TIME + r"))(.+?)\s+(?:ka|ki|ke\s+liye)\s+reminder\s+(?:" + TIME + r")",
    r"\b(.+?)\s+(?:ka|ki|ke\s+liye)\s+reminder\s+(?:laga\s+do|lagao|set\s+karo)\s+(" + TIME + r")",
    r"\b(.+?)\s+yaad\s+dila(?:na|o|\s+dena|\s+do)\s+(?:mujhe\s+)?(" + TIME + r")",
], ("reminder", "yaad dila"))
_add("reminder_list", [
    r"\b(?:mere|saare|saare|meri|sabhi|pending)?\s*reminders?\b.{0,20}\b(?:" + SHOW + r"|list|dekho|kya\s+kya|kaun\s+se)",
    r"\bkaun\s+(?:se|sa)\s+reminders?\b",
], ("reminder",))
_add("reminder_cancel", [
    r"\b(?:saare|sabhi|sab|mere|all)?\s*reminders?\s+(?:" + DEL + r")",
    r"\b(?:" + DEL + r")\s+(?:saare|sabhi|sab|mere)?\s*reminders?",
], ("reminder",))
_add("set_timer", [
    r"\b(\d+(?:\.\d+)?\s*(?:second|seconds|sec|minute|minutes|min|ghanta|ghante|hour|hours|hr)(?:\s+(?:aur\s+)?\d+\s*(?:second|seconds|sec|minute|minutes|min))?)\s+(?:ka|ki|ke\s+liye)\s+timer\b",
    r"\btimer\s+(?:" + SET + r")\s+(\d+(?:\.\d+)?\s*(?:second|seconds|sec|minute|minutes|min|ghanta|ghante|hour|hours|hr))",
], ("timer",))
_add("set_alarm", [
    r"\b((?:(?:kal|aaj|parso)\s+)?(?:(?:subah|shaam|raat|dopahar)\s+)?\d{1,2}(?::\d{2})?\s*(?:baje|am|pm))\s+(?:ka|ke\s+liye|ki)?\s*alarm\b",
    r"\balarm\s+(?:" + SET + r")\s+((?:(?:kal|aaj|parso)\s+)?(?:(?:subah|shaam|raat|dopahar)\s+)?\d{1,2}(?::\d{2})?\s*(?:baje|am|pm))",
], ("alarm",))
_add("start_stopwatch", [r"\bstopwatch\b.{0,15}\b(?:start|shuru|chalu|chalao)\b", r"\b(?:start|shuru|chalu)\s+" + DO + r"\s+stopwatch\b"], ("stopwatch",))
_add("lap_stopwatch", [r"\bstopwatch\b.{0,12}\blap\b|\blap\s+(?:lo|le|lagao|note)\b"], ("lap",))
_add("stop_stopwatch", [r"\bstopwatch\b.{0,15}\b(?:band|stop|ruk|roko|rok\s+do)\b"], ("stopwatch",))
_add("take_note", [
    r"\bnote\s+" + DO + r"\s+(?:ki\s+)?(.+)",
    r"\bnote\s+kar\s+lo\s+(?:ki\s+)?(.+)",
    r"\bnote\s+(?:me|mein)\s+(?:likho|likh\s+do|add\s+karo)\s+(.+)",
], ("note",))
_add("read_notes", [r"\b(?:meri|mere|saare|sabhi)?\s*notes?\s+(?:padh|read|sunao|suna\s+do|dikhao|dikha\s+do)", r"\bnotes?\b.{0,15}\b(?:padh\s+ke\s+sunao|padhkar\s+sunao|sunao)\b"], ("notes", "note"))
_add("clear_notes", [r"\b(?:saare|sabhi|sab|meri|mere)?\s*notes?\s+(?:" + DEL + r")", r"\b(?:" + DEL + r")\s+(?:saare|sabhi|sab|meri|mere)\s*notes?\b"], ("notes", "note"))
_add("add_todo", [
    r"\b(?:to-?do|todo|task)\s+(?:me|mein|list\s+mein)\s+(?:add|jodo|jod\s+do|daalo|daal\s+do)\s+(?:karo\s+)?(.+)",
    r"\b(?:to-?do|todo)\s+(?:me|mein)\s+add\s+karo\s+(.+)",
    r"\bnaya\s+(?:to-?do|todo|task)\s+(?:add\s+karo\s+)?[:\-]?\s*(.+)",
], ("todo", "to-do", "task"))
_add("list_todos", [
    r"\b(?:mere|meri|saare|sabhi)?\s*(?:pending|baaki|bache\s+hue)?\s*(?:kaam|todos?|to-?dos?|tasks?)\s+(?:" + SHOW + r"|list|dekho)()",
    r"\b(?:mere|saare)\s+(?:pending|baaki|bache)\s+(?:kaam|tasks?|todos?)\s*()(?:" + SHOW + r")",
], ("kaam", "todo", "to-do", "task"))
_add("complete_todo", [r"\b(.+?)\s+(?:wala\s+)?(?:kaam|task|todo)\s+(?:ho\s+gaya|complete\s+" + DO + r"|done\s+mark\s+" + DO + r"|khatam\s+" + DO + r")"], ("kaam", "task", "todo"))
_add("delete_todo", [r"\b(.+?)\s+(?:wala\s+)?(?:kaam|task|todo)\s+(?:todo\s+se\s+|list\s+se\s+)?(?:" + DEL + r")"], ("kaam", "task", "todo"))
_add("clipboard_read", [r"\bclipboard\s+(?:me|mein)\s+(?:kya\s+hai|kya\s+copy\s+hai|kya\s+likha)", r"\bclipboard\b.{0,20}\b(?:" + SHOW + r"|padh)"], ("clipboard",))
_add("clipboard_write", [r"\b(?:ye|yeh|isse)?\s*clipboard\s+(?:me|mein)\s+(?:copy|save)\s+" + DO + r"\s*[:\-]?\s*(.+)", r"\bcopy\s+" + DO + r"\s+clipboard\s+(?:me|mein)\s*[:\-]?\s*(.+)"], ("clipboard",))
_add("screenshot_describe", [r"\bscreen\s+(?:pe|par|mein|me)\s+(?:kya|jo)\s+(?:chal\s+raha|dikh\s+raha|hai|likha)", r"\bscreen\b.{0,30}\b(?:describe|batao|bata\s+do|samjhao)\b"], ("screen",))
_add("weather", [
    r"\b([A-Za-z][A-Za-z ]{1,25}?)\s+(?:ka|ki|ke)\s+(?:mausam|weather)\b",
    r"\b([A-Za-z][A-Za-z ]{1,25}?)\s+(?:mein|me|ka)\s+(?:mausam|weather)\s+(?:kaisa|kya|batao)",
], ("mausam", "weather"))
_add("news", [r"\b(?:aaj\s+ki\s+)?(?:taaza|taza|latest)?\s*(?:khabar(?:e|en)?|news)\s+(?:sunao|suna\s+do|batao|bata\s+do|dikhao)\s*()", r"\b(.+?)\s+(?:ki|ke\s+baare\s+mein)\s+(?:taaza\s+)?(?:khabar(?:e|en)?|news)\b"], ("khabar", "news"))
_add("summarize_url", [r"\b(?:is|iss|ye|yeh)\s+(?:article|page|link|website)\s+ka\s+(?:summary|saransh)\s+(?:batao|bata\s+do|de\s+do|do)\s+(\S+)"], ("summary", "saransh"))
_add("open_url", [r"\b((?:https?://)?(?:[a-z0-9\-]+\.)+[a-z]{2,}(?:/\S*)?)\s+(?:website\s+|site\s+)?(?:" + OPEN + r")", r"\b(?:website|site)\s+(\S+\.[a-z]{2,}\S*)\s+(?:" + OPEN + r")"], (".com", ".org", ".in", ".net", "website", "site"))
_add("play_youtube", [r"\byoutube\s+(?:pe|par|mein|me)\s+(.+?)\s+(?:chalao|chala\s+do|bajao|baja\s+do|play\s+" + DO + r"|lagao|laga\s+do)\s*$"], ("youtube",))
_add("play_spotify", [r"\bspotify\s+(?:pe|par|mein|me)\s+(.+?)\s+(?:chalao|chala\s+do|bajao|baja\s+do|play\s+" + DO + r"|lagao|laga\s+do)\s*$"], ("spotify",))
_add("web_search", [r"\bgoogle\s+(?:pe|par|mein|me)\s+(.+?)\s+(?:search\s+" + DO + r"|dhundo|dhoondo|khojo|dhund\s+do|search\s+karo)\s*$", r"\b(.+?)\s+(?:ke\s+baare\s+(?:me|mein)\s+)?(?:search\s+" + DO + r"|google\s+" + DO + r")\s*$"], ("google", "search"))
_add("calculator", [r"\b(\d+(?:\.\d+)?\s*(?:\+|-|\*|x|/|plus|minus|guna|bata|divide)\s*\d+(?:\.\d+)?)\s*(?:kitna|kya)\s+(?:hota\s+hai|hoga|hai)"], ("kitna", "kya"))
# ── system ────────────────────────────────────────────────────────────────
_add("system_info", [r"\b(?:pc|computer|system|laptop)\s+(?:ki|ka)\s+(?:status|halat|sthiti|performance|health)\b", r"\bsystem\s+(?:status|info|kaisa)\b"], ("status", "halat", "sthiti", "system"))
_add("set_volume", [r"\b(?:volume|awaaz|awaz)\s+(?:ko\s+)?(\d{1,3})\s*(?:%|percent|pratishat)?\s*(?:" + DO + r"|par|pe|tak)", r"\b(\d{1,3})\s*(?:%|percent)\s+(?:volume|awaaz)"], ("volume", "awaaz", "awaz"))
_add("mute", [r"\b(?:volume|awaaz|awaz|sound|sound\s+ko)\s+(?:ko\s+)?mute\s+" + DO, r"\bmute\s+" + DO + r"\b", r"\b(?:awaaz|awaz|volume|sound)\s+band\s+" + DO])
_add("unmute", [r"\b(?:volume|awaaz|awaz|sound)?\s*unmute\s+" + DO, r"\bunmute\b", r"\b(?:awaaz|awaz|sound|volume)\s+(?:wapas\s+)?(?:chalu\s+" + DO + r")"], ("unmute", "awaaz", "awaz", "sound"))
_add("lock_pc", [r"\b(?:pc|computer|laptop|system|screen)?\s*lock\s+" + DO, r"\b(?:pc|computer|laptop|system)\s+ko\s+lock\b"], ("lock",))
_add("sleep_system", [r"\b(?:pc|computer|laptop|system)\s+(?:ko\s+)?sleep\s+(?:mode\s+)?(?:me|mein)\s+(?:daal|dal|bhej|kar)", r"\bsleep\s+(?:mode\s+)?(?:me|mein)\s+(?:daal|dal|jao|chalo)", r"\bsleep\s+" + DO], ("sleep",))
_add("hibernate_system", [r"\b(?:pc|computer|laptop|system)?\s*hibernate\s+" + DO, r"\bhibernate\s+(?:mode\s+)?(?:me|mein)\s+(?:daal|dal|jao)"], ("hibernate",))
_add("log_off", [r"\b(?:sign\s*out|log\s*(?:off|out)|logout|logoff)\s+" + DO, r"\bmeri\s+id\s+(?:se\s+)?(?:log\s*out|sign\s*out)"], ("log", "sign out", "signout"))
_add("shutdown_system", [r"\b(?:pc|computer|laptop|system)\s+(?:ko\s+)?(?:shutdown|shut\s+down|band)\s+" + DO, r"\bshutdown\s+" + DO, r"\b(?:pc|computer|laptop)\s+(?:ko\s+)?off\s+" + DO], ("shutdown", "shut down", "band kar", "off kar"))
_add("restart_system", [r"\b(?:pc|computer|laptop|system)\s+(?:ko\s+)?restart\s+" + DO, r"^[\s,]*(?:(?:please|pls|sara|zara|ek baar|jara)\s+)*restart\s+" + DO + r"(?:\s+(?:pc|computer|laptop))?\s*$", r"\b(?:pc|computer|laptop)\s+(?:ko\s+)?reboot\s+" + DO], ("restart", "reboot"))
_add("cancel_shutdown", [r"\bshutdown\s+(?:ko\s+)?(?:cancel|ruk|roko|rok\s+do|band\s+mat)\b", r"\bcancel\s+" + DO + r"\s+shutdown"], ("shutdown",))
_add("max_volume", [r"\b(?:volume|awaaz|awaz)\s+(?:ko\s+)?" + FULL + r"\s+" + DO, r"\b" + FULL + r"\s+volume\b"], ("volume", "awaaz", "awaz"))
_add("min_volume", [r"\b(?:volume|awaaz|awaz)\s+(?:ko\s+)?" + LOW + r"\s+" + DO, r"\b(?:volume|awaaz)\s+(?:ko\s+)?sabse\s+(?:kam|neeche)"], ("volume", "awaaz", "awaz"))
_add("set_brightness", [r"\bbrightness\s+(?:ko\s+)?(\d{1,3})\s*(?:%|percent)?\s*(?:" + DO + r"|par|pe|tak)"], ("brightness",))
_add("increase_brightness", [r"\bbrightness\s+(?:ko\s+)?(?:thodi\s+|thoda\s+|aur\s+)?(?:" + INC + r")"], ("brightness",))
_add("decrease_brightness", [r"\bbrightness\s+(?:ko\s+)?(?:thodi\s+|thoda\s+)?(?:" + DEC + r")"], ("brightness",))
_add("max_brightness", [r"\bbrightness\s+(?:ko\s+)?" + FULL + r"\s+" + DO, r"\b" + FULL + r"\s+brightness\b"], ("brightness",))
_add("min_brightness", [r"\bbrightness\s+(?:ko\s+)?(?:" + LOW + r")\s*" + DO + r"?", r"\bbrightness\s+(?:ko\s+)?sabse\s+(?:kam|neeche)"], ("brightness",))
_add("get_brightness_status", [r"\b(?:abhi|current|kitni)\s+(?:ki\s+)?brightness\s+(?:kitni|kya)\b", r"\bbrightness\s+(?:kitni|kya)\s+(?:hai|hui|hogi)", r"\babhi\s+brightness\s+kitni"], ("brightness",))
_add("show_desktop", [r"\bdesktop\s+(?:" + SHOW + r"|dikhao)", r"\bdesktop\s+(?:pe|par)\s+(?:jao|chalo)"], ("desktop",))
_add("minimize_all_windows", [r"\b(?:saari|sabhi|saare|all)\s+windows?\s+(?:ko\s+)?minimi[sz]e\s+" + DO], ("minimi", "windows"))
_add("restore_windows", [r"\bminimi[sz]e\s+(?:ki\s+hui|hui|ki\s+gayi)\s+windows?\s+(?:wapas\s+)?(?:laao|lao|dikhao|restore)", r"\bwindows?\s+(?:ko\s+)?(?:wapas|restore)\b"], ("windows", "window"))
_add("maximize_active_window", [r"\b" + THIS_WIN + r"?\s*(?:window\s+)?maximi[sz]e\s+" + DO, r"\b(?:ye|yeh|is)\s+window\s+(?:ko\s+)?(?:bada|badi)\s+" + DO], ("maximi", "window"))
_add("minimize_active_window", [r"\b" + THIS_WIN + r"?\s*(?:window\s+)?minimi[sz]e\s+" + DO, r"\b(?:ye|yeh|is)\s+window\s+(?:ko\s+)?(?:chhota|chhoti|choti|chota)\s+" + DO], ("minimi", "window"))
_add("close_active_window", [r"\b(?:ye|yeh|is|iss|current)\s+window\s+(?:ko\s+)?(?:" + CLOSE + r")", r"\bwindow\s+(?:ko\s+)?(?:" + CLOSE + r")"], ("window",))
_add("snap_window_left", [r"\bwindow\s+(?:ko\s+)?(?:left|baayen|baen)\s*(?:side|taraf)?\s+(?:me|mein|par|pe)?\s*snap", r"\bsnap\s+" + DO + r".{0,12}\bleft\b"], ("snap",))
_add("snap_window_right", [r"\bwindow\s+(?:ko\s+)?(?:right|daayen|dayen)\s*(?:side|taraf)?\s+(?:me|mein|par|pe)?\s*snap", r"\bsnap\s+" + DO + r".{0,12}\bright\b"], ("snap",))
_add("switch_window", [r"\b(?:dusri|doosri|agli|next|pichli|previous)\s+window\s+(?:pe|par)\s+(?:switch|jao|chalo)", r"\bwindow\s+switch\s+" + DO], ("window",))
_add("always_on_top", [r"\b(.+?)\s+(?:ko\s+)?always\s+on\s+top\s+" + DO], ("always on top",))
_add("toggle_fullscreen", [r"\b(.+?)\s+(?:ko\s+)?full\s*screen\s+" + DO], ("fullscreen", "full screen"))
_add("move_window", [r"\b(.+?)\s+(?:ko|ki\s+window\s+ko)\s+((?:left|right|top|bottom)\s+half|top\s+left|top\s+right|bottom\s+left|bottom\s+right|center|full\s+screen)\s+(?:me|mein|par|pe)?\s*(?:move\s+" + DO + r"|le\s+jao|shift\s+" + DO + r")"], ("half",))
_add("play_pause_media", [r"\b(?:gaana|gana|song|music|video|media)\s+(?:ko\s+)?(?:pause|play)\s+" + DO, r"\b(?:gaana|song|music|video)\s+(?:ko\s+)?(?:roko|rok\s+do|ruk\s+jao)", r"\bpause\s+" + DO + r"\b"], ("pause", "play", "roko"))
_add("next_track", [r"\b(?:agla|agle|next)\s+(?:gaana|gana|song|track)\s+(?:chalao|chala\s+do|lagao|laga\s+do|bajao|baja\s+do|play)"], ("agla", "agle", "next"))
_add("previous_track", [r"\b(?:pichla|pichle|previous|purana)\s+(?:gaana|gana|song|track)\s+(?:chalao|chala\s+do|lagao|laga\s+do|bajao|baja\s+do|play)"], ("pichla", "pichle", "previous"))
_add("stop_media", [r"\b(?:music|gaana|gana|song|sangeet|video)\s+(?:ko\s+)?(?:band|stop)\s+" + DO], ("music", "gaana", "gana", "song", "video"))
_add("typing_text", [r"\btype\s+" + DO + r"\s*[:\-]?\s*(.+)"], ("type",))
_add("press_key", [r"\b(.+?)\s+key\s+(?:dabao|daba\s+do|press\s+" + DO + r"|dabana)\b", r"\b(?:dabao|press\s+" + DO + r")\s+(.+?)\s+key\b"], ("key", "dabao"))
_add("copy_selection", [r"\b(?:ye|yeh|selected|chuna\s+hua)\s+(?:selected\s+)?(?:text|content)?\s*copy\s+" + DO, r"\bselected\s+text\s+copy"], ("copy",))
_add("paste_clipboard", [r"\b(?:yahan|yaha|idhar|yahaan)\s+paste\s+" + DO, r"\bpaste\s+" + DO + r"\b"], ("paste",))
_add("select_all", [r"\b(?:sab\s+kuch|sabkuch|sab|saara|sara|everything)\s+select\s+" + DO, r"\bselect\s+all\b"], ("select",))
_add("undo", [r"\b(?:wo|woh|vo|ye|yeh)?\s*undo\s+" + DO], ("undo",))
_add("redo", [r"\b(?:wo|woh|vo|ye|yeh)?\s*redo\s+" + DO], ("redo",))
_add("new_tab", [r"\b(?:naya|nayi|new)\s+tab\s+(?:" + OPEN + r")", r"\bnew\s+tab\s+" + DO], ("tab",))
_add("close_tab", [r"\b(?:ye|yeh|is|iss|current)\s+tab\s+(?:ko\s+)?(?:" + CLOSE + r")", r"\btab\s+(?:ko\s+)?(?:" + CLOSE + r")"], ("tab",))
_add("next_tab", [r"\b(?:agle|agla|next)\s+tab\s+(?:pe|par|ko)?\s*(?:jao|chalo|switch)"], ("tab",))
_add("prev_tab", [r"\b(?:pichle|pichla|previous|purane)\s+tab\s+(?:pe|par|ko)?\s*(?:jao|chalo|switch)"], ("tab",))
_add("reload_page", [r"\bpage\s+(?:ko\s+)?(?:refresh|reload)\s+" + DO, r"\b(?:refresh|reload)\s+" + DO + r"\b"], ("refresh", "reload"))
_add("zoom_in", [r"\bzoom\s+in\s+" + DO, r"\bzoom\s+(?:badhao|badha\s+do)\b"], ("zoom",))
_add("zoom_out", [r"\bzoom\s+out\s+" + DO, r"\bzoom\s+(?:kam|ghatao|ghata\s+do)\b"], ("zoom",))
_add("zoom_reset", [r"\bzoom\s+(?:ko\s+)?reset\s+" + DO, r"\bzoom\s+(?:normal|default)\s+" + DO], ("zoom",))
_add("scroll_up", [r"\b(?:thoda\s+|thodi\s+)?(?:upar|oopar|up)\s+scroll\s+" + DO, r"\bscroll\s+up\b"], ("scroll",))
_add("scroll_down", [r"\b(?:thoda\s+|thodi\s+)?(?:neeche|niche|down)\s+scroll\s+" + DO, r"\bscroll\s+down\b"], ("scroll",))
_add("scroll_top", [r"\b(?:page\s+ke\s+)?(?:sabse\s+)?(?:upar|oopar|top)\s+(?:tak\s+)?(?:jao|chalo|scroll)", r"\btop\s+(?:pe|par)\s+(?:jao|chalo)"], ("top", "upar"))
_add("scroll_bottom", [r"\b(?:page\s+ke\s+)?(?:sabse\s+)?(?:neeche|niche|bottom)\s+(?:tak\s+)?(?:jao|chalo|scroll)", r"\bbottom\s+(?:pe|par)\s+(?:jao|chalo)"], ("bottom", "neeche", "niche"))
_add("wifi_on", [r"\bwi-?fi\s+(?:ko\s+)?(?:" + ON + r")", r"\bwi-?fi\s+chalu\b"], ("wifi", "wi-fi"))
_add("wifi_off", [r"\bwi-?fi\s+(?:ko\s+)?(?:" + CLOSE + r")"], ("wifi", "wi-fi"))
_add("bluetooth_on", [r"\bbluetooth\s+(?:ko\s+)?(?:" + ON + r")", r"\bbluetooth\s+chalu\b"], ("bluetooth",))
_add("bluetooth_off", [r"\bbluetooth\s+(?:ko\s+)?(?:" + CLOSE + r")"], ("bluetooth",))
_add("dark_mode", [r"\bdark\s+mode\s+(?:" + ON + r"|lagao|laga\s+do)", r"\bdark\s+theme\s+(?:" + ON + r")"], ("dark",))
_add("light_mode", [r"\blight\s+mode\s+(?:" + ON + r"|lagao|laga\s+do)", r"\blight\s+theme\s+(?:" + ON + r")"], ("light",))
_add("find_file", [r"\b(.+?)\s+naam\s+(?:ki|ka|wali|wala)\s+file\s+(?:dhundo|dhoondo|khojo|dhund\s+do|find\s+" + DO + r"|search\s+" + DO + r")"], ("file",))
_add("empty_recycle_bin", [r"\brecycle\s+bin\s+(?:ko\s+)?(?:khali|saaf|empty)\s+" + DO, r"\brecycle\s+bin\s+(?:" + DEL + r")"], ("recycle",))
_add("notify_on_file", [r"\b(?:download|file)\s+(?:ho\s+jaye|ho\s+jae|complete\s+ho|poori\s+ho)\s+(?:to|toh)\s+(?:mujhe\s+)?(?:bata|batana|alert|notify)"], ("download",))
for _name, _word in (("open_downloads", "downloads?"), ("open_documents", "documents?"), ("open_desktop_folder", "desktop"), ("open_pictures", "pictures?|photos?"), ("open_music", "music"), ("open_videos", "videos?")):
    _add(_name, [r"\b(?:" + _word + r")\s+folder\s+(?:" + OPEN + r"|" + SHOW + r")"], (_word.split("|")[0].rstrip("?"),))
_add("open_this_pc", [r"\bthis\s+pc\s+(?:" + OPEN + r"|" + SHOW + r")"], ("this pc",))
_add("open_recycle_bin", [r"\brecycle\s+bin\s+(?:" + OPEN + r"|" + SHOW + r")"], ("recycle",))
_add("open_file_explorer", [r"\bfile\s+explorer\s+(?:" + OPEN + r"|" + SHOW + r")"], ("explorer",))
_add("open_control_panel", [r"\bcontrol\s+panel\s+(?:" + OPEN + r"|" + SHOW + r")"], ("control panel",))
_add("open_task_manager", [r"\btask\s+manager\s+(?:" + OPEN + r"|" + SHOW + r")"], ("task manager",))
for _name, _word in (("open_display_settings", "display"), ("open_sound_settings", "sound"), ("open_bluetooth_settings", "bluetooth"), ("open_network_settings", "network"),
                     ("open_privacy_settings", "privacy"), ("open_storage_settings", "storage"), ("open_power_settings", "power"), ("open_apps_settings", "apps?"),
                     ("open_personalization_settings", "personali[sz]ation"), ("open_update_settings", "(?:windows\\s+)?update"), ("open_about_settings", "about")):
    _add(_name, [r"\b" + _word + r"\s+settings?\s+(?:" + OPEN + r"|" + SHOW + r")"], (_word.split("|")[0].replace("\\s+", " ").replace("(?:windows ", "").rstrip("?)"),) if False else ("settings",))
_add("open_update_settings", [r"\bwindows\s+update\s+(?:check\s+" + DO + r"|dekho|dikhao)"], ("update",))
_add("open_apps_settings", [r"\b(?:koi\s+)?app\s+uninstall\s+(?:karna|" + DO + r")"], ("uninstall",))
_add("open_personalization_settings", [r"\bwallpaper\s+(?:change|badal)\s+(?:karo|karna|kar\s+do|do)", r"\bwallpaper\s+badlo"], ("wallpaper",))
_add("open_about_settings", [r"\b(?:mere\s+)?windows\s+(?:ka\s+)?version\s+(?:kaunsa|kya)", r"\bwindows\s+ka\s+version"], ("version",))
_add("open_nightlight_settings", [r"\bnight\s*light\s+(?:" + ON + r"|" + OPEN + r")"], ("night",))
_add("open_airplane_mode_settings", [r"\bairplane\s+mode\s+(?:" + ON + r"|" + OPEN + r")", r"\bflight\s+mode\s+(?:" + ON + r")"], ("airplane", "flight mode"))
_add("disk_usage", [r"\bdisk\s+(?:me|mein|ki|ka)\s+(?:kitni\s+jagah|space)", r"\bstorage\s+(?:kitni|kitna)\s+(?:bacha|bachi|bacha\s+hai)", r"\bdisk\s+usage\s+(?:check\s+" + DO + r"|batao|dikhao)"], ("disk", "storage"))
_add("uptime", [r"\bpc\s+(?:kab\s+se|kitni\s+der\s+se)\s+(?:on|chalu|chal\s+raha)", r"\buptime\s+(?:batao|bata\s+do)"], ("uptime", "kab se", "kitni der"))
_add("local_ip", [r"\b(?:mera|mere)\s+(?:local\s+)?ip\s+(?:address\s+)?(?:" + SHOW + r")", r"\bip\s+address\s+(?:" + SHOW + r")"], ("ip",))
_add("gpu_status", [r"\bgpu\s+(?:ka\s+)?(?:usage|status|kitna)\s*(?:batao|bata\s+do)?"], ("gpu",))
_add("temperature_status", [r"\b(?:cpu|processor|pc|laptop)\s+(?:ka\s+)?(?:temperature|temp|tapman)\s*(?:batao|bata\s+do|kitna)?"], ("temperature", "temp"))
_add("process_list", [r"\b(?:running|chal\s+rahe|chalu)\s+processes?\s+(?:" + SHOW + r")", r"\bprocesses?\s+list\s+(?:" + DO + r"|dikhao)"], ("process",))
_add("list_services", [r"\bwindows\s+services?\s+(?:" + SHOW + r"|list)", r"\bservices?\s+list\s+(?:" + DO + r"|dikhao)"], ("service",))
_add("start_service", [r"\b(.+?)\s+service\s+(?:ko\s+)?(?:start|chalu|shuru)\s+" + DO], ("service",))
_add("stop_service", [r"\b(.+?)\s+service\s+(?:ko\s+)?(?:stop|band)\s+" + DO], ("service",))
_add("time_query", [r"\b(?:abhi|ab)\s+(?:time|samay)\s+(?:kya|kitna|kitne)\s+(?:hua|hai|baje)", r"\b(?:time|samay)\s+kya\s+(?:hua|hai)", r"\bkitne\s+baje\s+(?:hain|hai|hue)", r"\b(?:abhi\s+)?kya\s+time\s+(?:hua|hai)"], ("time", "samay", "baje"))
_add("date_query", [r"\b(?:aaj\s+ki\s+)?(?:date|tareekh|tarikh)\s+(?:kya\s+hai|kya\s+hui|batao)", r"\baaj\s+(?:kaun\s+sa\s+din|kya\s+tareekh|kaunsi\s+date)"], ("date", "tareekh", "tarikh", "aaj"))
_add("calendar_today", [r"\baaj\s+(?:ka|ki)\s+(?:schedule|calendar|meetings?|agenda)\s+(?:" + SHOW + r")", r"\baaj\s+(?:kaun\s+si|kya)\s+meetings?\s+(?:hain|hai)"], ("schedule", "calendar", "meeting", "agenda"))
_add("calendar_create", [r"\b(?=(?:[^\s]+\s+){0,3}?\d{1,2}(?::\d{2})?\s*(?:baje|am|pm)\s+(.+?)\s+naam\s+(?:ki|ka)\s+meeting)((?:[^\s]+\s+){0,3}?\d{1,2}(?::\d{2})?\s*(?:baje|am|pm))\s+.+?\s+naam\s+(?:ki|ka)\s+meeting\s+(?:schedule|set|add|create|laga)"], ("meeting",))
_add("why_proactive", [r"\bye\s+(?:tumne|aapne)\s+(?:kyu|kyun|kyon)\s+(?:bola|kaha|bataya)", r"\bkyu\s+(?:bola|kaha)\s+(?:tumne|ye)"], ("kyu", "kyun", "kyon"))
_add("why_decision", [r"\bmaine\s+(.+?)\s+(?:kyu|kyun|kyon)\s+(?:change|badla|badli)\s+(?:ki|kiya)\s+thi"], ("kyu", "kyun", "kyon"))
_add("memory_forget_all", [r"\b(?:mere\s+baare\s+(?:me|mein)\s+)?(?:sab\s+kuch|sabkuch|saari\s+baatein|sab)\s+(?:bhool|bhul)\s+(?:jao|ja|jaao)", r"\bsab\s+(?:kuch\s+)?(?:bhula|bhulao)\s+do"], ("bhool", "bhul"))
_add("memory_forget_specific", [r"\b(?:ye|yeh)\s+bhool\s+jao\s+ki\s+(.+)", r"\bbhool\s+jao\s+ki\s+(.+)"], ("bhool",))
_add("memory_recall", [r"\b(?:mere\s+baare\s+(?:me|mein)|mujhe\s+lekar)\s+(?:tumhe|aapko|tum|aap)\s+kya\s+(?:yaad|pata)", r"\bmere\s+baare\s+(?:me|mein)\s+kya\s+(?:jaante|pata)\s+ho"], ("yaad", "pata", "jaante"))
_add("run_routine", [r"\b(?:mera\s+)?(.+?)\s+routine\s+(?:shuru|start|chalu|chalao)\s*" + r"(?:" + DO + r")?"], ("routine",))
_add("open_app", [r"^[\s,]*(?:(?:please|pls|sara|zara|hey sara|ek baar|jara)\s+)*([A-Za-z0-9][A-Za-z0-9+\-.]*(?:\s+[A-Za-z0-9][A-Za-z0-9+\-.]*){0,2}?)\s+(?:app\s+)?(?:" + OPEN + r")(?:\s+(?:please|pls|na|zara|jara))?\s*$"], (" khol", "open kar", "khalo"))
_add("close_app", [r"^[\s,]*(?:(?:please|pls|sara|zara|hey sara|ek baar|jara)\s+)*([A-Za-z0-9][A-Za-z0-9+\-.]*(?:\s+[A-Za-z0-9][A-Za-z0-9+\-.]*){0,2}?)\s+(?:app\s+)?(?:" + CLOSE + r")(?:\s+(?:please|pls|na|zara|jara))?\s*$"], ("band", "close"))
_add("restart_application", [r"^[\s,]*([A-Za-z0-9][A-Za-z0-9+\-.]*(?:\s+[A-Za-z0-9][A-Za-z0-9+\-.]*){0,2}?)\s+(?:app\s+)?(?:ko\s+)?restart\s+" + DO + r"\s*$"], ("restart",))
_add("switch_to_application", [r"^[\s,]*([A-Za-z0-9][A-Za-z0-9+\-.]*(?:\s+[A-Za-z0-9][A-Za-z0-9+\-.]*){0,2}?)\s+(?:pe|par|per)\s+switch\s+" + DO + r"\s*$"], ("switch",))

# ── Hindi (Devanagari) ───────────────────────────────────────────────────
def _hadd(intent, pats, gate): _add(intent, _hi(*pats), gate)

_hadd("set_volume", [r"(?:आवाज़?|वॉल्यूम)\s*(?:को\s*)?(\d{1,3})\s*(?:%|प्रतिशत)?\s*(?:" + H_DO + r"|पर|तक)"], ("आवाज", "वॉल्यूम"))
_hadd("mute", [r"(?:आवाज़?|वॉल्यूम|साउंड)\s*(?:को\s*)?म्यूट\s*" + H_DO, r"म्यूट\s*" + H_DO], ("म्यूट",))
_hadd("unmute", [r"(?:आवाज़?|वॉल्यूम|साउंड)?\s*अनम्यूट\s*" + H_DO, r"अनम्यूट"], ("अनम्यूट",))
_hadd("max_volume", [r"(?:आवाज़?|वॉल्यूम)\s*(?:को\s*)?" + H_FULL + r"\s*" + H_DO, r"(?:आवाज़?|वॉल्यूम)\s*(?:को\s*)?(?:सबसे\s*ज़्यादा|सबसे\s*तेज़?)"], ("आवाज", "वॉल्यूम"))
_hadd("min_volume", [r"(?:आवाज़?|वॉल्यूम)\s*(?:को\s*)?" + H_LOW + r"\s*" + H_DO, r"(?:आवाज़?|वॉल्यूम)\s*(?:को\s*)?सबसे\s*(?:कम|धीमी)"], ("आवाज", "वॉल्यूम"))
_hadd("set_brightness", [r"ब्राइटनेस\s*(?:को\s*)?(\d{1,3})\s*(?:%|प्रतिशत)?\s*(?:" + H_DO + r"|पर|तक)"], ("ब्राइटनेस",))
_hadd("increase_brightness", [r"ब्राइटनेस\s*(?:को\s*)?(?:थोड़ी\s*|थोड़ा\s*)?(?:" + H_INC + r")"], ("ब्राइटनेस",))
_hadd("decrease_brightness", [r"ब्राइटनेस\s*(?:को\s*)?(?:थोड़ी\s*|थोड़ा\s*)?(?:" + H_DEC + r")"], ("ब्राइटनेस",))
_hadd("max_brightness", [r"ब्राइटनेस\s*(?:को\s*)?" + H_FULL + r"\s*" + H_DO], ("ब्राइटनेस",))
_hadd("min_brightness", [r"ब्राइटनेस\s*(?:को\s*)?(?:" + H_LOW + r"|सबसे\s*कम)"], ("ब्राइटनेस",))
_hadd("get_brightness_status", [r"ब्राइटनेस\s*(?:अभी\s*)?कितनी\s*(?:है|हैं)"], ("ब्राइटनेस",))
_hadd("set_timer", [r"((?:\d+|एक|दो|तीन|चार|पाँच|पांच|छह|छः|सात|आठ|नौ|दस|पंद्रह|बीस|तीस|पैंतालीस|साठ)\s*(?:सेकंड|मिनट|घंटे|घंटा))\s*(?:का|के\s*लिए)\s*टाइमर"], ("टाइमर",))
_hadd("set_alarm", [r"((?:सुबह|शाम|रात|दोपहर)?\s*(?:\d{1,2}|एक|दो|तीन|चार|पाँच|पांच|छह|छः|सात|आठ|नौ|दस|ग्यारह|बारह)\s*बजे)\s*(?:का|के\s*लिए)?\s*अलार्म"], ("अलार्म",))
_hadd("reminder_list", [r"(?:मेरे|सारे|सभी)?\s*रिमाइंडर\s*(?:" + H_SHOW + r")"], ("रिमाइंडर",))
_hadd("reminder_cancel", [r"(?:सारे|सभी|मेरे)?\s*रिमाइंडर\s*(?:" + H_DEL + r")"], ("रिमाइंडर",))
_hadd("reminder_add", [r"(?:मुझे\s*)?(?=(?:\S+\s+){0,3}?बजे\s*(.+?)\s*की\s*याद\s*दिला)((?:\S+\s+){0,3}?बजे)\s*.+?\s*की\s*याद\s*दिला"], ("याद दिला", "याद"))
_hadd("weather", [r"(?:आज\s*)?([\u0900-\u097F]{2,20})\s*का\s*मौसम"], ("मौसम",))
_hadd("news", [r"(?:आज\s*की\s*)?(?:ताज़ा|ताजा)?\s*(?:खबरें|ख़बरें|खबर|समाचार)\s*(?:" + H_SHOW + r")()"], ("खबर", "ख़बर", "समाचार"))
_hadd("take_note", [r"नोट\s*" + H_DO + r"\s*(?:कि\s*)?(.+)"], ("नोट",))
_hadd("read_notes", [r"(?:मेरे|मेरी|सारे)?\s*नोट्स?\s*(?:पढ़कर\s*सुना|पढ़\s*के\s*सुना|पढ़ो|सुना)"], ("नोट",))
_hadd("clear_notes", [r"(?:सारे|सभी|मेरे)?\s*नोट्स?\s*(?:" + H_DEL + r")"], ("नोट",))
_hadd("time_query", [r"(?:अभी\s*)?कितने\s*बजे\s*(?:हैं|है|हुए)", r"(?:अभी\s*)?(?:समय|टाइम)\s*क्या\s*(?:हुआ|है)"], ("बजे", "समय", "टाइम"))
_hadd("date_query", [r"(?:आज\s*की\s*)?(?:तारीख|तारीख़|दिनांक|डेट)\s*(?:क्या\s*है|क्या\s*हुई|बताओ)", r"आज\s*(?:कौन\s*सा\s*दिन|क्या\s*तारीख)"], ("तारीख", "दिनांक", "डेट", "आज"))
_hadd("wifi_on", [r"वाई\s*-?\s*फ़?ाई\s*(?:को\s*)?(?:" + H_ON + r")"], ("वाई", "वाइ"))
_hadd("wifi_off", [r"वाई\s*-?\s*फ़?ाई\s*(?:को\s*)?(?:" + H_CLOSE + r")"], ("वाई", "वाइ"))
_hadd("bluetooth_on", [r"ब्लूटूथ\s*(?:को\s*)?(?:" + H_ON + r")"], ("ब्लूटूथ",))
_hadd("bluetooth_off", [r"ब्लूटूथ\s*(?:को\s*)?(?:" + H_CLOSE + r")"], ("ब्लूटूथ",))
_hadd("screenshot_describe", [r"स्क्रीन\s*(?:पर|में)\s*(?:क्या|जो)\s*(?:चल\s*रहा|दिख\s*रहा|है)"], ("स्क्रीन",))
_hadd("play_pause_media", [r"(?:गाना|गाने|गीत|संगीत|म्यूज़िक|वीडियो)\s*(?:को\s*)?(?:पॉज़?|प्ले)\s*" + H_DO, r"(?:गाना|गीत|संगीत|वीडियो)\s*(?:को\s*)?(?:रोको|रोक\s*दो)"], ("गाना", "गाने", "गीत", "संगीत", "वीडियो"))
_hadd("next_track", [r"(?:अगला|अगले)\s*(?:गाना|गाने|गीत|ट्रैक)\s*(?:चलाओ|चला\s*दो|लगाओ|लगा\s*दो|बजाओ|बजा\s*दो)"], ("अगला", "अगले"))
_hadd("previous_track", [r"(?:पिछला|पिछले|पुराना)\s*(?:गाना|गाने|गीत|ट्रैक)\s*(?:चलाओ|चला\s*दो|लगाओ|लगा\s*दो|बजाओ|बजा\s*दो)"], ("पिछला", "पिछले"))
_hadd("stop_media", [r"(?:संगीत|गाना|गाने|म्यूज़िक|वीडियो)\s*(?:को\s*)?(?:बंद|बन्द)\s*" + H_DO], ("संगीत", "गाना", "गाने", "वीडियो"))
_hadd("lock_pc", [r"(?:कंप्यूटर|कम्प्यूटर|लैपटॉप|पीसी)\s*(?:को\s*)?लॉक\s*" + H_DO, r"लॉक\s*" + H_DO], ("लॉक",))
_hadd("shutdown_system", [r"(?:कंप्यूटर|कम्प्यूटर|लैपटॉप|पीसी)\s*(?:को\s*)?(?:बंद|बन्द|शटडाउन|शट\s*डाउन)\s*" + H_DO], ("बंद", "बन्द", "शटडाउन"))
_hadd("restart_system", [r"(?:कंप्यूटर|कम्प्यूटर|लैपटॉप|पीसी)\s*(?:को\s*)?(?:रीस्टार्ट|रिस्टार्ट|रीबूट)\s*" + H_DO], ("रीस्टार्ट", "रिस्टार्ट", "रीबूट"))
_hadd("sleep_system", [r"(?:कंप्यूटर|कम्प्यूटर|लैपटॉप|पीसी)\s*(?:को\s*)?स्लीप\s*(?:मोड\s*)?(?:में|मे)\s*(?:डालो|डाल\s*दो|भेजो)", r"स्लीप\s*" + H_DO], ("स्लीप",))
_hadd("play_youtube", [r"यूट्यूब\s*(?:पर|में|पे)\s*(.+?)\s*(?:चलाओ|चला\s*दो|बजाओ|बजा\s*दो|लगाओ|लगा\s*दो)\s*$"], ("यूट्यूब",))
_hadd("play_spotify", [r"स्पॉटिफ़?ाई\s*(?:पर|में|पे)\s*(.+?)\s*(?:चलाओ|चला\s*दो|बजाओ|बजा\s*दो|लगाओ|लगा\s*दो)\s*$"], ("स्पॉटिफ",))
_hadd("web_search", [r"गूगल\s*(?:पर|में|पे)\s*(.+?)\s*(?:खोजो|ढूंढो|ढूँढो|सर्च\s*" + H_DO + r")\s*$"], ("गूगल",))
_hadd("calculator", [r"((?:\d+|एक|दो|तीन|चार|पाँच|पांच|छह|सात|आठ|नौ|दस|ग्यारह|बारह|तेरह|चौदह|पंद्रह|बीस)\s*(?:गुणा|जमा|घटा|भाग|प्लस|माइनस)\s*(?:\d+|एक|दो|तीन|चार|पाँच|पांच|छह|सात|आठ|नौ|दस|ग्यारह|बारह))\s*कितना\s*(?:होता\s*है|होगा|है)"], ("कितना",))
_hadd("system_info", [r"(?:कंप्यूटर|कम्प्यूटर|लैपटॉप|पीसी|सिस्टम)\s*(?:की|का)\s*(?:स्थिति|हालत|स्टेटस|परफॉर्मेंस)"], ("स्थिति", "हालत", "स्टेटस"))
_hadd("add_todo", [r"(?:टू-?डू|टूडू|टास्क)\s*(?:में|मे|लिस्ट\s*में)\s*(?:जोड़ो|जोड़\s*दो|डालो|डाल\s*दो|ऐड\s*" + H_DO + r")\s*(.+)"], ("टू", "टास्क"))
_hadd("list_todos", [r"(?:मेरे|मेरी|सारे)?\s*(?:बाकी|बचे\s*हुए|पेंडिंग)?\s*(?:काम|टू-?डू|टास्क)\s*(?:" + H_SHOW + r")()"], ("काम", "टू", "टास्क"))
_hadd("start_stopwatch", [r"स्टॉपवॉच\s*(?:" + H_ON + r"|शुरू\s*करो)"], ("स्टॉपवॉच",))
_hadd("stop_stopwatch", [r"स्टॉपवॉच\s*(?:रोक|बंद|स्टॉप)"], ("स्टॉपवॉच",))
_hadd("calendar_today", [r"आज\s*(?:का|की)\s*(?:शेड्यूल|कैलेंडर|मीटिंग|एजेंडा)\s*(?:" + H_SHOW + r")"], ("शेड्यूल", "कैलेंडर", "मीटिंग"))
_hadd("dark_mode", [r"डार्क\s*मोड\s*(?:" + H_ON + r"|लगाओ|लगा\s*दो)"], ("डार्क",))
_hadd("light_mode", [r"लाइट\s*मोड\s*(?:" + H_ON + r"|लगाओ|लगा\s*दो)"], ("लाइट",))
_hadd("show_desktop", [r"डेस्कटॉप\s*(?:" + H_SHOW + r")"], ("डेस्कटॉप",))
_hadd("minimize_all_windows", [r"(?:सारी|सभी|सारे)\s*(?:विंडो|विंडोज़?)\s*(?:को\s*)?(?:छोटी|मिनिमाइज़?)\s*" + H_DO], ("विंडो",))
_hadd("maximize_active_window", [r"(?:यह|ये|इस)\s*(?:विंडो)\s*(?:को\s*)?(?:बड़ी|मैक्सिमाइज़?)\s*" + H_DO], ("विंडो",))
_hadd("close_active_window", [r"(?:यह|ये|इस)\s*(?:विंडो)\s*(?:को\s*)?(?:" + H_CLOSE + r")"], ("विंडो",))
_hadd("new_tab", [r"(?:नया|नई)\s*टैब\s*(?:" + H_OPEN + r")"], ("टैब",))
_hadd("close_tab", [r"(?:यह|ये|इस)\s*टैब\s*(?:को\s*)?(?:" + H_CLOSE + r")"], ("टैब",))
_hadd("reload_page", [r"पेज\s*(?:को\s*)?(?:रीफ्रेश|रिफ्रेश|रीलोड)\s*" + H_DO, r"(?:रीफ्रेश|रिफ्रेश)\s*" + H_DO], ("रीफ्रेश", "रिफ्रेश", "रीलोड"))
_hadd("zoom_in", [r"ज़ूम\s*इन\s*" + H_DO], ("ज़ूम", "जूम"))
_hadd("zoom_out", [r"ज़ूम\s*आउट\s*" + H_DO], ("ज़ूम", "जूम"))
_hadd("scroll_up", [r"(?:ऊपर)\s*स्क्रॉल\s*" + H_DO], ("स्क्रॉल",))
_hadd("scroll_down", [r"(?:नीचे)\s*स्क्रॉल\s*" + H_DO], ("स्क्रॉल",))
_hadd("open_downloads", [r"डाउनलोड(?:स|्स)?\s*फ़?ोल्डर\s*(?:" + H_OPEN + r")"], ("डाउनलोड",))
_hadd("open_task_manager", [r"टास्क\s*मैनेजर\s*(?:" + H_OPEN + r")"], ("टास्क",))
_hadd("open_file_explorer", [r"फ़?ाइल\s*एक्सप्लोरर\s*(?:" + H_OPEN + r")"], ("एक्सप्लोरर",))
_hadd("disk_usage", [r"डिस्क\s*(?:में|मे)\s*कितनी\s*जगह\s*(?:बची|बचा|है)"], ("डिस्क",))
_hadd("local_ip", [r"(?:मेरा|मेरे)\s*(?:आईपी|आई\s*पी)\s*(?:एड्रेस\s*)?(?:" + H_SHOW + r")"], ("आईपी", "आई पी"))
_hadd("empty_recycle_bin", [r"रीसायकल\s*बिन\s*(?:को\s*)?(?:खाली|साफ़?)\s*" + H_DO], ("रीसायकल",))
_hadd("memory_recall", [r"मेरे\s*बारे\s*(?:में|मे)\s*(?:तुम्हें|तुम्हे|आपको)\s*क्या\s*(?:याद|पता)"], ("याद", "पता"))
_hadd("memory_forget_all", [r"(?:मेरे\s*बारे\s*(?:में|मे)\s*)?सब\s*कुछ\s*भूल\s*जाओ"], ("भूल",))
_hadd("run_routine", [r"(?:मेरा\s*)?(.+?)\s*रूटीन\s*(?:शुरू|चालू|स्टार्ट)\s*" + H_DO], ("रूटीन",))
_hadd("copy_selection", [r"(?:चुना\s*हुआ|सेलेक्ट\s*किया\s*हुआ|यह)?\s*(?:टेक्स्ट)?\s*कॉपी\s*" + H_DO], ("कॉपी",))
_hadd("paste_clipboard", [r"(?:यहाँ|यहां|इधर)\s*पेस्ट\s*" + H_DO, r"पेस्ट\s*" + H_DO], ("पेस्ट",))
_hadd("select_all", [r"(?:सब\s*कुछ|सारा|सब)\s*सेलेक्ट\s*" + H_DO], ("सेलेक्ट",))
_hadd("undo", [r"(?:वो|वह|ये|यह)?\s*अनडू\s*" + H_DO], ("अनडू",))


# ── English priority fixes found by the benchmark ────────────────────────
_REORDER = (
    ("dark_mode", "switch_to_application"),
    ("light_mode", "switch_to_application"),
    ("find_file", "web_search"),
    ("disk_usage", "system_info"),
    ("unmute", "mute"),
    ("next_tab", "switch_to_application"),
    ("prev_tab", "switch_to_application"),
    ("summarize_url", "news"),
    ("lock_pc", "lap_stopwatch"),
    ("sleep_system", "lap_stopwatch"),
    ("restart_system", "lap_stopwatch"),
    ("shutdown_system", "lap_stopwatch"),
    ("hibernate_system", "lap_stopwatch"),
)


def reorder(intent_patterns: list) -> None:
    """Move each first-named intent directly before the second one (stable, idempotent)."""
    for first, before in _REORDER:
        names = [n for n, _ in intent_patterns]
        if first not in names or before not in names:
            continue
        i, j = names.index(first), names.index(before)
        if i < j:
            continue
        entry = intent_patterns.pop(i)
        intent_patterns.insert(j, entry)


def apply(intent_patterns: list, intent_gates: dict) -> None:
    """Append the extra patterns to their intents and extend the gates."""
    index = {name: pos for pos, (name, _) in enumerate(intent_patterns)}
    for intent, patterns, gate in _SPECS:
        if intent not in index:
            continue
        pos = index[intent]
        name, existing = intent_patterns[pos]
        groups = max((re.compile(p).groups for p in existing), default=0)
        merged = list(existing)
        for pat in patterns:
            if pat in merged:
                continue
            compiled = re.compile(pat, re.IGNORECASE)
            if compiled.groups != groups:      # keep the handler-visible group layout
                continue
            merged.append(pat)
        intent_patterns[pos] = (name, merged)
        if intent in intent_gates:
            old = tuple(intent_gates[intent])
            intent_gates[intent] = old + tuple(g for g in gate if g.lower() not in old)
        elif gate:
            intent_gates[intent] = tuple(g.lower() for g in gate)
    reorder(intent_patterns)


# Words the typo-rescue pass must never "correct" into a command word
# (for example "science" -> "silence" would mute the volume).
TYPO_PROTECTED = frozenset({"science", "sciences", "signal", "silent", "vision", "visit", "viewer", "lacks"})
