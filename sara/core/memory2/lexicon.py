"""Multilingual cue lexicon for Memory 2.0 (English, Hinglish, Devanagari)."""
from __future__ import annotations

import unicodedata

__all__ = [
    "CUE_TOKENS",
    "KIN_GROUPS",
    "PREDICATE_HINTS",
    "STOP",
    "has_history_cue",
    "is_memory_question",
    "predicates_in_query",
    "tokens",
]


def tokens(text: object) -> list[str]:
    """Lowercase NFKC word tokens; Devanagari combining marks stay inside words."""
    out: list[str] = []
    cur: list[str] = []
    for ch in unicodedata.normalize("NFKC", str(text or "")).lower():
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _words(raw: str) -> frozenset[str]:
    return frozenset(t for w in raw.split() for t in tokens(w))


def _phrase(raw: str) -> str:
    return " ".join(tokens(raw))


PREDICATE_HINTS: dict[str, tuple[str, ...]] = {
    "lives_in": ("live", "lives", "living", "lived", "reside", "resides", "home", "city", "town",
                 "address", "rehta", "rehti", "rehte", "rahta", "rahti", "rahte", "ghar", "sheher",
                 "shahar", "रहता", "रहती", "रहते", "घर", "शहर"),
    "works_at": ("work", "works", "working", "job", "employer", "company", "office", "employed",
                 "kaam", "naukri", "काम", "नौकरी", "कंपनी", "दफ्तर"),
    "studies_at": ("study", "studies", "studying", "college", "university", "school", "padhta",
                   "padhti", "padhai", "पढ़ता", "पढ़ती", "पढ़ाई", "कॉलेज"),
    "name_is": ("name", "named", "called", "call me", "naam", "नाम"),
    "age": ("old", "age", "umar", "umr", "उम्र", "आयु"),
    "birthday": ("birthday", "born", "bday", "birth", "janamdin", "janmdin", "janamdiwas",
                 "जन्मदिन", "जन्म"),
    "likes": ("like", "likes", "love", "loves", "enjoy", "enjoys", "pasand", "hobby", "hobbies",
              "पसंद", "शौक"),
    "dislikes": ("dislike", "dislikes", "hate", "hates", "napasand", "नापसंद"),
    "favorite_food": ("food", "dish", "eat", "eating", "meal", "cuisine", "khana", "khaana", "खाना"),
    "favorite_color": ("color", "colour", "rang", "रंग"),
    "has_pet": ("pet", "pets", "dog", "cat", "puppy", "kutta", "kutte", "billi", "पालतू", "कुत्ता"),
    "speaks": ("speak", "speaks", "bolta", "bolti", "बोलता", "बोलती"),
    "preferred_language": ("language", "languages", "bhasha", "भाषा"),
    "plays": ("play", "plays", "playing", "khelta", "khelti", "khel", "sport", "sports", "game",
              "खेल", "खेलता", "खेलती"),
    "knows": ("knows", "jaanta", "jaanti", "जानता", "जानती"),
    "uses": ("use", "uses", "using", "istemal", "इस्तेमाल"),
    "interested_in": ("interest", "interested", "interests", "ruchi", "रुचि", "दिलचस्पी"),
    "wake_time": ("wake", "wakes", "wakeup", "alarm", "uthta", "uthti", "uthna", "उठता", "उठती"),
    "relationship_status": ("relationship", "married", "marital", "single", "shaadi", "shadi",
                            "शादी", "वैवाहिक"),
}

_COMPILED: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {}
for _pred, _cues in PREDICATE_HINTS.items():
    _singles: set[str] = set()
    _phrases: list[str] = []
    for _cue in _cues:
        _toks = tokens(_cue)
        if len(_toks) == 1:
            _singles.add(_toks[0])
        elif _toks:
            _phrases.append(" ".join(_toks))
    _COMPILED[_pred] = (frozenset(_singles), tuple(_phrases))

CUE_TOKENS: frozenset[str] = frozenset(t for singles, _ in _COMPILED.values() for t in singles)

