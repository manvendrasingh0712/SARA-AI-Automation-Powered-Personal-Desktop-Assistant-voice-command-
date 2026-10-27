"""
sara.gui.app.clock_api
ApiClockMixin -- Home Clock Card: timer + stopwatch control surface.

The actual countdown/elapsed-time math lives in the frontend
(js/clock-card.js), computed off real timestamps so it can't drift -- these
methods only start/stop that state and push the same 'timer_state' /
'stopwatch_state' events the frontend already listens for. That means a
caller other than the on-screen buttons (see NOTE) drives the Clock Card
exactly the same way, with no frontend changes needed.

NOTE -- this does NOT wire the voice command itself. Recognising "Hey Sara,
set a timer for 10 minutes" is intent parsing, which lives in
sara/orchestrator/intent_handlers.py -- outside sara/gui and not part of the
files provided for this task. Whoever owns that file should, on recognising
the timer/stopwatch intents, call self.start_timer(seconds) /
self.start_stopwatch() / etc. on this same Api object (it's mixed into the
one exposed Api class in engine.py, so these methods are already on
self there). No other change is needed once that call is added.
"""
from .events import _push


class ApiClockMixin:

    def start_timer(self, seconds):
        try:
            seconds = max(1, int(seconds))
        except (TypeError, ValueError):
            return {"ok": False}
        _push("timer_state", {"status": "started", "duration": seconds})
        return {"ok": True, "duration": seconds}

    def cancel_timer(self):
        _push("timer_state", {"status": "cancelled"})
        return {"ok": True}

    def start_stopwatch(self):
        _push("stopwatch_state", {"status": "started"})
        return {"ok": True}

    def pause_stopwatch(self):
        _push("stopwatch_state", {"status": "paused"})
        return {"ok": True}

    def resume_stopwatch(self):
        _push("stopwatch_state", {"status": "resumed"})
        return {"ok": True}

    def reset_stopwatch(self):
        _push("stopwatch_state", {"status": "reset"})
        return {"ok": True}