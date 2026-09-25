"""
sara.orchestrator.handlers.notes

Notes and to-dos. Split out of the former monolithic
intent_handlers.py -- see that module's docstring.
"""
from sara.tools import system as system_tools

from ..command_helpers import _quick

def _h_take_note(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.take_note(match.group(1).strip()))


def _h_read_notes(match, ctx):
    return _quick(ctx, system_tools.read_notes())


def _h_clear_notes(match, ctx):
    return _quick(ctx, system_tools.clear_notes())


def _h_add_todo(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.add_todo(match.group(1).strip()))


def _h_list_todos(match, ctx):
    """
    "what are my to-dos" defaults to pending-only (group(1) absent/
    empty); "show me all my to-dos" / "show everything" captures
    "all"/"everything" into group(1), which flips pending_only off --
    same optional-capture-group shape as _h_news()'s topic handling
    above.
    """
    pending_only = not (match and match.lastindex and match.group(1))
    return _quick(ctx, system_tools.list_todos(pending_only=pending_only))


def _h_complete_todo(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.complete_todo(match.group(1).strip()))


def _h_delete_todo(match, ctx):
    if not match:
        return None
    return _quick(ctx, system_tools.delete_todo(match.group(1).strip()))

