"""
sara.core.planning.schema
Data types, parsing, and per-tool argument validation for the multi-step
planner.

This module is the single source of truth for "what does a valid plan
step look like." Both the planner (turning a raw LLM tool-call payload
into steps) and the executor (validating arguments immediately before
every dispatch attempt, including corrected arguments produced mid-run)
import from here -- a step can never reach a real tool function without
passing through the exact same checks, regardless of which code path
built it.

SECURITY HARDENING (open_url / open_app / close_app)
------------------------------------------------------
validate_url() and validate_app_target() hold the hardening required
for the two most sensitive tool surfaces in this codebase:
sara.tools.web.open_url() and sara.tools.system.open_application()/
close_application() have no scheme or allowlist check beyond the
fast-path regex capture itself. Two call sites enforce these checks:

  1. sara.core.planning.executor -- every open_url/open_app/close_app
     step a multi-step plan produces is validated twice: once when the
     plan is first parsed (parse_plan_from_llm), and again immediately
     before every dispatch attempt inside the execution loop
     (defense-in-depth, in case a Plan is ever constructed by something
     other than the parser, and to cover corrected arguments produced
     by the self-correction retry path).

  2. sara.orchestrator.intent_handlers._h_open_url() / _h_open_app() /
     _h_close_app() -- the EXISTING single-tool fast path also runs
     through validate_tool_arguments() before calling the real tool
     function, so the hardening covers both surfaces uniformly.

URL scheme allowlist (http/https only) is a hard-coded security
invariant, not a user-configurable setting -- there is no legitimate
reason to ever let "javascript:", "data:", "file:", "vbscript:", or any
other scheme reach a browser-open call sourced from voice input. The
application allowlist, by contrast, IS configurable
(Config.APP_LAUNCH_ALLOWLIST / APP_LAUNCH_ALLOWLIST_ENABLED) since a
legitimate allowlist is inherently installation-specific.

PERFORMANCE NOTES
-------------------
All regexes used here are compiled once at import time. validate_url()
and validate_app_target() are pure, allocation-light functions with no
I/O -- safe to call on every single tool-argument resolution (fast path
and planning path alike) with no measurable latency cost.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger("sara.core.planning.schema")


class PlanValidationError(Exception):
    """
    Raised whenever a proposed plan, a single step, or a tool argument
    fails structural or security validation. Every caller in this
    package treats this the same way as any other "this attempt didn't
    work" signal -- it is always caught and degrades gracefully (drop
    the offending step, decline the whole plan, or fall back to the
    existing single-tool path), never allowed to propagate into the
    voice loop.
    """


class StepStatus(str, Enum):
    """Terminal status of one executed (or skipped) plan step."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PlanStep:
    """
    One validated, ready-to-dispatch step in a plan.

    `tool` is always a key that exists in
    sara.core.tool_router.TOOL_NAME_TO_INTENT -- parse_plan_from_llm()
    below is the only place a PlanStep is constructed from untrusted LLM
    output, and it never lets an unknown tool name through.

    `arguments` have already passed validate_tool_arguments() at the
    time this object was constructed. The executor re-validates them
    again immediately before every dispatch attempt regardless (defense
    in depth) -- see module docstring.

    Immutable (frozen) so a step can never be mutated in place after
    validation -- any "correction" during execution produces a NEW
    PlanStep-shaped arguments dict that itself passes back through
    validate_tool_arguments(), rather than patching an existing step.
    """

    tool: str
    arguments: Dict[str, Any]
    depends_on_previous: bool = False


@dataclass(frozen=True)
class Plan:
    """
    An ordered, bounded sequence of validated PlanStep objects.

    `proposed_step_count` records how many raw steps the LLM originally
    proposed before validation/truncation -- kept purely for logging and
    observability (e.g. "proposed 6 steps, 4 survived validation").
    """

    steps: Tuple[PlanStep, ...]
    proposed_step_count: int


@dataclass(frozen=True)
class StepResult:
    """The outcome of attempting (or skipping) one PlanStep."""

    step: PlanStep
    status: StepStatus
    output: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0


