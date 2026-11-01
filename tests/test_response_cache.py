"""Part 3 / P7: the response cache never serves anything time-, user-, memory- or tool-dependent."""
import pytest

from sara.core.response_cache import ResponseCache, is_cacheable_question, is_cacheable_reply, normalize

SHOULD_CACHE = [
    "python me list aur tuple me kya fark hai",
    "photosynthesis kya hota hai",
    "what is the difference between a list and a tuple",
    "explain how a transformer neural network works",
    "who invented the telephone",
    "why is the sky blue",
    "what is the full form of HTML",
    "how does a refrigerator work",
    "define machine learning",
    "gravity kya hai",
]

MUST_NOT_CACHE = [
    "what time is it", "what's the time now", "abhi kitne baje hain kya", "aaj ki date kya hai",
    "mera naam kya hai", "meri age kya hai", "what is my name", "who am i", "mujhe kya pasand hai",
    "what is the weather in delhi", "mausam kaisa hai aaj", "latest news kya hai", "what is the price of bitcoin",
    "what is the current score", "what is the capital of india 2024", "who is the current prime minister",
    "remember that my keys are on the table", "what did you say before", "explain it again", "what is that",
    "tum kaun ho", "what are you", "your name kya hai", "uska matlab kya hai", "what is the temperature",
    "kal ka mausam kya hai", "open chrome", "play music", "set an alarm", "ok", "hi", "",
    "what is 25 times 4",
]


@pytest.mark.parametrize("q", SHOULD_CACHE)
def test_stable_knowledge_questions_are_cacheable(q):
    assert is_cacheable_question(q), q


@pytest.mark.parametrize("q", MUST_NOT_CACHE)
def test_dynamic_personal_or_tool_questions_never_cache(q):
    assert not is_cacheable_question(q), q


def test_roundtrip_ttl_and_scope():
    t = [1000.0]
    c = ResponseCache(ttl_days=7, clock=lambda: t[0])
    q = "photosynthesis kya hota hai"
    r = "Photosynthesis woh process hai jisme paudhe sunlight se khana banate hain."
    assert c.put(q, r, "hinglish", "p1", "m1")
    assert c.get("Photosynthesis, kya hota hai?", "hinglish", "p1", "m1") == r      # normalized
    assert c.get(q, "english", "p1", "m1") is None                                 # other language
    assert c.get(q, "hinglish", "p2", "m1") is None                                # other persona version
    assert c.get(q, "hinglish", "p1", "m2") is None                                # other model
    t[0] += 8 * 86400
    assert c.get(q, "hinglish", "p1", "m1") is None                                # expired


def test_never_stores_bad_replies_or_uncacheable_questions():
    c = ResponseCache()
    assert not c.put("what time is it", "It is ten past four in the evening right now.")
    assert not c.put("photosynthesis kya hota hai", "Sorry, I had trouble responding to that.")
    assert not c.put("photosynthesis kya hota hai", "ok")
    assert c.size() == 0
    assert not is_cacheable_reply("x" * 2000)


def test_lru_eviction_keeps_the_most_recently_used():
    t = [0.0]
    c = ResponseCache(max_entries=3, clock=lambda: t[0])
    qs = [f"what is the thing called topic{chr(97 + i)} in science" for i in range(4)]
    for i, q in enumerate(qs[:3]):
        t[0] = i + 1
        c.put(q, f"A long enough answer number {i} about the topic.")
    t[0] = 10
    assert c.get(qs[0])                      # refresh the oldest
    t[0] = 11
    c.put(qs[3], "A long enough answer number 3 about the topic.")
    assert c.size() == 3
    assert c.get(qs[1]) is None and c.get(qs[0]) and c.get(qs[3])


def test_near_duplicate_needs_the_similarity_threshold():
    vecs = {"explain how a transformer works": [1.0, 0.0], "explain how transformers work": [0.99, 0.05],
            "explain how a car engine works": [0.0, 1.0]}
    c = ResponseCache(embed=lambda s: vecs[normalize(s)])
    c.put("explain how a transformer works", "Transformers use attention to weigh every word against every other.")
    assert c.get("explain how transformers work")
    assert c.get("explain how a car engine works") is None


def test_clear_and_hit_rate():
    c = ResponseCache()
    c.put("define machine learning", "Machine learning lets computers learn patterns from data.")
    c.get("define machine learning")
    c.get("define deep learning")
    assert c.hit_rate() == 0.5
    assert c.clear() == 1 and c.size() == 0
