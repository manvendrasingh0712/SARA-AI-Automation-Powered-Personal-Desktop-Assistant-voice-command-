"""
sara.orchestrator._shared_state

Optional/additive import guards (long-term memory RAG, the LLM
tool-router, and the multi-step planning engine) plus the small set of
module-level constants and the logger that many of the other
sara.orchestrator modules (split out of the former monolithic
intent_handlers.py) need. See intent_handlers.py's module docstring for
the full rationale behind each of these.
"""
import logging

# Same latency fix as sara/tools/reminders.py's add_reminder(): restrict
# dateparser to only the languages Sara actually needs (en/hi), instead
# of letting it try dozens of locales.
_CALENDAR_DATEPARSER_LANGUAGES = ["en", "hi"]
_DEFAULT_MEETING_DURATION_MINUTES = 30

# PRODUCTION-AUDIT ADDITION (Phase 2): long-term memory (RAG) and the
# LLM tool-calling fallback are both optional, additive features — if
# either module fails to import for any reason (e.g. numpy missing),
# the whole app must still start exactly as before, just without that
# one feature. Both are re-checked as None/False below wherever used.
try:
    from sara.core.rag import LongTermMemory

    _HAS_RAG = True
except Exception as _rag_import_err:  # noqa: BLE001
    LongTermMemory = None
    _HAS_RAG = False
    print(
        f"[Core] sara.core.rag unavailable, long-term memory disabled: {_rag_import_err}"
    )

try:
    from sara.core.tool_router import (
        resolve_tool_call,
        build_fake_match,
        TOOL_NAME_TO_INTENT,
    )

    _HAS_TOOL_ROUTER = True
except Exception as _tool_router_import_err:  # noqa: BLE001
    resolve_tool_call = None
    build_fake_match = None
    TOOL_NAME_TO_INTENT = {}
    _HAS_TOOL_ROUTER = False
    print(
        f"[Core] sara.core.tool_router unavailable, LLM tool-calling fallback "
        f"disabled: {_tool_router_import_err}"
    )

# NEW: multi-step planning engine (sara/core/planning/) -- optional,
# additive, same "never break startup" contract as RAG/tool_router
# above. If this fails to import for any reason, _handle_command() below
# simply never attempts a plan and behaves exactly as it did before this
# feature existed.
try:
    from sara.core.planning import try_plan_and_execute
    from sara.core.planning.schema import (
        PlanValidationError,
        validate_tool_arguments,
    )
    from sara.core.planning.executor import PlanStepRequiresConfirmation

    # LATENCY FIX: the plan-signal detector is now needed HERE, up front, by
    # _route_chat_message() below -- it is what decides (without any LLM
    # call) whether this turn takes the planner path or the tool-router
    # path. It used to be called only from inside try_plan_and_execute().
    # Imported defensively: older builds of sara.core.planning may not
    # re-export it from the package root, in which case the router simply
    # never picks the "plan" route and behaves like planning is disabled.
    try:
        from sara.core.planning import should_attempt_plan
    except Exception:  # noqa: BLE001
        try:
            from sara.core.planning.trigger import should_attempt_plan
        except Exception:  # noqa: BLE001
            should_attempt_plan = None

    _HAS_PLANNING = True
except Exception as _planning_import_err:  # noqa: BLE001
    try_plan_and_execute = None
    validate_tool_arguments = None
    should_attempt_plan = None

    class PlanValidationError(Exception):  # type: ignore[no-redef]
        """Sentinel fallback so isinstance/except checks below never crash
        when sara.core.planning failed to import."""

    PlanStepRequiresConfirmation = None

    _HAS_PLANNING = False
    print(
        f"[Core] sara.core.planning unavailable, multi-step planning "
        f"disabled (single-tool routing is unaffected): {_planning_import_err}"
    )
    

logger = logging.getLogger("sara.core_logic")

# STOP-WORD FIX: bare "stop" used to be in this set. _matches_phrase_set()
# does prefix/suffix word matching, not exact matching, so ANY sentence
# starting or ending with the single word "stop" matched -- "stop the
# music", "stop timer", "stop recording" all silently exited the entire
# app instead of doing what they said. Removed; "exit"/"quit"/"shutdown"/
# "goodbye"/etc. are still exact/prefix/suffix matches for actually
# quitting Sara.
_EXIT_WORDS = {
    "exit",
    "quit",
    "goodbye",
    "bye",
    "shutdown",
    "band karo",
    "band kar",
    "alvida",
    "phir milenge",
    "bye bye",
    "बंद करो",
    "अलविदा",
}
# NOTE: stays local, NOT in ._constants -- this shard's value conflicts
# with another shard's copy (that one still has "stop"; this one
# deliberately doesn't, see STOP-WORD FIX above). See CONFLICTS.

# CONFIRMATION FLOW: closing/stopping something "risky" (a core system
# process/service, not an everyday app) asks for a yes/no first instead
# of just doing it. Matched as a case-insensitive substring against the
# app/service name, so e.g. "explorer" also catches "explorer.exe".
# Tune these lists as needed -- err on the side of adding, not removing.
_RISKY_APP_KEYWORDS = (
    "explorer", "taskmgr", "task manager", "cmd", "command prompt",
    "powershell", "terminal", "regedit", "registry", "services.msc",
    "control panel", "defender", "antivirus", "firewall", "vpn",
    "svchost", "winlogon", "csrss", "system32",
)
_RISKY_SERVICE_KEYWORDS = (
    "defend", "wuau", "dns", "dhcp", "eventlog", "rpcss", "winmgmt",
    "netlogon", "lanman", "cryptsvc", "bits", "schedule", "power",
    "audiosrv", "spooler", "themes",
)

_CONFIRM_YES_WORDS = {
    "yes", "yeah", "yep", "confirm", "sure", "go ahead", "do it",
    "haan", "ha", "kar do", "kardo", "theek hai", "ok", "okay",
}
_CONFIRM_NO_WORDS = {
    "no", "nope", "cancel", "abort", "never mind", "nevermind",
    "nahi", "mat karo", "rehne do", "chhodo",
}
_CONFIRM_PENDING_TTL_S = 30.0
