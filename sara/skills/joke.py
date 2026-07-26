"""
sara.skills.joke
"Tell me a joke" — a small offline joke bank. Deliberately doesn't call
the LLM or any network tool: a joke should come back instantly and never
fail just because Ollama is cold or the network is down.

Keeps a small in-memory "recently told" window (not persisted — resets
each run) so the same joke doesn't repeat back-to-back.
"""
import random

INTENT_NAME = "tell_joke"

PATTERNS = [
    r"tell me a joke",
    r"got any jokes?",
    r"say something funny",
    r"make me laugh",
    r"(?:koi )?joke sunao",
    r"ek joke sunao",
    r"koi joke bolo",
    r"mujhe (?:hasao|hasaao)",
]

GATE = ("joke", "funny", "laugh", "hasao", "hasaao")

_JOKES = [
    "Why do programmers prefer dark mode? Because light attracts bugs.",
    "I told my computer I needed a break, and now it won't stop sending me KitKats.",
    "Why did the developer go broke? Because they used up all their cache.",
    "I'm reading a book about anti-gravity. It's impossible to put down.",
    "Why do Java developers wear glasses? Because they don't C sharp.",
    "I would tell you a UDP joke, but you might not get it.",
    "Why was the math book sad? It had too many problems.",
    "Parallel lines have so much in common. It's a shame they'll never meet.",
    "Why did the student eat his homework? Because the teacher said it was a piece of cake.",
    "I'm on a seafood diet — every time I see food, I eat it.",
]

_recent: list = []


def handle(match, ctx):
    ui_update = ctx["ui_update"]
    tts = ctx["tts"]

    available = [j for j in _JOKES if j not in _recent] or _JOKES
    joke = random.choice(available)

    _recent.append(joke)
    if len(_recent) > 3:
        _recent.pop(0)

    ui_update("status", "speaking")
    tts.speak(joke, fast=True)
    return joke