@dataclass(frozen=True)
class PlanOutcome:
    """
    The full result of executing a Plan, returned by
    sara.core.planning.executor.execute_plan() and, ultimately, by
    sara.core.planning.try_plan_and_execute().

    `final_message` is the ready-to-speak natural-language summary of
    what happened across every step (partial successes and failures
    included) -- callers should generally just speak this directly
    rather than re-deriving their own summary from `results`.
    """

    results: Tuple[StepResult, ...]
    aborted: bool
    abort_reason: Optional[str]
    elapsed_s: float
    final_message: str


# ══════════════════════════════════════════════════════════════════════
# URL validation
# ══════════════════════════════════════════════════════════════════════

_ALLOWED_URL_SCHEMES: FrozenSet[str] = frozenset({"http", "https"})

# Matches a leading "<scheme>:" even across leading whitespace/control
# characters, so a value like "  javascript:alert(1)" is still caught --
# urlsplit() alone can be lenient about what it calls a "scheme" when the
# input is malformed, so this regex is the first, stricter line of
# defense before urlsplit() is even consulted.
_LEADING_SCHEME_RE = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9+.\-]*)\s*:", re.IGNORECASE)

# Explicit denylist of schemes that are unambiguously dangerous in a
# "voice assistant opens this in a browser" context. Checked in ADDITION
# to the allowlist above (belt-and-suspenders: even if a future edit
# accidentally widens _ALLOWED_URL_SCHEMES, these specific schemes are
# still caught by name here first).
_DANGEROUS_URL_SCHEMES: FrozenSet[str] = frozenset(
    {
        "javascript",
        "data",
        "file",
        "vbscript",
        "about",
        "blob",
        "filesystem",
        "chrome",
        "chrome-extension",
        "view-source",
        "jar",
        "ms-appx",
        "ms-appx-web",
    }
)

# Control characters (including the ones commonly used to smuggle a
# scheme past naive checks, e.g. tab/newline inside "java\tscript:")
# are stripped before any scheme detection runs.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# Detects percent-encoded characters so an encoded scheme like
# "java%73cript:" can be decoded and re-checked rather than slipping
# through as an opaque path segment.
_PERCENT_ENCODED_RE = re.compile(r"%[0-9a-fA-F]{2}")


def _strip_control_and_normalize(raw: str) -> str:
    """
    Removes control/formatting characters and applies Unicode NFKC
    normalization so visually-similar or zero-width characters cannot be
    used to smuggle a dangerous scheme past the regex-based checks below
    (e.g. a zero-width space inserted into "javascript:").
    """
    normalized = unicodedata.normalize("NFKC", raw)
    return _CONTROL_CHARS_RE.sub("", normalized)


