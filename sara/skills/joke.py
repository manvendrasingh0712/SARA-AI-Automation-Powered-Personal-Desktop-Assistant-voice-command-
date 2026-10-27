"""
sara.skills.joke
"Tell me a joke" — an offline joke bank (110+ jokes in 4 categories). It
deliberately doesn't need the LLM or the network by default, so a joke comes
back instantly and never fails because Ollama is cold or Wi-Fi is down.

  * Categories: programmer, student, hinglish, dad (+ "general" = any).
    "tell me a programmer joke", "ek student joke sunao", ...
  * Two-part jokes are spoken as setup -> short pause -> punchline, and the
    card reveals the punchline after a beat.
  * "ek aur" / "one more" (within 5 minutes) tells another of the same kind.
  * "funny tha" / "not funny" (within 5 minutes) teaches Sara which
    categories you like: stored as preferences `joke_score:<category>` and
    used to weight the random choice when you don't name a category.
  * The last 20 jokes told are persisted (preference `joke_recent`), so
    repeats are avoided across restarts too.
  * Optional: Config.JOKE_USE_LLM = True asks the LLM for a fresh joke first
    (bank is the fallback). Off by default -- it adds latency and the prompt
    joins conversation history.
"""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Optional

from ._framework import SkillContext, SkillResult, skill

logger = logging.getLogger("sara.skills.joke")

