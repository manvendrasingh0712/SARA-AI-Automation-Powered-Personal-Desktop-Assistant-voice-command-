"""
sara.orchestrator.lazy
Lazy background construction (used for the LLM client and ReminderManager
so first UI paint / wake-word readiness never blocks on a cold-start).
"""

import threading

from sara.orchestrator._constants import _DEBUG


def _debug_log(msg: str) -> None:
    if _DEBUG:
        print(msg)


# ----------------------------------------------------------------------------
# Lazy loader
# ----------------------------------------------------------------------------


class _Lazy:
    __slots__ = ("_factory", "_obj", "_ready", "_thread", "_error")

    def __init__(self, factory):
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_obj", None)
        object.__setattr__(self, "_error", None)
        object.__setattr__(self, "_ready", threading.Event())
        t = threading.Thread(target=self._build, daemon=True)
        object.__setattr__(self, "_thread", t)
        t.start()

    def _build(self):
        try:
            obj = self._factory()
            object.__setattr__(self, "_obj", obj)
        except Exception as e:
            print(f"[_Lazy] background factory failed: {e}")
            object.__setattr__(self, "_error", e)
        finally:
            self._ready.set()

    def __getattr__(self, name):
        self._ready.wait()
        if self._error is not None:
            raise RuntimeError(
                f"Component failed to initialize, cannot access '{name}': {self._error}"
            ) from self._error
        return getattr(self._obj, name)
