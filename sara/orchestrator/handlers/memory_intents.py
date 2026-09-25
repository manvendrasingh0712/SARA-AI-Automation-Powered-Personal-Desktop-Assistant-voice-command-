"""
sara.orchestrator.handlers.memory_intents

Long-term (RAG) memory recall and forgetting. Split out of the former
monolithic intent_handlers.py -- see that module's docstring's MEMORY
MANAGEMENT / DECISION MEMORY section.
"""
import difflib
import time

from ..command_helpers import _quick, _ack, _MEMORY_FORGET_MATCH_THRESHOLD
from .._shared_state import _HAS_RAG, _CONFIRM_PENDING_TTL_S

def _best_fuzzy_memory_match(target_phrase: str, candidates: list) -> "dict | None":
    """
    Finds the RAG memory in `candidates` (list of {"id","text",...} from
    LongTermMemory.list_memories()) whose text best matches
    `target_phrase`, using stdlib difflib (no new dependency -- it's part
    of the Python standard library). Returns None if there are no
    candidates at all; otherwise returns the best candidate dict with an
    added "score" key (0-1) the caller checks against
    Config.MEMORY_FORGET_MATCH_THRESHOLD before acting on it.
    """
    if not target_phrase or not candidates:
        return None
    normalized_target = target_phrase.strip().lower()
    best = None
    best_score = 0.0
    for candidate in candidates:
        text = (candidate.get("text") or "").lower()
        ratio = difflib.SequenceMatcher(None, normalized_target, text).ratio()
        # Bonus for a direct substring match -- handles the common case
        # where the spoken phrase is a short fragment of a longer stored
        # memory sentence (e.g. "pizza" inside "user mentioned they like
        # pizza on weekends").
        if normalized_target in text:
            ratio = max(ratio, 0.8)
        if ratio > best_score:
            best_score = ratio
            best = candidate
    if best is None:
        return None
    return {**best, "score": best_score}


def _h_memory_recall(match, ctx):
    """
    "what do you remember about me" / "mere baare mein kya yaad hai" --
    speaks a short summary built from (1) the RAG long-term memory
    store's top semantic matches for a generic "personal facts" query,
    and (2) ONLY the user_name preference. Design decision: preferences
    DB's other keys (wake_word, streak_count, routine_last_run:*, etc.)
    are internal/system bookkeeping, not memories worth reading back to
    the user, so they are deliberately excluded here.
    """
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")

    db = ctx.get("db")
    user_name = db.get_preference("user_name") if db else None

    rag = ctx.get("notes_memory")
    memory_texts = []
    if rag is not None and _HAS_RAG and getattr(rag, "enabled", False):
        try:
            hits = rag.search(
                "personal facts and preferences about the user", top_k=5
            )
            memory_texts = [h.text for h in hits]
        except Exception as e:
            print(f"[Memory] recall search failed: {e}")

    if not memory_texts and not user_name:
        return _quick(ctx, "I don't have anything specific stored about you yet.")

    parts = []
    if user_name:
        parts.append(f"I know your name is {user_name}.")
    if memory_texts:
        joined = "; ".join(memory_texts[:5])
        parts.append(f"Here's what I remember: {joined}.")
    return _quick(ctx, " ".join(parts))


def _h_memory_forget_specific(match, ctx):
    """
    "forget that I like X" / "ye bhool jao ki mujhe X pasand hai" --
    fuzzy-matches the spoken phrase against ACTUAL stored RAG memories
    (never against preferences DB's system keys) and deletes only the
    single best match, ONLY if it clears
    Config.MEMORY_FORGET_MATCH_THRESHOLD. If nothing matches
    confidently, says so instead of guessing -- per this feature's spec,
    this must never wipe the wrong memory or the whole store.
    """
    if not match:
        return None
    target_phrase = match.group(1).strip()
    if not target_phrase:
        return _quick(ctx, "What would you like me to forget?")

    rag = ctx.get("notes_memory")
    if rag is None or not _HAS_RAG or not getattr(rag, "enabled", False):
        return _quick(ctx, "I don't have long-term memory available right now.")

    try:
        candidates = rag.list_memories()
    except Exception as e:
        print(f"[Memory] list_memories failed: {e}")
        return _quick(ctx, "Sorry, I couldn't look through my memories right now.")

    best = _best_fuzzy_memory_match(target_phrase, candidates)
    if best is None or best["score"] < _MEMORY_FORGET_MATCH_THRESHOLD:
        return _quick(
            ctx,
            f"I couldn't find a specific memory about '{target_phrase}' to forget. "
            f"Could you say it a bit differently?",
        )

    _ack(ctx)
    try:
        ok = rag.delete_memory(best["id"])
    except Exception as e:
        print(f"[Memory] delete_memory failed: {e}")
        ok = False
    if ok:
        return _quick(ctx, "Okay, I've forgotten that.")
    return _quick(ctx, "Sorry, I ran into a problem forgetting that.")


def _h_memory_forget_all(match, ctx):
    """
    "forget everything you know about me" -- a full wipe of the RAG
    long-term memory store is destructive and irreversible, so this
    NEVER executes on the first utterance. It only arms a pending
    confirmation (the SAME confirm_state mechanism already used for
    close_app/stop_service on risky targets -- see _handle_command's
    pending-confirmation block below for where "yes"/"cancel" is
    actually resolved and rag.clear_all() is actually called).

    SCOPE NOTE: this wipes ONLY the RAG long-term memory store, not
    conversation_log (that's the existing, separate "forget everything"/
    "forget our conversation" _FORGET_WORDS path above, untouched by
    this feature) and not preferences like user_name.
    """
    ctx["confirm_state"]["pending"] = {
        "action": "forget_all_memories",
        "target": None,
        "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
    }
    return _quick(
        ctx,
        "This will permanently delete everything I've remembered about you "
        "long-term -- are you sure? Say yes or cancel.",
    )