# (setup, punchline).  punchline None => a one-liner.
_BANK = {
    "programmer": [
        ("Why do programmers prefer dark mode?", "Because light attracts bugs."),
        ("Why did the developer go broke?", "Because they used up all their cache."),
        ("Why do Java developers wear glasses?", "Because they don't C sharp."),
        ("I would tell you a UDP joke,", "but you might not get it."),
        ("How many programmers does it take to change a light bulb?", "None, that's a hardware problem."),
        ("A SQL query walks into a bar, sees two tables and asks,", "'Can I join you?'"),
        ("Why was the JavaScript developer sad?", "Because he didn't know how to null his feelings."),
        ("There are only 10 kinds of people in the world:", "those who understand binary and those who don't."),
        ("Why do programmers always mix up Halloween and Christmas?", "Because Oct 31 equals Dec 25."),
        ("What's a programmer's favourite place to hang out?", "The Foo Bar."),
        ("Why did the programmer quit his job?", "He didn't get arrays."),
        ("How do you comfort a JavaScript bug?", "You console it."),
        ("Why was the function so calm?", "It had no side effects."),
        ("What did the router say to the doctor?", "It hurts when IP."),
        ("Why did the developer stay calm during the outage?", "He had a solid backup plan."),
        ("My code doesn't work and I have no idea why.", "My code works and I have no idea why. Both are terrifying."),
        ("What do you call 8 hobbits?", "A hobbyte."),
        ("Why don't programmers like nature?", "Too many bugs."),
        ("Why did the Python programmer not get invited to the party?", "He kept raising exceptions."),
        ("What is a computer's favourite snack?", "Microchips."),
        ("A programmer's wife says: 'Go to the store and buy a loaf of bread. If they have eggs, buy a dozen.'", "He came back with twelve loaves."),
        ("Why did the two threads go to therapy?", "They had trouble with synchronization."),
        ("What's the object-oriented way to become wealthy?", "Inheritance."),
        ("Why was the array so good at parties?", "It knew how to index."),
        ("Why did the database administrator leave his wife?", "She had one-to-many relationships."),
        ("What did the git user say after a bad merge?", "'I'm feeling a bit conflicted.'"),
        ("Why do programmers hate stairs?", "They prefer to loop."),
        ("How do you keep a programmer in the shower forever?", "Give them a shampoo bottle that says: lather, rinse, repeat."),
        ("What's the best thing about a Boolean?", "Even if you're wrong, you're only off by a bit."),
        ("Why was the computer cold?", "It left its Windows open."),
    ],
    "student": [
        ("Why did the student eat his homework?", "Because the teacher said it was a piece of cake."),
        ("Why was the math book sad?", "It had too many problems."),
        ("Why did the student bring a ladder to school?", "Because she wanted to go to high school."),
        ("What did the zero say to the eight?", "Nice belt."),
        ("Why is six afraid of seven?", "Because seven eight nine."),
        ("Why did the teacher wear sunglasses to class?", "Because her students were so bright."),
        ("What do you call a sleeping student in class?", "A rest in peace of mind."),
        ("Why was the equal sign so humble?", "It knew it wasn't less than or greater than anyone else."),
        ("What's a physicist's favourite food?", "Fission chips."),
        ("Why can't you trust atoms?", "They make up everything."),
        ("What did the calculator say to the student?", "You can count on me."),
        ("Why did the biology student break up with the chemistry student?", "There was no chemistry."),
        ("Why did the teacher put the student's report card in the fridge?", "Because it had too many Cs."),
        ("What's the chemist's favourite kind of joke?", "Periodic ones, they never get old."),
        ("Why did the scarecrow get an award?", "He was outstanding in his field."),
        ("How does a student make a nap productive?", "By calling it 'active recall'."),
        ("What do you call an angle that's lying down?", "Acute-ly comfortable."),
        ("Why was the student's pencil so stressed?", "It was always being pointed at."),
        ("What did one history book say to the other?", "'I've got a lot of dates.'"),
        ("Why do students love the chemistry of coffee?", "Because there's no reaction quite like 3 a.m. revision."),
        ("What did the pencil say to the eraser?", "You rock!"),
        ("Why did the exam paper look so nervous?", "It knew it was about to be marked."),
        ("I told my friend I'd start studying at 6 a.m.", "Neither of us specified which day."),
        ("Why did the physics teacher break up with the electric circuit?", "It was too resistant."),
        ("Why did the student do multiplication on the floor?", "The teacher said no tables allowed."),
    ],
    "hinglish": [
        ("Teacher ne pucha: 'Paani ka formula kya hai?' Student bola:", "Sir, H, I, J, K, L, M, N, O!"),
        ("Papa: Beta result kaisa aaya?", "Beta: Papa, jo padha tha wo aaya hi nahi."),
        ("Doctor: Aapko roz 8 ghante sona chahiye.", "Patient: Doctor sahab, class mein ya ghar pe?"),
        ("Bijli wala ghar aaya aur bola:", "'Bill bharo warna light chali jayegi.' Maine kaha: 'Wo to pehle se hi gayi hui hai.'"),
        ("Pappu ne exam mein likha:", "'Answer mujhe pata hai, par question galat pucha gaya hai.'"),
        ("Beta ne bola: Mummy, main kal se pakka padhunga.", "Mummy: Kaunse saal se?"),
        ("Teacher: Tum late kyun aaye?", "Student: Sir, sign board pe likha tha 'School ahead, go slow'."),
        ("Ek aadmi ne WiFi ka password poocha,", "dukaan wale ne kaha: 'Pehle kuch kharido, phir bataunga.' Ab wo chai peeta hai, password ke saath."),
        ("Dost: Yaar tu itna kam kyun bolta hai?", "Main: Data pack bachana padta hai."),
        ("Bhai: Mera phone garam kyun ho jaata hai?", "Dusra bhai: Kyunki tu usse dil se chalata hai."),
        ("Pati: Aaj khaane mein kya banaya?", "Patni: Kuch nahi. Pati: Wah! Kal bhi wahi tha na?"),
        ("Student: Sir, mujhe zero kaise mila?", "Teacher: Kyunki isse kam dene ka option nahi tha."),
        ("Dukandaar: Aunty, ye 500 ka note phata hai.", "Aunty: Beta, tabhi to sasta laga tha."),
        ("Chintu: Papa, aap mujhe roz padhne ko kyun kehte ho?", "Papa: Taaki tum meri tarah na bano. Chintu: Papa, aap to officer ho!"),
        ("Alarm ne subah kaha: 'Uth ja beta.'", "Maine kaha: 'Beta bolke dhoka mat de, main 5 minute aur soonga.'"),
        ("Gol gappe wale bhaiya se poocha: 'Paani kitna teekha hai?'", "Bhaiya: 'Aapki nazar jitna.'"),
        ("Ek kachua aur khargosh ki race hui,", "kachua jeet gaya kyunki khargosh ne reel dekhne ke liye rukna tha."),
        ("Teacher: Batao, sabse tez kya chalta hai?", "Student: Sir, exam ka time."),
        ("Mummy: Beta phone rakh, aankhein kharab ho jayengi.", "Beta: Mummy, chashma to already laga hua hai."),
        ("Maine gym join kiya,", "ab main bhi kehta hoon 'kal se pakka jaunga'."),
        ("Doctor ne kaha: 'Aapko aaram chahiye.'", "Maine kaha: 'Doctor sahab, wo to mujhe hamesha chahiye tha.'"),
        ("Bhai ne pucha: 'Tu itna sochta kyun hai?'", "Maine kaha: 'Kyunki soch pe koi GST nahi lagta.'"),
        ("Ek dost ne kaha: 'Life mein set ho jao.'", "Maine kaha: 'Bhai, mera to phone bhi set nahi hota.'"),
        ("Student: Sir, kya main aapse ek sawaal pooch sakta hoon?", "Teacher: Haan. Student: Sir, chhutti kab hai?"),
        ("Aaj kal ke bacche itne smart hain,", "ki Google se pehle mummy ko hi search kar lete hain."),
    ],
    "dad": [
        ("I'm reading a book about anti-gravity.", "It's impossible to put down."),
        ("I told my computer I needed a break,", "and now it won't stop sending me KitKats."),
        ("Parallel lines have so much in common.", "It's a shame they'll never meet."),
        ("I'm on a seafood diet.", "Every time I see food, I eat it."),
        ("Why don't skeletons fight each other?", "They don't have the guts."),
        ("What do you call fake spaghetti?", "An impasta."),
        ("Why did the coffee file a police report?", "It got mugged."),
        ("I used to hate facial hair,", "but then it grew on me."),
        ("What do you call cheese that isn't yours?", "Nacho cheese."),
        ("Why did the bicycle fall over?", "It was two tired."),
        ("What did the ocean say to the beach?", "Nothing, it just waved."),
        ("I only know 25 letters of the alphabet.", "I don't know y."),
        ("Did you hear about the restaurant on the moon?", "Great food, no atmosphere."),
        ("Why do cows wear bells?", "Because their horns don't work."),
        ("What's orange and sounds like a parrot?", "A carrot."),
        ("How does a penguin build its house?", "Igloos it together."),
        ("What do you call a bear with no teeth?", "A gummy bear."),
        ("Why did the tomato turn red?", "Because it saw the salad dressing."),
        ("I would avoid the sushi if I were you.", "It's a little fishy."),
        ("What did the janitor say when he jumped out of the closet?", "Supplies!"),
        ("Why did the golfer bring two pairs of pants?", "In case he got a hole in one."),
        ("What do you call a fish wearing a bowtie?", "Sofishticated."),
        ("Why can't a nose be 12 inches long?", "Because then it would be a foot."),
        ("How do you organize a space party?", "You planet."),
        ("What did the left eye say to the right eye?", "Between us, something smells."),
        ("I'm afraid for the calendar.", "Its days are numbered."),
        ("Why was the belt arrested?", "For holding up a pair of pants."),
        ("What do you call a factory that makes okay products?", "A satisfactory."),
        ("Why don't eggs tell jokes?", "They'd crack each other up."),
        ("I couldn't figure out why the baseball kept getting larger.", "Then it hit me."),
    ],
}