def validate_url(url: Any) -> str:
    """
    Validates a URL argument destined for the open_url tool.

    Returns the (possibly scheme-completed) URL string on success.
    Raises PlanValidationError with a clear, user-safe message if the
    URL is empty, uses a disallowed/dangerous scheme, or has no
    discernible host.

    Only "http" and "https" (case-insensitive) are ever permitted.
    Values with no explicit scheme at all (e.g. "example.com", matching
    how sara/core/intent/patterns.py's own open_url regex already
    accepts bare hostnames) are defaulted to "https://" rather than
    rejected, to preserve today's existing "open <site>" phrasing.

    Hardening applied, in order:
      1. Control characters stripped, Unicode NFKC-normalized.
      2. Percent-encoded scheme smuggling detected and rejected outright
         (e.g. "java%73cript:alert(1)") -- this content is refused
         rather than decoded-and-retried, since a legitimate http(s)
         URL never needs an encoded scheme separator.
      3. Explicit denylist check against known-dangerous schemes.
      4. Allowlist check (http/https only).
      5. Host presence/sanity check via urlsplit().
    """
    if not isinstance(url, str) or not url.strip():
        raise PlanValidationError("URL argument is empty.")

    cleaned = _strip_control_and_normalize(url.strip())

    if _PERCENT_ENCODED_RE.search(cleaned.split("://", 1)[0] if "://" in cleaned else cleaned[:20]):
        # A percent-encoded sequence appearing before/around where a
        # scheme separator would be is treated as a smuggling attempt --
        # legitimate http(s) URLs never need to percent-encode their own
        # scheme. Reject outright rather than trying to decode safely.
        decoded_prefix = cleaned[:32]
        logger.warning(
            "Blocked open_url request with percent-encoded prefix (possible "
            "scheme-smuggling attempt): %r",
            decoded_prefix,
        )
        raise PlanValidationError(
            "Refusing to open that link -- it contains suspicious encoded "
            "characters near the start of the address."
        )

    scheme_match = _LEADING_SCHEME_RE.match(cleaned)
    if not scheme_match:
        candidate = f"https://{cleaned}"
    else:
        candidate = cleaned
        detected_scheme = scheme_match.group(1).lower()
        if detected_scheme in _DANGEROUS_URL_SCHEMES:
            logger.warning(
                "Blocked open_url request with denylisted scheme '%s://' "
                "(original input: %r).",
                detected_scheme,
                url,
            )
            raise PlanValidationError(
                f"Refusing to open a '{detected_scheme}://' link -- only "
                f"http:// and https:// links are allowed."
            )

    parsed = urlsplit(candidate)
    scheme = parsed.scheme.lower()

    if scheme not in _ALLOWED_URL_SCHEMES:
        logger.warning(
            "Blocked open_url request with disallowed scheme '%s://' "
            "(original input: %r).",
            scheme,
            url,
        )
        raise PlanValidationError(
            f"Refusing to open a '{scheme}://' link -- only http:// and "
            f"https:// links are allowed."
        )

    if not parsed.netloc or any(ch.isspace() for ch in parsed.netloc):
        raise PlanValidationError(f"'{url}' doesn't look like a valid website address.")

    if "@" in parsed.netloc:
        # Reject userinfo@host URLs outright -- a common phishing/
        # confusion vector ("https://accounts.google.com@evil.example")
        # that a voice-triggered open should never need.
        logger.warning(
            "Blocked open_url request containing userinfo in netloc: %r", url
        )
        raise PlanValidationError(
            f"'{url}' looks suspicious (contains an '@' in the address) -- "
            f"refusing to open it."
        )

    return candidate


# ══════════════════════════════════════════════════════════════════════
# Application-target validation
# ══════════════════════════════════════════════════════════════════════

_WHITESPACE_COLLAPSE_RE = re.compile(r"\s+")

# Caps how long a raw app-name argument is allowed to be before
# validation even attempts a match -- guards against a pathological
# multi-hundred-character "target" (e.g. injected garbage) being run
# through substring matching against every allowlist entry.
_MAX_APP_TARGET_LENGTH = 80


def validate_app_target(
    target: Any,
    allowed_apps: FrozenSet[str],
    *,
    enabled: bool = True,
) -> str:
    """
    Validates an application-name argument destined for the open_app (or
    close_app) tool against an allowlist of known application names/
    aliases.

    Returns the normalized (stripped, lowercased, whitespace-collapsed)
    target name on success. Raises PlanValidationError with a clear
    message if the target is empty, too long, or -- when `enabled` is
    True -- if it doesn't match any entry in `allowed_apps` (matched by
    exact equality or substring in either direction, so "google chrome"
    and "chrome browser" both match an allowlist entry of "chrome").

    `enabled=False` (Config.APP_LAUNCH_ALLOWLIST_ENABLED) skips the
    allowlist check entirely and only enforces the non-empty/length
    checks -- an explicit installation-level opt-out, not a silent
    bypass.

    An enabled allowlist that is empty is treated as "nothing can be
    validated" and always raises -- this is deliberately the safer
    failure direction (nothing permitted) rather than silently
    degrading to "everything permitted" if a config typo empties the
    list out.
    """
    if not isinstance(target, str) or not target.strip():
        raise PlanValidationError("Application name argument is empty.")

    if len(target) > _MAX_APP_TARGET_LENGTH:
        raise PlanValidationError(
            f"Application name is too long ({len(target)} characters, max "
            f"{_MAX_APP_TARGET_LENGTH})."
        )

    normalized = _WHITESPACE_COLLAPSE_RE.sub(" ", target.strip().lower())

    if not enabled:
        return normalized

    if not allowed_apps:
        raise PlanValidationError(
            "The application allowlist is enabled but empty, so no "
            "application launch can be validated. Configure "
            "APP_LAUNCH_ALLOWLIST in .env, or set "
            "APP_LAUNCH_ALLOWLIST_ENABLED=false to disable enforcement."
        )

    for alias in allowed_apps:
        if not alias:
            continue
        if alias == normalized or alias in normalized or normalized in alias:
            return normalized

    logger.info("Blocked app-launch request for unlisted target %r.", target)
    raise PlanValidationError(
        f"'{target}' isn't in the list of allowed applications. Add it to "
        f"APP_LAUNCH_ALLOWLIST if this should be permitted."
    )


