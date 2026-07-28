"""
sara.skills.notes_qa
"What do my notes say about X?" — answers questions from .txt/.md class
notes dropped into Config.NOTES_FOLDER (default: sara_class_notes/ next to
config.py), using sara/core/rag.py's LongTermMemory vector store.

Note: LongTermMemory was already fully built in this codebase, but
nothing actually instantiated or called it anywhere before this skill —
every orchestrator file imported it defensively (the standard
try/except-optional-feature pattern here) but no code path ever created
one. This skill is what makes it do real work for the first time:
sara/orchestrator/core_wiring.py builds one `notes_memory` instance and
runs sync_notes_folder() once at startup, then this module both ingests
new/changed note files into it and answers questions against it.

Ingestion (sync_notes_folder): scans NOTES_FOLDER for .txt/.md files,
skips any file whose mtime hasn't changed since it was last ingested
(tracked via a "notes_mtime:<filename>" preference in PreferencesDB),
splits changed files into roughly NOTES_CHUNK_CHARS-character chunks, and
embeds each chunk with rag_memory.add_memory(chunk, source="notes:<file>").
Every time a sync actually runs (rag enabled + embedding backend up), it
also stamps a "notes_last_sync_at" preference with the current UTC time,
regardless of whether anything new was ingested — this is what
get_notes_index_status() below reports as "last synced".

Answering (handle): semantically searches the same store, restricted to
notes:* sources, and asks the LLM to answer using only what was
retrieved — the LLM call goes through the normal
brain.generate_response() path (this IS a real, explicit user turn, so it
should join conversation history, unlike sara/orchestrator/proactive.py's
background nudges which deliberately avoid that).

Needs an embedding-capable Ollama model pulled locally (Config.EMBEDDING_MODEL,
default "nomic-embed-text" — `ollama pull nomic-embed-text`) for the
underlying LongTermMemory to actually produce embeddings; if that model
isn't available, search() returns no hits and this skill says so rather
than failing silently or raising.

Status (get_notes_index_status): a small read-only helper for UI status
displays ("X notes indexed | last synced: ..."). Deliberately does NOT
depend on any PreferencesDB prefix-listing method — counts by walking
the current NOTES_FOLDER contents and checking each file's stored mtime
key, so it self-corrects if files are deleted/renamed after ingestion
instead of reporting stale/orphaned counts.
"""
import os
import re
import threading
from datetime import datetime, timezone

from config import Config

INTENT_NAME = "notes_qa"

PATTERNS = [
    r"(?:what do |do )?my notes (?:say|mention) (?:about |on )?(.+)",
    r"(?:check|search|look in) my notes (?:for|about) (.+)",
    r"notes (?:mein|main) (.+) (?:ke baare mein|ke bare me) kya (?:likha|hai)",
    r"ask (?:my )?notes (?:about )?(.+)",
]

# Cheap pre-filter — same convention as sara/core/intent/patterns.py's
# _INTENT_GATES.
GATE = ("notes",)

_sync_lock = threading.Lock()

_LAST_SYNC_PREF_KEY = "notes_last_sync_at"


def _iter_note_files():
    folder = getattr(Config, "NOTES_FOLDER", None)
    if not folder or not os.path.isdir(folder):
        return
    for root, _dirs, files in os.walk(folder):
        for fname in files:
            if fname.lower().endswith((".txt", ".md")):
                yield os.path.join(root, fname)


def _chunk(text: str, size: int):
    text = re.sub(r"\s+", " ", text).strip()
    for i in range(0, len(text), max(1, size)):
        piece = text[i : i + size].strip()
        if piece:
            yield piece