_CATEGORY_WORDS = {
    "programmer": ("programmer", "coding", "developer", "code", "tech"),
    "student": ("student", "school", "study", "padhai", "exam"),
    "hinglish": ("hinglish", "hindi", "desi"),
    "dad": ("dad",),
}
_FOLLOWUP_WINDOW_S = 300
_RECENT_KEY = "joke_recent"
_RECENT_MAX = 20

_last: dict = {"category": None, "at": 0.0, "scored": False}


def total_jokes() -> int:
    return sum(len(v) for v in _BANK.values())


def _category_from_text(text: str) -> Optional[str]:
    low = (text or "").lower()
    for cat, words in _CATEGORY_WORDS.items():
        if any(w in low for w in words):
            return cat
    return None


def _load_recent(ctx: SkillContext) -> list:
    try:
        data = json.loads(ctx.pref(_RECENT_KEY) or "[]")
        return [str(x) for x in data] if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _save_recent(ctx: SkillContext, recent: list) -> None:
    ctx.set_pref(_RECENT_KEY, json.dumps(recent[-_RECENT_MAX:]))


def _score(ctx: SkillContext, cat: str) -> int:
    try:
        return int(ctx.pref(f"joke_score:{cat}") or "0")
    except ValueError:
        return 0


def pick_joke(category: Optional[str], recent: list, scores: dict, rng=random):
    """Pure(ish) selection: returns (category, index, (setup, punch))."""
    if category in _BANK:
        cats = [category]
    else:
        cats = list(_BANK)
    pool = []
    for cat in cats:
        weight = max(0.2, 1.0 + 0.4 * scores.get(cat, 0)) if category not in _BANK else 1.0
        for i, joke in enumerate(_BANK[cat]):
            if f"{cat}:{i}" in recent:
                continue
            pool.append((weight, cat, i, joke))
    if not pool:  # everything recently told: reset the window for these cats
        for cat in cats:
            for i, joke in enumerate(_BANK[cat]):
                pool.append((1.0, cat, i, joke))
    weights = [p[0] for p in pool]
    _w, cat, idx, joke = rng.choices(pool, weights=weights, k=1)[0]
    return cat, idx, joke


