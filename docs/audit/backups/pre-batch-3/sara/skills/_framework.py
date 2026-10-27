"""
sara.skills._framework
The skill framework: one small, safe, declarative way to write a skill.

    from ._framework import skill, SkillContext, SkillResult

    @skill(name="check_streak", patterns=[r"my streak"], gate=("streak",),
           category="social", examples=("what's my streak",), timeout=None)
    def handle(match, ctx: SkillContext):
        ctx.status("thinking")
        return SkillResult(text=ctx.t("Five days!", "Paanch din!"),
                           card={...}, chips=["Tell me a joke"])

What the framework does FOR the skill (so it never repeats this boilerplate):
  * builds a SkillContext (crash-proof ctx.say / ctx.status / ctx.pref, the
    reply language, cancel awareness) around the dispatcher's raw ctx dict;
  * enforces `requires=("notes_memory", ...)` with a friendly reply;
  * runs the handler with an optional watchdog `timeout=` (in a helper
    thread; a handler that overruns is abandoned, its late speech is
    dropped, and SkillTimeout is raised so sara/orchestrator/dispatcher.py
    speaks its standard error line and writes the action_log "fail" row);
  * lets unexpected exceptions propagate (after logging) so the dispatcher's
    existing error boundary + action_log audit stays accurate;
  * speaks the result (optionally as several utterances with pauses),
    emits the UI card / suggestion chips, and returns the text;
  * honours the live enable/disable switch (a disabled skill returns None,
    which makes the dispatcher fall through to normal chat).

Return contract of handle(): SkillResult | str | None
    None        -> "not for me", dispatcher falls through (logged "skipped")
    str         -> spoken + returned as-is
    SkillResult -> see the dataclass below

UI events (all optional, best-effort, never raise):
    ui_update("skill_card",  <skill name>, <card dict>)
    ui_update("skill_chips", <skill name>, <list of chip strings>)
Rendering them needs the matching front-end hook (see frontend/skill_cards.js);
without it the events are simply ignored. Disable with Config.SKILL_UI_EVENTS=False.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("sara.skills")

__all__ = ["skill", "SkillContext", "SkillResult", "SkillSpec", "SkillTimeout", "detect_lang"]


class SkillTimeout(Exception):
    """Raised when a skill's handler exceeded its watchdog timeout."""


# ── language ───────────────────────────────────────────────────────────────

_HINGLISH_MARKERS = frozenset({
    "kya", "kaise", "kaisa", "kaisi", "hai", "hain", "batao", "bata", "sunao",
    "karo", "mera", "meri", "mere", "mujhe", "aaj", "abhi", "tak", "tumne",
    "aap", "aur", "dikhao", "bolo", "chalao", "theek", "thik", "nahi", "haan",
    "kitna", "kitne", "hasao", "hasaao", "mein", "ka", "ke", "ek", "lo",
    "kiya", "hua", "hue", "din", "baare", "bare", "kaam", "rahi", "raha",
    "banao", "dena",
})


def detect_lang(text: str) -> str:
    """'hinglish' if the utterance has Hinglish/Devanagari words, else 'en'."""
    if not text:
        return "en"
    if re.search(r"[\u0900-\u097F]", text):
        return "hinglish"
    tokens = re.findall(r"[a-z']+", text.lower())
    return "hinglish" if any(t in _HINGLISH_MARKERS for t in tokens) else "en"


# ── result / spec ──────────────────────────────────────────────────────────

@dataclass
class SkillResult:
    """
    text      spoken text (short, voice-friendly); also the history text.
    display   optional longer on-screen text; returned to the dispatcher
              instead of `text` (so it is what the chat transcript shows).
    segments  optional [(utterance, pause_after_seconds), ...] spoken in
              order instead of `text` (e.g. joke setup -> pause -> punchline).
    card      optional dict for the GUI, must contain "type".
    chips     optional follow-up suggestions (tap = same as saying it).
    speak     False if the handler already spoke (progressive speech).
    """
    text: str
    display: Optional[str] = None
    segments: Optional[list] = None
    card: Optional[dict] = None
    chips: list = field(default_factory=list)
    speak: bool = True


@dataclass
class SkillSpec:
    name: str
    patterns: list
    handler: Callable
    gate: Optional[tuple] = None
    description: str = ""
    category: str = "general"
    examples: tuple = ()
    timeout: Optional[float] = None
    requires: tuple = ()
    version: str = "1.0"
    module: str = ""          # short module name, e.g. "streak"
    legacy: bool = False      # plain handle(match, raw_ctx) module (no SkillContext)


_REGISTRY: list = []
_REGISTRY_LOCK = threading.Lock()