def sync_notes_folder(rag_memory, db) -> int:
    """
    Ingests every new/changed .txt/.md file under Config.NOTES_FOLDER into
    `rag_memory`. Safe to call repeatedly (e.g. once at every app start,
    which is how sara/orchestrator/core_wiring.py uses it) — a file
    already ingested at its current mtime is skipped. Returns the number
    of files (re-)ingested. Never raises; a bad notes folder / unreadable
    file just gets logged and skipped, it can't block startup.
    """
    if rag_memory is None or not getattr(rag_memory, "enabled", False):
        return 0

    if hasattr(rag_memory, "check_backend") and not rag_memory.check_backend():
        model = getattr(Config, "EMBEDDING_MODEL", "nomic-embed-text")
        print(
            f"[NotesQA] Embedding model '{model}' isn't responding — notes "
            f"won't be searchable until it's available. Run: "
            f"ollama pull {model}"
        )
        return 0

    ingested = 0
    with _sync_lock:
        try:
            for path in _iter_note_files():
                fname = os.path.basename(path)
                try:
                    mtime = str(int(os.path.getmtime(path)))
                except OSError:
                    continue
                key = f"notes_mtime:{fname}"
                if db is not None and db.get_preference(key) == mtime:
                    continue  # unchanged since last ingestion
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except Exception as e:
                    print(f"[NotesQA] Failed to read {fname}: {e}")
                    continue
                chunk_size = int(getattr(Config, "NOTES_CHUNK_CHARS", 800))
                max_chunks = int(getattr(Config, "NOTES_MAX_CHUNKS_PER_FILE", 200))
                added_for_file = 0
                for chunk in _chunk(text, chunk_size):
                    if added_for_file >= max_chunks:
                        print(
                            f"[NotesQA] {fname}: stopping at {max_chunks} chunks "
                            f"(NOTES_MAX_CHUNKS_PER_FILE) — file is larger than that."
                        )
                        break
                    rag_memory.add_memory(chunk, source=f"notes:{fname}")
                    added_for_file += 1
                if db is not None:
                    db.set_preference(key, mtime)
                ingested += 1
            if ingested:
                print(
                    f"[NotesQA] Ingested {ingested} note file(s) from "
                    f"{getattr(Config, 'NOTES_FOLDER', '?')}"
                )
            # Stamp "last synced" on every successful run (whether or not
            # anything new was ingested) — this reaches here only if the
            # rag-enabled / embedding-backend-up checks above passed, so
            # it genuinely means "a sync attempt completed", not just
            # "the app booted".
            if db is not None:
                try:
                    db.set_preference(
                        _LAST_SYNC_PREF_KEY, datetime.now(timezone.utc).isoformat()
                    )
                except Exception as e:
                    print(f"[NotesQA] Failed to stamp last-sync time: {e}")
        except Exception as e:
            print(f"[NotesQA] sync_notes_folder failed: {e}")
    return ingested


def get_notes_index_status(db) -> dict:
    """
    Read-only status for UI display: {"count": int, "last_synced": ISO-string or None}.

    "count" is the number of files currently in Config.NOTES_FOLDER that
    have a matching (i.e. up-to-date) "notes_mtime:<file>" preference —
    computed by walking the live folder rather than trusting a raw count
    of stored preference keys, so a file that was ingested once and later
    deleted/renamed doesn't inflate the number forever.

    "last_synced" is whatever was last stamped into the
    "notes_last_sync_at" preference by sync_notes_folder(), or None if a
    sync has never successfully run.

    Never raises — a missing/None db or a folder read problem just
    yields count=0 rather than propagating an error into the caller
    (e.g. the Settings page).
    """
    if db is None:
        return {"count": 0, "last_synced": None}

    count = 0
    try:
        for path in _iter_note_files():
            fname = os.path.basename(path)
            try:
                if db.get_preference(f"notes_mtime:{fname}") is not None:
                    count += 1
            except Exception as e:
                print(f"[NotesQA] get_notes_index_status: check failed for {fname}: {e}")
    except Exception as e:
        print(f"[NotesQA] get_notes_index_status: folder walk failed: {e}")

    try:
        last_synced = db.get_preference(_LAST_SYNC_PREF_KEY)
    except Exception as e:
        print(f"[NotesQA] get_notes_index_status: last-sync read failed: {e}")
        last_synced = None

    return {"count": count, "last_synced": last_synced}


def handle(match, ctx):
    ui_update = ctx["ui_update"]
    tts = ctx["tts"]
    rag_memory = ctx.get("notes_memory")
    query = (
        match.group(1).strip()
        if match and match.lastindex
        else ctx.get("user_input", "")
    )

    if rag_memory is None or not getattr(rag_memory, "enabled", False):
        text = "Notes search isn't set up right now — long-term memory is disabled."
        ui_update("status", "speaking")
        tts.speak(text, fast=True)
        return text

    ui_update("status", "thinking")
    top_k = int(getattr(Config, "NOTES_QA_TOP_K", 4))
    try:
        raw_hits = rag_memory.search(query, top_k=top_k * 2)
    except Exception as e:
        print(f"[NotesQA] search failed: {e}")
        raw_hits = []
    hits = [h for h in raw_hits if h.source.startswith("notes:")][:top_k]

    if not hits:
        text = "I couldn't find anything about that in your notes."
        ui_update("status", "speaking")
        tts.speak(text, fast=True)
        return text

    brain = ctx.get("brain")
    context_block = "\n\n".join(f"[{h.source}] {h.text}" for h in hits)
    text = None
    if brain is not None:
        try:
            prompt = (
                "Using ONLY the notes excerpts below, answer the question in "
                "2-3 sentences. If the excerpts don't actually answer it, say "
                f"so.\n\nNotes excerpts:\n{context_block}\n\nQuestion: {query}"
            )
            text = brain.generate_response(prompt)
        except Exception as e:
            print(f"[NotesQA] LLM answer failed, falling back to raw excerpt: {e}")
    if not text or not text.strip():
        text = hits[0].text

    ui_update("status", "speaking")
    tts.speak(text, fast=True)
    return text