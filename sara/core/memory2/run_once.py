"""``python -m sara.core.memory2.run_once``: run ONE extraction pass and print the result."""
from __future__ import annotations

import json
import sys


def main() -> int:
    from config import Config

    Config.MEMORY2_ENABLED = True
    from sara.core import memory2
    from sara.core.llm import SaraLLM
    from sara.core.memory import PreferencesDB
    from sara.core.memory2 import extract
    from sara.orchestrator.history import _finish_brain_setup

    db = PreferencesDB()
    brain = SaraLLM(memory=None)
    try:
        _finish_brain_setup(db, brain)
    except Exception as exc:  # noqa: BLE001
        print(f"[run_once] brain setup warning: {type(exc).__name__}")
    store = memory2.get_store()
    if store is None:
        print("[run_once] Memory 2.0 store unavailable")
        db.close()
        return 1
    if not brain.is_usable():
        print("[run_once] LLM backend not usable right now")
        db.close()
        return 1
    result = extract.run_extraction_pass(store, db, brain)
    print(result)
    print(json.dumps(store.stats(), indent=2, sort_keys=True))
    memory2.reset_for_tests()
    db.close()
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())