def skill(
    name: str,
    patterns,
    *,
    gate=None,
    description: str = "",
    category: str = "general",
    examples=(),
    timeout: Optional[float] = None,
    requires=(),
    version: str = "1.0",
):
    """Declares a skill. Several @skill functions may live in one module."""
    def decorator(fn):
        module = (getattr(fn, "__module__", "") or "").rsplit(".", 1)[-1]
        spec = SkillSpec(
            name=name, patterns=list(patterns), handler=fn,
            gate=tuple(gate) if gate else None, description=description,
            category=category, examples=tuple(examples), timeout=timeout,
            requires=tuple(requires), version=version, module=module,
        )
        with _REGISTRY_LOCK:
            _REGISTRY[:] = [s for s in _REGISTRY if not (s.module == module and s.name == name)]
            _REGISTRY.append(spec)
        return fn
    return decorator


def take_specs(module: str) -> list:
    """Loader hook: removes and returns the specs a module registered."""
    with _REGISTRY_LOCK:
        mine = [s for s in _REGISTRY if s.module == module]
        _REGISTRY[:] = [s for s in _REGISTRY if s.module != module]
    return mine


# ── context ────────────────────────────────────────────────────────────────

def _cfg(name: str, default):
    try:
        from config import Config
        return getattr(Config, name, default)
    except Exception:  # noqa: BLE001
        return default


class SkillContext:
    """Crash-proof view of the dispatcher's ctx dict for ONE skill call."""

    def __init__(self, raw: dict, spec_name: str = ""):
        self.raw = raw if isinstance(raw, dict) else {}
        self.skill_name = spec_name
        self._abandoned = False
        self._lang: Optional[str] = None
        self._cancel_event = None
        try:  # captured NOW, in the dispatch thread (may be thread-local)
            from sara.orchestrator.state import TURN_STATE
            self._cancel_event = TURN_STATE.current_event()
        except Exception:  # noqa: BLE001
            self._cancel_event = None

    # -- raw access (backward compatible with dict-style skills) --
    def get(self, key, default=None):
        return self.raw.get(key, default)

    def __getitem__(self, key):
        return self.raw[key]

    db = property(lambda self: self.raw.get("db"))
    brain = property(lambda self: self.raw.get("brain"))
    tts = property(lambda self: self.raw.get("tts"))
    ears = property(lambda self: self.raw.get("ears"))
    reminders = property(lambda self: self.raw.get("reminders"))
    notes_memory = property(lambda self: self.raw.get("notes_memory"))
    user_input = property(lambda self: self.raw.get("user_input") or "")

    # -- language --
    @property
    def lang(self) -> str:
        """'en' or 'hinglish'. Follows the Settings language toggle
        (preference 'language_mode': en / hi / auto); in auto mode it is
        detected from the utterance."""
        if self._lang is None:
            mode = (self.pref("language_mode") or "auto").lower()
            if mode == "hi":
                self._lang = "hinglish"
            elif mode == "en":
                self._lang = "en"
            else:
                self._lang = detect_lang(self.user_input)
        return self._lang

    def t(self, en: str, hinglish: Optional[str] = None) -> str:
        return hinglish if (self.lang == "hinglish" and hinglish) else en

    # -- preferences (str only, never raise) --
    def pref(self, key: str, default: Optional[str] = None) -> Optional[str]:
        db = self.db
        if db is None or not hasattr(db, "get_preference"):
            return default
        try:
            value = db.get_preference(key)
            return default if value is None else value
        except Exception:  # noqa: BLE001
            logger.debug("get_preference(%r) failed", key, exc_info=True)
            return default

    def set_pref(self, key: str, value: str) -> bool:
        db = self.db
        if db is None or not hasattr(db, "set_preference"):
            return False
        try:
            db.set_preference(key, str(value))
            return True
        except Exception:  # noqa: BLE001
            logger.debug("set_preference(%r) failed", key, exc_info=True)
            return False

    def user_name(self) -> Optional[str]:
        db = self.db
        try:
            return db.get_user_name() if db is not None and hasattr(db, "get_user_name") else None
        except Exception:  # noqa: BLE001
            return None

    # -- cancel / abandon --
    @property
    def cancelled(self) -> bool:
        ev = self._cancel_event
        try:
            return bool(ev is not None and ev.is_set())
        except Exception:  # noqa: BLE001
            return False

    @property
    def abandoned(self) -> bool:
        return self._abandoned

    # -- output --
    def status(self, state: str) -> None:
        if self._abandoned:
            return
        self.emit("status", state)

    def emit(self, kind: str, *args) -> bool:
        fn = self.raw.get("ui_update")
        if not fn:
            return False
        try:
            fn(kind, *args)
            return True
        except Exception:  # noqa: BLE001 -- UI problems must never crash a skill
            logger.debug("ui_update(%r) failed", kind, exc_info=True)
            return False

    def say(self, text: str, pause_after: float = 0.0) -> bool:
        """Speaks `text` now (blocking, like tts.speak). False if it couldn't."""
        if self._abandoned or not text:
            return False
        tts = self.tts
        if tts is None:
            logger.debug("No 'tts' in ctx; skipping speech.")
            return False
        try:
            tts.speak(text, fast=True)
        except Exception as e:  # noqa: BLE001 -- e.g. audio device error
            logger.error("TTS speak failed: %s", e)
            return False
        if pause_after > 0:
            end = time.monotonic() + min(pause_after, 3.0)
            while time.monotonic() < end and not self._abandoned and not self.cancelled:
                time.sleep(0.05)
        return True

    def ack(self, en: str, hinglish: Optional[str] = None) -> None:
        """Short 'one second...' phrase for slow skills (non-blocking if the
        TTS supports block=False)."""
        text = self.t(en, hinglish)
        tts = self.tts
        if tts is None or self._abandoned:
            return
        try:
            try:
                tts.speak(text, fast=True, block=False)
            except TypeError:
                tts.speak(text, fast=True)
        except Exception:  # noqa: BLE001
            logger.debug("ack speak failed", exc_info=True)

    def card(self, payload: dict) -> None:
        if self._abandoned or not payload or not _cfg("SKILL_UI_EVENTS", True):
            return
        self.emit("skill_card", self.skill_name, payload)

    def chips(self, chips) -> None:
        if self._abandoned or not chips or not _cfg("SKILL_UI_EVENTS", True):
            return
        self.emit("skill_chips", self.skill_name, list(chips))


