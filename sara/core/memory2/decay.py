"""Memory 2.0 strength decay and the daily archive / purge pass."""
from __future__ import annotations

import logging
import math
import time
from typing import Any

logger = logging.getLogger(__name__)

_DAY_S = 86400.0
_META_LAST = "last_decay_ts"
_INACTIVE = ("archived", "superseded", "retracted")


def cfg_value(name: str, default: Any) -> Any:
    """Config attribute with a safe default (Config may be unavailable in tests)."""
    try:
        from config import Config

        value = getattr(Config, name, default)
        return default if value is None else value
    except Exception:  # noqa: BLE001
        return default


def tau_days() -> float:
    """Configured decay time constant in days (> 0)."""
    try:
        tau = float(cfg_value("MEMORY2_DECAY_TAU_DAYS", 60.0))
    except (TypeError, ValueError):
        return 60.0
    return tau if tau > 0 else 60.0


def strength(
    importance: float, ref_ts: float, use_count: int, pinned: bool, now: float, tau_days: float
) -> float:
    """importance * exp(-dt / (tau * (1 + 0.5*log1p(uses)))); pinned items are always 1.0."""
    if pinned:
        return 1.0
    tau = float(tau_days) if tau_days and tau_days > 0 else 60.0
    dt_days = max(0.0, (float(now) - float(ref_ts)) / _DAY_S)
    uses = max(0, int(use_count or 0))
    return float(importance) * math.exp(-dt_days / (tau * (1.0 + 0.5 * math.log1p(uses))))


def to_float(value: Any, default: float = 0.0) -> float:
    """Finite float or ``default``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def row_ref_ts(row: dict) -> float:
    """Most recent known activity timestamp of a fact or event row."""
    return max(
        to_float(row.get("last_used_at")), to_float(row.get("created_at")),
        to_float(row.get("valid_from")), to_float(row.get("ts")),
    )


def _archive(store: Any, now: float, tau: float, threshold: float) -> int:
    archived = 0
    for kind in ("facts", "events"):
        for row in store.iter_rows(kind, ("active",)):
            if row.get("pinned"):
                continue
            value = strength(to_float(row.get("importance"), 0.5), row_ref_ts(row),
                             int(to_float(row.get("use_count"))), False, now, tau)
            if value >= threshold:
                continue
            if store.set_status(row["id"], kind, "archived"):
                store.set_meta(f"archived:{kind}:{row['id']}", repr(now))
                archived += 1
    return archived


def _purge(store: Any, now: float, purge_days: float) -> int:
    cutoff = now - purge_days * _DAY_S
    purged = 0
    for kind in ("facts", "events"):
        for row in store.iter_rows(kind, _INACTIVE):
            if row.get("pinned"):
                continue
            stamp = to_float(store.get_meta(f"archived:{kind}:{row['id']}", "0"))
            stamp = max(stamp, to_float(row.get("valid_to")), row_ref_ts(row)) if stamp <= 0 else stamp
            if stamp < cutoff and store.delete_row(row["id"], kind):
                purged += 1
    return purged


def daily_pass(store: Any, now: float | None = None) -> dict:
    """Archive weak items and purge old inactive rows; runs at most once per 24 h."""
    result = {"archived": 0, "purged": 0, "ran": False}
    try:
        stamp = time.time() if now is None else float(now)
        if stamp - to_float(store.get_meta(_META_LAST, "0")) < _DAY_S:
            return result
        result["archived"] = _archive(
            store, stamp, tau_days(), to_float(cfg_value("MEMORY2_ARCHIVE_THRESHOLD", 0.05), 0.05)
        )
        result["purged"] = _purge(store, stamp, to_float(cfg_value("MEMORY2_PURGE_DAYS", 90), 90.0))
        store.set_meta(_META_LAST, repr(stamp))
        result["ran"] = True
        logger.info("[Memory2] decay pass archived=%d purged=%d",
                    result["archived"], result["purged"])
    except Exception as exc:  # noqa: BLE001
        logger.error("[Memory2] decay pass failed (%s)", type(exc).__name__)
    return result