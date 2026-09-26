"""
sara.orchestrator.handlers.media

YouTube/Spotify playback, web search, page summarization, and opening
URLs. Split out of the former monolithic intent_handlers.py -- see
that module's docstring, in particular the SECURITY HARDENING note
covering _h_open_url().
"""
from sara.tools import web as web_tools

from ..network_utils import _call_with_timeout
from ..command_helpers import _quick, _ack, _run_activity
from .._shared_state import _HAS_PLANNING, validate_tool_arguments, PlanValidationError

def _h_play_youtube(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")
    query = match.group(1).strip()
    result = _run_activity(
        ctx, "youtube", "Searching YouTube", "Playing on YouTube", "Couldn't play that",
        lambda: _call_with_timeout(web_tools.play_youtube, query, tool_name="play_youtube"),
    )
    if isinstance(result, str) and result.startswith("Playing"):
        ctx["playback_state"]["youtube"] = {"query": query, "index": 0}
    return _quick(ctx, result)


def _h_play_next_youtube(match, ctx):
    """
    'next video' / 'agla video chalao' follow-up — only makes sense
    right after a play_youtube call, so it needs ctx["playback_state"]
    to know which search to continue.
    """
    state = ctx["playback_state"].get("youtube")
    if not state:
        return _quick(ctx, "I'm not playing anything from YouTube right now.")
    ctx["ui_update"]("status", "thinking")
    result = _run_activity(
        ctx, "youtube", "Loading next video", "Next video playing", "Couldn't load next video",
        lambda: _call_with_timeout(
            web_tools.play_next_youtube,
            state["query"],
            state["index"],
            tool_name="play_next_youtube",
        ),
    )
    if isinstance(result, tuple) and len(result) == 2:
        message, new_index = result
        state["index"] = new_index
    else:
        # _call_with_timeout hit its own timeout/exception path and
        # returned a plain error string instead of our (msg, index) tuple.
        message = result
    return _quick(ctx, message)


def _h_play_spotify(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")
    query = match.group(1).strip()
    return _quick(
        ctx,
        _run_activity(
            ctx, "spotify", "Opening Spotify", "Playing on Spotify", "Couldn't play that",
            lambda: _call_with_timeout(web_tools.play_spotify, query),
        ),
    )


def _h_web_search(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    query = match.group(1)
    return _quick(
        ctx,
        _run_activity(
            ctx, "search", "Searching the web", "Search complete", "Search failed",
            lambda: _call_with_timeout(web_tools.search_web, query, ctx=ctx),
        ),
    )


def _h_summarize_url(match, ctx):
    if not match:
        return None
    ctx["ui_update"]("status", "thinking")

    def _read_and_summarize():
        page_text = _call_with_timeout(web_tools.read_webpage, match.group(1))
        if isinstance(page_text, str) and (
            page_text.startswith("Error:") or page_text.startswith("Sorry,")
        ):
            return page_text
        return ctx["brain"].summarize_text(page_text)

    return _quick(
        ctx,
        _run_activity(
            ctx, "link", "Reading page", "Summary ready", "Couldn't read that page",
            _read_and_summarize,
        ),
    )


def _h_open_url(match, ctx):
    """
    Opens a URL captured by the open_url fast-path regex (or resolved by
    the LLM single-tool router, or proposed by a multi-step plan -- all
    three converge here).

    SECURITY HARDENING: the captured URL is validated via
    sara.core.planning.schema.validate_tool_arguments() before being
    passed to web_tools.open_url() -- only http:// and https:// schemes
    are ever allowed through; javascript:/data:/file:/vbscript:/etc. are
    rejected with a clear spoken message instead of being opened. If
    sara.core.planning failed to import (_HAS_PLANNING is False), this
    degrades to the original unvalidated behavior rather than crashing
    -- matching this codebase's "optional feature missing must never
    break the app" convention.
    """
    if not match:
        return None
    raw_url = match.group(1)
    if _HAS_PLANNING and validate_tool_arguments is not None:
        try:
            validated = validate_tool_arguments("open_url", {"url": raw_url})
            raw_url = validated["url"]
        except PlanValidationError as e:
            return _quick(ctx, str(e))
        except Exception as e:  # noqa: BLE001 -- validation must never crash the handler
            print(f"[Security] open_url validation raised unexpectedly: {e}")
            return _quick(ctx, "Sorry, I couldn't safely open that link.")
    _ack(ctx)
    return _quick(
        ctx,
        _run_activity(
            ctx, "link", "Opening link", "Link opened", "Couldn't open link",
            lambda: web_tools.open_url(raw_url),
        ),
    )