def _llm_joke(ctx: SkillContext, category: Optional[str]):
    """Optional fresh joke from the LLM. Returns (setup, punch) or None."""
    try:
        from config import Config
        if not getattr(Config, "JOKE_USE_LLM", False) or ctx.brain is None:
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        kind = f"a short {category} joke" if category else "a short clean joke"
        style = "in Hinglish (Roman script)" if ctx.lang == "hinglish" else "in English"
        raw = ctx.brain.generate_response(
            f"Tell me {kind} {style}. Reply with ONLY the joke as 'SETUP || PUNCHLINE' "
            f"on one line, no extra words."
        )
        if raw and "||" in raw:
            setup, punch = [p.strip() for p in raw.split("||", 1)]
            if setup and punch:
                return setup, punch
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM joke failed, using bank: %s", e)
    return None


def _tell(ctx: SkillContext, category: Optional[str]) -> SkillResult:
    recent = _load_recent(ctx)
    scores = {c: _score(ctx, c) for c in _BANK}
    fresh = _llm_joke(ctx, category)
    if fresh is not None:
        cat = category or "general"
        setup, punch = fresh
        key = None
    else:
        cat, idx, (setup, punch) = pick_joke(category, recent, scores)
        key = f"{cat}:{idx}"
        recent.append(key)
        _save_recent(ctx, recent)

    _last.update(category=cat if cat in _BANK else None, at=time.time(), scored=False)
    if punch:
        segments = [(setup, 0.9), (punch, 0.0)]
        text = f"{setup} {punch}"
    else:
        segments, text = None, setup
    return SkillResult(
        text=text,
        segments=segments,
        card={"type": "joke", "category": cat, "setup": setup, "punchline": punch or "",
              "reveal_ms": 1500},
        chips=[ctx.t("One more", "Ek aur"), ctx.t("That was funny", "Funny tha"),
               ctx.t("Not funny", "Mazaa nahi aaya")],
    )


@skill(
    name="tell_joke",
    patterns=[
        r"tell me (?:a |an |another |one more )?(?:[a-z]+ )?joke",
        r"got any jokes?",
        r"say something funny",
        r"make me laugh",
        r"(?:koi )?(?:[a-z]+ )?joke sunao",
        r"ek (?:[a-z]+ )?joke sunao",
        r"koi joke bolo",
        r"mujhe (?:hasao|hasaao)",
    ],
    gate=("joke", "funny", "laugh", "hasao", "hasaao"),
    description="Tells a joke (programmer / student / hinglish / dad) from a 110+ joke offline bank",
    category="fun",
    examples=("tell me a joke", "tell me a programmer joke", "ek joke sunao", "mujhe hasao"),
)
def handle(match, ctx: SkillContext):
    return _tell(ctx, _category_from_text(ctx.user_input))


@skill(
    name="joke_again",
    patterns=[r"^\s*(?:ek aur|aur ek|aur sunao|one more|another one|another|one more joke)\s*[.!]*\s*$"],
    gate=("aur", "one more", "another"),
    description="'Ek aur' — another joke of the same kind (within 5 minutes)",
    category="fun",
)
def handle_again(match, ctx: SkillContext):
    if time.time() - _last["at"] > _FOLLOWUP_WINDOW_S:
        return None  # no recent joke: not for us
    return _tell(ctx, _last["category"])


@skill(
    name="joke_feedback",
    patterns=[
        r"^\s*(?:that was |ye |wo |bahut )?(?:funny|hilarious|mazedaar|mazedar)(?: tha| hai)?\s*[.!]*\s*$",
        r"^\s*(?:ha ?ha(?: ha)*|lol)\s*[.!]*\s*$",
        r"^\s*(?:not funny|boring(?: tha)?|mazaa nahi aaya|funny nahi tha)\s*[.!]*\s*$",
    ],
    gate=("funny", "haha", "lol", "hilarious", "mazedaar", "mazedar", "boring", "mazaa"),
    description="'Funny tha' / 'not funny' — teaches Sara which jokes you like",
    category="fun",
)
def handle_feedback(match, ctx: SkillContext):
    if time.time() - _last["at"] > _FOLLOWUP_WINDOW_S or _last["scored"]:
        return None
    low = ctx.user_input.lower()
    negative = any(w in low for w in ("not funny", "boring", "nahi"))
    cat = _last["category"]
    _last["scored"] = True
    if cat:
        ctx.set_pref(f"joke_score:{cat}", str(max(-5, min(5, _score(ctx, cat) + (-1 if negative else 1)))))
    if negative:
        return SkillResult(text=ctx.t("Noted, I'll pick different jokes next time.",
                                      "Theek hai, agli baar alag jokes chunungi."),
                           chips=[ctx.t("One more", "Ek aur")])
    return SkillResult(text=ctx.t("Glad you liked it! I'll tell more like that.",
                                  "Khushi hui! Aise aur sunaungi."),
                       chips=[ctx.t("One more", "Ek aur")])