# ══════════════════════════════════════════════════════════════════════
# Per-tool argument validation dispatcher
# ══════════════════════════════════════════════════════════════════════

# Tools whose arguments need the security hardening above. Every other
# tool's arguments pass through unchanged (validate_tool_arguments() is
# still the single place that decides this, so adding a future sensitive
# tool only means adding one branch here, not touching every call site).
_URL_ARG_TOOLS: FrozenSet[str] = frozenset({"open_url"})
_APP_ARG_TOOLS: FrozenSet[str] = frozenset({"open_app", "close_app"})


def validate_tool_arguments(
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    allowed_apps: FrozenSet[str] = frozenset(),
    app_allowlist_enabled: bool = True,
) -> Dict[str, Any]:
    """
    Validates (and, where needed, normalizes) the arguments for one tool
    call, dispatching to validate_url()/validate_app_target() for the
    tools that need it. Returns a NEW dict (the input is never mutated)
    with any validated/normalized values substituted in.

    Tools with no special validation requirement pass through unchanged
    (a shallow copy is still returned, preserving the "never mutate the
    caller's dict" guarantee uniformly).

    Raises PlanValidationError on any violation -- callers are expected
    to catch this and either drop the offending step or fail it clearly,
    never to suppress it silently.
    """
    if not isinstance(arguments, dict):
        raise PlanValidationError(
            f"Arguments for tool '{tool_name}' must be an object, got "
            f"{type(arguments).__name__}."
        )

    if tool_name in _URL_ARG_TOOLS:
        validated = dict(arguments)
        validated["url"] = validate_url(arguments.get("url", ""))
        return validated

    if tool_name in _APP_ARG_TOOLS:
        validated = dict(arguments)
        validated["target"] = validate_app_target(
            arguments.get("target", ""),
            allowed_apps,
            enabled=app_allowlist_enabled,
        )
        return validated

    return dict(arguments)


# ══════════════════════════════════════════════════════════════════════
# Plan parsing
# ══════════════════════════════════════════════════════════════════════