_STOP_RAW = """
a an the is are was were am be been do does did i me my mine you your we our it its of to in on at
for with about and or but not no yes what which who whom whose when where why how tell told say said
remember remind please can could would will shall have has had that this these those there here s t
m ll d re ve now current currently right just also ever still any some mention mentioned earlier
before previously ago today yesterday tomorrow tonight last next week weeks day days month months
year years monday tuesday wednesday thursday friday saturday sunday favorite favourite fav best
thing things happened happen doing done get got one two three four five six seven eight nine ten
main mai mein mera meri mere mujhe mujhko maine humne tum tumhe tumko aap kya kab kahan kaun kaunsa
kaunsi kaunse kitne kitna kitni kis kisko kisne kise ki ka ke ko se hai hain hoon ho tha thi the
hota hoti hote raha rahi rahe baare bare batao bataya bataiye bata yaad kiya kiye ab abhi aaj kal
parso pichle pehle pahle din hafte hafta mahine bhi to toh na nahi ye yeh wo woh is us isme jo aur
ya par lekin kuch sab apna apni apne bhool jao
मैं मैंने मेरा मेरी मेरे मुझे मुझको क्या कब कहाँ कौन कौनसा कितने कितना कि का के की को से है
हैं हूँ था थी थे में पर और या भी तो बताओ बताया बताइए याद अभी अब आज कल परसों पहले पिछले दिन
हफ्ते महीने यह वह इस उस जो कुछ सब अपना अपनी अपने भूल जाओ
"""
STOP: frozenset[str] = _words(_STOP_RAW)

_KIN_RAW = (
    "mother mom mum mummy maa mata माँ मां मम्मी माता",
    "father dad daddy papa pita पिता पापा पिताजी",
    "sister sis behen behan bahan बहन",
    "brother bro bhai भाई",
    "friend friends dost yaar दोस्त",
    "roommate flatmate",
    "grandmother grandma nani dadi नानी दादी",
    "grandfather grandpa nana dada नाना दादा",
    "cousin uncle chacha aunt aunty chachi mausi bua",
    "wife biwi patni पत्नी बीवी",
    "husband pati पति",
    "son beta बेटा",
    "daughter beti बेटी",
    "boss manager colleague coworker teacher",
    "girlfriend boyfriend partner",
)
KIN_GROUPS: tuple[frozenset[str], ...] = tuple(_words(g) for g in _KIN_RAW)

_HISTORY = _words(
    "before earlier previously previous formerly used history past was were pehle pahle purane "
    "purana purani पहले पुराने पुराना पुरानी"
)

_MEMORY_PHRASES = tuple(
    _phrase(p)
    for p in (
        "do you remember", "did you remember", "you remember", "what did i tell you",
        "what did i say", "did i tell you", "did i mention", "have i told you",
        "what have i told you", "what do you know about me", "remember when i",
        "remember what i", "yaad hai", "yaad he", "maine kya bataya", "maine kya kaha",
        "tumhe yaad", "याद है", "याद हैं", "मैंने क्या बताया", "मैंने क्या कहा", "तुम्हें याद",
    )
)
_POSSESSIVE = _words("my mine mera meri mere मेरा मेरी मेरे")
_INTERROGATIVE = _words(
    "what which who when where how do does did is are am was were kya kab kahan kaun kaunsa kaunsi "
    "kaunse kitne kitna kitni क्या कब कहाँ कौन कौनसा कितने कितना"
)
_PERSONAL_PREDS = (
    "lives_in", "works_at", "studies_at", "name_is", "age", "birthday", "favorite_food",
    "favorite_color", "has_pet", "speaks", "preferred_language", "wake_time", "relationship_status",
)
_PERSONAL_NOUNS = _words("favorite favourite fav phone number email mobile password hobby")


def predicates_in_query(query: str) -> list[str]:
    """Predicates whose English / Hinglish / Hindi cue words appear in ``query``."""
    toks = tokens(query)
    if not toks:
        return []
    tset = set(toks)
    padded = " " + " ".join(toks) + " "
    return [
        pred
        for pred, (singles, phrases) in _COMPILED.items()
        if tset & singles or any(f" {p} " in padded for p in phrases)
    ]


def has_history_cue(query: str) -> bool:
    """True when the query asks about earlier / superseded values."""
    return bool(set(tokens(query)) & _HISTORY)


def is_memory_question(query: str) -> bool:
    """True for explicit memory phrasing or a question about the user's own stored data."""
    toks = tokens(query)
    if not toks:
        return False
    tset = set(toks)
    padded = " " + " ".join(toks) + " "
    if any(f" {p} " in padded for p in _MEMORY_PHRASES):
        return True
    if ({"maine", "bataya"} <= tset) or ({"मैंने", "बताया"} <= tset):
        return True
    if not (tset & _POSSESSIVE) or not (tset & _INTERROGATIVE):
        return False
    preds = set(predicates_in_query(query))
    kin = any(g & tset for g in KIN_GROUPS)
    return bool(preds & set(_PERSONAL_PREDS)) or kin or bool(tset & _PERSONAL_NOUNS)