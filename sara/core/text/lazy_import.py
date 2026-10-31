"""A module that is imported on first use. `dateparser = lazy_module("dateparser")` keeps every `dateparser.parse(...)`
call (and `patch("pkg.mod.dateparser")` in tests) working while taking ~0.4 s out of start-up."""
from __future__ import annotations

import importlib
import threading
from types import ModuleType


class _LazyModule(ModuleType):
    def __init__(self, name: str):
        super().__init__(name)
        object.__setattr__(self, "_lazy_name", name)
        object.__setattr__(self, "_lazy_mod", None)
        object.__setattr__(self, "_lazy_lock", threading.Lock())

    def _load(self):
        mod = object.__getattribute__(self, "_lazy_mod")
        if mod is None:
            with object.__getattribute__(self, "_lazy_lock"):
                mod = object.__getattribute__(self, "_lazy_mod")
                if mod is None:
                    mod = importlib.import_module(object.__getattribute__(self, "_lazy_name"))
                    object.__setattr__(self, "_lazy_mod", mod)
        return mod

    def __getattr__(self, item):
        return getattr(self._load(), item)

    def __dir__(self):
        return dir(self._load())


def lazy_module(name: str) -> ModuleType:
    return _LazyModule(name)