# ── running a handler ──────────────────────────────────────────────────────

def _run_with_timeout(fn: Callable, timeout: float, ctx: SkillContext, name: str):
    box: dict = {}

    def target():
        try:
            box["result"] = fn()
        except BaseException as e:  # noqa: BLE001 -- re-raised in caller thread
            box["error"] = e

    worker = threading.Thread(target=target, name=f"skill-{name}", daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        worker.join(0.1)
        if ctx.cancelled:
            ctx._abandoned = True
            return None
        if time.monotonic() >= deadline:
            ctx._abandoned = True
            raise SkillTimeout(f"skill '{name}' exceeded {timeout:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _finalize(ctx: SkillContext, out) -> Optional[str]:
    if out is None:
        return None
    if isinstance(out, str):
        ctx.status("speaking")
        ctx.say(out)
        return out
    if not isinstance(out, SkillResult):
        logger.warning("skill '%s' returned unsupported type %s", ctx.skill_name, type(out).__name__)
        out = SkillResult(text=str(out))

    if out.card:
        ctx.card(out.card)
    if out.chips:
        ctx.chips(out.chips)
    if out.speak:
        ctx.status("speaking")
        if out.segments:
            for utterance, pause in out.segments:
                if ctx.cancelled:
                    break
                ctx.say(utterance, pause)
        else:
            ctx.say(out.text)
    return out.display or out.text


def build_adapter(spec: SkillSpec, enabled_check: Callable[[str], bool]) -> Callable:
    """Returns the fn(match, raw_ctx) that is registered with
    intent_handlers.register_handler()."""

    if spec.legacy:
        def legacy_adapter(match, raw_ctx):
            if not enabled_check(spec.module):
                return None
            return spec.handler(match, raw_ctx)
        legacy_adapter.__name__ = f"skill_{spec.name}"
        return legacy_adapter

    def adapter(match, raw_ctx):
        if not enabled_check(spec.module):
            return None  # disabled in Settings: fall through to normal chat
        ctx = SkillContext(raw_ctx, spec.name)

        for key in spec.requires:
            if raw_ctx.get(key) is None:
                logger.info("skill '%s' unavailable: ctx has no %r", spec.name, key)
                return _finalize(ctx, SkillResult(text=ctx.t(
                    f"That isn't available right now ({key.replace('_', ' ')} is off).",
                    f"Ye abhi available nahi hai ({key.replace('_', ' ')} band hai).",
                )))

        try:
            if spec.timeout:
                out = _run_with_timeout(lambda: spec.handler(match, ctx), spec.timeout, ctx, spec.name)
            else:
                out = spec.handler(match, ctx)
        except SkillTimeout:
            logger.error("skill '%s' timed out after %ss", spec.name, spec.timeout)
            raise
        except Exception:
            logger.exception("skill '%s' raised", spec.name)
            raise  # dispatcher speaks its standard error line + logs "fail"
        if ctx.abandoned:
            return None
        return _finalize(ctx, out)

    adapter.__name__ = f"skill_{spec.name}"
    return adapter