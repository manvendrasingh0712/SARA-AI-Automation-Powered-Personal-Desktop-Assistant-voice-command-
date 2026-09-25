"""
sara.orchestrator.handlers.info

Weather, news, time-of-day, and date. Split out of the former
monolithic intent_handlers.py -- see that module's docstring.
"""
from datetime import datetime

from sara.tools import web as web_tools

from ..network_utils import _call_with_timeout
from ..command_helpers import _quick, _ack, _run_activity
from ..context_tracking import _remember_context

def _h_weather(match, ctx):
    if not match:
        return None
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    location = match.group(1)
    _remember_context(ctx, "weather", location)
    return _quick(
        ctx,
        _run_activity(
            ctx, "weather", "Checking weather", "Weather ready", "Couldn't get weather",
            lambda: _call_with_timeout(web_tools.get_weather, location),
        ),
    )


def _h_news(match, ctx):
    _ack(ctx)
    ctx["ui_update"]("status", "thinking")
    if match and match.lastindex and match.lastindex >= 1:
        topic = match.group(1)
        _remember_context(ctx, "news", topic)
        news_call = lambda: _call_with_timeout(web_tools.get_news, topic)  # noqa: E731
    else:
        news_call = lambda: _call_with_timeout(web_tools.get_news)  # noqa: E731
    return _quick(
        ctx,
        _run_activity(
            ctx, "news", "Fetching news", "News ready", "Couldn't get news", news_call
        ),
    )



def _h_time_query(match, ctx):
    return _quick(ctx, f"It's {datetime.now().strftime('%I:%M %p')}.")


def _h_date_query(match, ctx):
    return _quick(ctx, f"Today is {datetime.now().strftime('%A, %B %d, %Y')}.")


