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

_HINGLISH_MARKERS = frozenset({"mujhe", "mera", "meri", "mere", "bhool", "jao", "hai", "ki", "main"})


def _mem2():
    """Memory 2.0 store, or None when disabled or unavailable."""
    try:
        from sara.core.memory2 import get_store

        return get_store()
    except Exception:
        return None


def _preview_lang(phrase: str) -> str:
    if set(phrase.lower().split()) & _HINGLISH_MARKERS or any("\u0900" <= ch <= "\u097f" for ch in phrase):
        return "hinglish"
    return "english"


def _memory2_recall_line() -> str:
    """Spoken list of pinned / strongest active user facts ('' when none or on any failure)."""
    store = _mem2()
    if store is None:
        return ""
    try:
        from datetime import datetime

        from sara.core.memory2 import decay

        now = time.time()
        tau = decay.tau_days()
        rows = [
            r for r in store.iter_rows("facts", ("active",))
            if str(r.get("subject") or "user").lower() == "user" and r.get("text")
        ]
        rows.sort(key=lambda r: (not r.get("pinned"), -decay.strength(
            float(r.get("importance") or 0.5), decay.row_ref_ts(r), int(r.get("use_count") or 0),
            bool(r.get("pinned")), now, tau)))
        top = rows[:5]
        if not top:
            return ""
        first = float(top[0].get("valid_from") or top[0].get("created_at") or now)
        since = datetime.fromtimestamp(first).strftime("%d %B %Y")
        parts = [f"{top[0]['text']} (since {since})"] + [str(r["text"]) for r in top[1:]]
        return "Here's what I remember: " + "; ".join(parts) + "."
    except Exception as e:
        print(f"[Memory2] recall failed: {type(e).__name__}")
        return ""


def _arm_forget_items(ctx, phrase: str):
    """Look in Memory 2.0 and RAG; arm a confirmation. None when Memory 2.0 is off or nothing matched."""
    store = _mem2()
    if store is None:
        return None
    hits = []
    try:
        from sara.core.memory2 import forget

        hits = forget.find_matches(phrase, store=store)
    except Exception as e:
        print(f"[Memory2] forget lookup failed: {type(e).__name__}")
    rag_ids, rag_texts = [], []
    rag = ctx.get("notes_memory")
    if rag is not None and _HAS_RAG and getattr(rag, "enabled", False):
        try:
            best = _best_fuzzy_memory_match(phrase, rag.list_memories())
            if best is not None and best["score"] >= _MEMORY_FORGET_MATCH_THRESHOLD:
                rag_ids.append(best["id"])
                rag_texts.append(str(best.get("text") or ""))
        except Exception as e:
            print(f"[Memory] list_memories failed: {e}")
    if not hits and not rag_ids:
        return None
    try:
        from sara.core.memory2 import forget
        from sara.core.memory2.types import Mem2Hit

        shown = list(hits) + [
            Mem2Hit("rag", int(i), t, 1.0, "", 1.0, None, "active", False)
            for i, t in zip(rag_ids, rag_texts)
        ]
        preview = forget.preview_text(shown, _preview_lang(phrase))
    except Exception as e:
        print(f"[Memory2] preview failed: {type(e).__name__}")
        return None
    ctx["confirm_state"]["pending"] = {
        "action": "forget_memory_items",
        "target": phrase,
        "mem2_ids": [[h.kind, h.id] for h in hits],
        "rag_ids": rag_ids,
        "expires_at": time.time() + _CONFIRM_PENDING_TTL_S,
    }
    return _quick(ctx, preview)


def apply_forget_items(pending: dict, ctx) -> str:
    """Execute a confirmed 'forget_memory_items' action; returns the spoken reply."""
    forgotten = 0
    store = _mem2()
    if store is not None:
        for entry in pending.get("mem2_ids") or []:
            try:
                if store.retract(int(entry[1]), str(entry[0])):
                    forgotten += 1
            except Exception as e:
                print(f"[Memory2] retract failed: {type(e).__name__}")
    rag = ctx.get("notes_memory")
    if rag is not None and _HAS_RAG and getattr(rag, "enabled", False):
        for rag_id in pending.get("rag_ids") or []:
            try:
                if rag.delete_memory(rag_id):
                    forgotten += 1
            except Exception as e:
                print(f"[Memory] delete_memory failed: {e}")
    if forgotten <= 0:
        return "Sorry, I ran into a problem forgetting that."
    return f"Okay, I've forgotten {forgotten} thing{'s' if forgotten != 1 else ''}."

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
    m2_line = _memory2_recall_line()
    if not m2_line and rag is not None and _HAS_RAG and getattr(rag, "enabled", False):
        try:
            hits = rag.search(
                "personal facts and preferences about the user", top_k=5
            )
            memory_texts = [h.text for h in hits]
        except Exception as e:
            print(f"[Memory] recall search failed: {e}")

    if m2_line:
        name_part = f"I know your name is {user_name}. " if user_name else ""
        return _quick(ctx, name_part + m2_line)
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

    armed = _arm_forget_items(ctx, target_phrase)
    if armed is not None:
        return armed

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