def parse_plan_from_llm(
    raw_steps: Any,
    *,
    allowed_tools: FrozenSet[str],
    max_steps: int,
    allowed_apps: FrozenSet[str] = frozenset(),
    app_allowlist_enabled: bool = True,
) -> Plan:
    """
    Strictly parses and validates the raw `steps` payload an LLM
    proposed for the `propose_plan` tool call into a Plan of trusted
    PlanStep objects.

    This function NEVER trusts the model:
      - `raw_steps` must be a non-empty list, or this raises.
      - Each item must be a dict with a string `tool` that is a member
        of `allowed_tools` (i.e. a real, known tool name) -- anything
        else (wrong type, hallucinated tool name, missing key) is
        dropped with a logged warning, not raised, so one bad step
        doesn't sink an otherwise-valid plan.
      - `arguments` must be a dict (or absent/None, treated as {}) --
        anything else drops that step.
      - Each surviving step's arguments are run through
        validate_tool_arguments() -- a step that fails this (e.g. a
        disallowed URL scheme or an unlisted app) is ALSO dropped with a
        logged warning, never silently "fixed" or passed through.
      - Duplicate consecutive identical steps (same tool + same
        arguments) are collapsed to one -- guards against a hallucinating
        model proposing the same action twice in a row, which would
        otherwise burn plan-length budget and execute a redundant
        real-world side effect (e.g. two identical reminders).
      - The list is truncated to `max_steps` (a hard, config-driven
        cap), with a logged warning if truncation actually happened.

    Raises PlanValidationError if, after all of the above, zero valid
    steps remain -- callers treat that exactly like any other planning
    failure (fall back to the existing single-tool path).
    """
    if not isinstance(raw_steps, list):
        raise PlanValidationError(
            f"Plan proposal was not a list of steps (got {type(raw_steps).__name__})."
        )
    if not raw_steps:
        raise PlanValidationError("Plan proposal contained zero steps.")

    if max_steps < 1:
        raise PlanValidationError(f"max_steps must be >= 1, got {max_steps}.")

    valid_steps: List[PlanStep] = []
    dropped_count = 0
    last_signature: Optional[Tuple[str, Tuple[Tuple[str, Any], ...]]] = None

    for index, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            logger.warning("Dropping plan step %d: not an object (%r).", index, item)
            dropped_count += 1
            continue

        tool_name = item.get("tool")
        if not isinstance(tool_name, str) or tool_name not in allowed_tools:
            logger.warning(
                "Dropping plan step %d: unknown or invalid tool name %r.",
                index,
                tool_name,
            )
            dropped_count += 1
            continue

        raw_arguments = item.get("arguments")
        if raw_arguments is None:
            raw_arguments = {}
        if not isinstance(raw_arguments, dict):
            logger.warning(
                "Dropping plan step %d ('%s'): arguments were not an object (%r).",
                index,
                tool_name,
                raw_arguments,
            )
            dropped_count += 1
            continue

        depends_on_previous = bool(item.get("depends_on_previous", False))

        try:
            validated_arguments = validate_tool_arguments(
                tool_name,
                raw_arguments,
                allowed_apps=allowed_apps,
                app_allowlist_enabled=app_allowlist_enabled,
            )
        except PlanValidationError as exc:
            logger.warning(
                "Dropping plan step %d ('%s'): %s", index, tool_name, exc
            )
            dropped_count += 1
            continue

        try:
            signature = (
                tool_name,
                tuple(sorted(validated_arguments.items(), key=lambda kv: kv[0])),
            )
        except TypeError:
            # Arguments contain an unhashable/unsortable value (nested
            # dict/list) -- skip dedup for this step rather than failing
            # the whole parse over a cosmetic optimization.
            signature = None

        if signature is not None and signature == last_signature:
            logger.info(
                "Dropping plan step %d ('%s'): identical to the previous step "
                "(likely duplicate proposal).",
                index,
                tool_name,
            )
            dropped_count += 1
            continue

        valid_steps.append(
            PlanStep(
                tool=tool_name,
                arguments=validated_arguments,
                depends_on_previous=depends_on_previous,
            )
        )
        last_signature = signature

        if len(valid_steps) >= max_steps:
            remaining = len(raw_steps) - (index + 1)
            if remaining > 0:
                logger.info(
                    "Plan truncated at %d step(s) (max_steps=%d); %d further "
                    "proposed step(s) ignored.",
                    max_steps,
                    max_steps,
                    remaining,
                )
            break

    if not valid_steps:
        raise PlanValidationError(
            f"No valid steps remained after validation ({dropped_count} dropped "
            f"of {len(raw_steps)} proposed)."
        )

    if dropped_count:
        logger.info(
            "Plan parsed with %d valid step(s), %d dropped during validation "
            "(of %d proposed).",
            len(valid_steps),
            dropped_count,
            len(raw_steps),
        )

    return Plan(steps=tuple(valid_steps), proposed_step_count=len(raw_steps))