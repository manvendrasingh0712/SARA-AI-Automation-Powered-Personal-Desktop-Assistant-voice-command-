"""
Read the real per-stage voice latency out of telemetry.sqlite.

    python -m bench.voice_latency              # all turns, voice vs text
    python -m bench.voice_latency --source voice
    python -m bench.voice_latency --since-hours 2
    python -m bench.voice_latency --csv out.csv

WHY THIS EXISTS
    SARA already instruments 13 stages per turn and already stores them
    (sara/core/telemetry). `python -m sara.core.telemetry.dump` prints raw
    rows, and telemetry.get_summary() computes ttfa percentiles -- but from
    the in-memory ring of the RUNNING process, so it is gone once SARA
    exits. Nothing read the persisted table back and reported a
    distribution, which is what a before/after baseline needs.

    This adds no dependency (sqlite3 is stdlib) and no instrumentation. It
    only reads what is already being written.

WHY voice AND text ARE REPORTED SEPARATELY
    They are not comparable. A `source='text'` turn skips wake word, audio
    capture, endpointing and STT entirely, so its time-to-first-audio is a
    LOWER BOUND on the spoken path, not a measurement of it. The audit's
    first baseline contained only text turns, and reading those numbers as
    voice numbers would have understated the real figure by whatever STT
    and endpointing cost. The two are never pooled here.

PERCENTILES
    Nearest-rank, and None below 5 samples -- identical to
    telemetry._percentile, so numbers from here and from get_summary() are
    directly comparable rather than merely similar.

NOTE ON THE WAL
    telemetry.sqlite runs in WAL mode, so recent turns may live in
    telemetry.sqlite-wal rather than the main file. Opening the database
    read-only and letting SQLite replay the WAL is handled below; a plain
    read of the main file alone can report an empty table while hundreds of
    KB of rows sit beside it.
"""
from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

# The stages worth reporting, in pipeline order. Names match the columns
# telemetry.store writes, so this cannot drift from the schema silently.
_STAGES: tuple[tuple[str, str], ...] = (
    ("speech_end_ms", "wake -> end of speech"),
    ("stt_ms", "transcription"),
    ("route_ms", "intent routing"),
    ("llm_ttft_ms", "model first token"),
    ("llm_total_ms", "model total"),
    ("tool_ms", "tool execution"),
    ("tts_start_ms", "synthesis -> first audio"),
    ("ttfa_ms", "TIME TO FIRST AUDIO"),
    ("total_ms", "total turn"),
)


def _db_path() -> Path:
    """Resolve telemetry.sqlite through the app's own config."""
    from sara.core.telemetry.store import db_path

    return db_path()


def _connect(path: Path) -> sqlite3.Connection:
    """Read-only connection that still replays the WAL."""
    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; None below 5 samples (matches telemetry)."""
    clean = sorted(v for v in values if v is not None)
    if len(clean) < 5:
        return None
    return clean[math.ceil(pct * len(clean) / 100) - 1]


def _fmt(value: Optional[float]) -> str:
    return "n<5" if value is None else f"{value:.0f}"


def _report(rows: list[sqlite3.Row], label: str) -> None:
    if not rows:
        print(f"\n{label}: no turns recorded.")
        return
    print(f"\n{label}: {len(rows)} turns")
    print(f"  {'stage':<26} {'n':>4} {'p50':>8} {'p90':>8} {'p95':>8} {'max':>8}")
    print(f"  {'-' * 26} {'-' * 4} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8}")
    for column, pretty in _STAGES:
        vals = [r[column] for r in rows if r[column] is not None]
        if not vals:
            continue
        print(
            f"  {pretty:<26} {len(vals):>4} {_fmt(_percentile(vals, 50)):>8} "
            f"{_fmt(_percentile(vals, 90)):>8} {_fmt(_percentile(vals, 95)):>8} "
            f"{max(vals):>8.0f}"
        )
    outcomes: dict[str, int] = {}
    for r in rows:
        outcomes[r["outcome"] or "?"] = outcomes.get(r["outcome"] or "?", 0) + 1
    print("  outcomes: " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items())))
    langs: dict[str, int] = {}
    for r in rows:
        langs[r["lang"] or "?"] = langs.get(r["lang"] or "?", 0) + 1
    print("  languages: " + ", ".join(f"{k}={v}" for k, v in sorted(langs.items())))


def _report_telemetry_import() -> bool:
    """
    Can the recorder even be imported the way the voice path imports it?

    D67: core_wiring.py wraps

        from sara.core.telemetry import begin_turn, end_turn, mark_pending

    in a bare `except Exception` that installs no-op stubs. That fallback is
    correct -- telemetry is optional -- but it was SILENT, so a failure
    there discards every voice turn with no error anywhere, which is
    indistinguishable from a voice problem.

    Importing all THREE names here, exactly as core_wiring does: a package
    that imports but is missing one name fails that statement and takes the
    whole no-op path, so probing a single name would not reproduce it.
    """
    try:
        import importlib

        _mod = importlib.import_module("sara.core.telemetry")
        for _name in ("begin_turn", "end_turn", "mark_pending"):
            if not hasattr(_mod, _name):
                raise ImportError(
                    f"cannot import name {_name!r} from 'sara.core.telemetry'"
                )
    except Exception as exc:
        print("\n  recorder import : *** FAILED ***")
        print(f"                    {type(exc).__name__}: {exc}")
        print("                    core_wiring.py falls back to no-op stubs on")
        print("                    this, so NOTHING is recorded however much")
        print("                    you speak, and nothing is logged. This is")
        print("                    the cause of zero voice rows.")
        return False
    print("\n  recorder import : ok (begin_turn, end_turn, mark_pending)")
    return True


def _report_telemetry_switch() -> None:
    """
    Say whether tracing is even switched on.

    Config.TELEMETRY_ENABLED defaults to True, but sara/gui/app/perf_api.py
    persists a "ui:telemetry_enabled" preference and re-applies it the first
    time the GUI asks for a perf summary. So a user who once turned tracing
    off in the GUI has it off in every later session, silently -- and this
    script would otherwise report "no turns recorded" and tell them to go
    and speak more, which cannot help.

    Read-only, and best-effort: an unreadable preferences DB means "unknown",
    never a crash.
    """
    import sqlite3 as _sq

    # Same app-data folder telemetry.sqlite lives in.
    db = _db_path().parent / "sara_data.db"
    if not db.exists():
        print(f"\n  tracing switch : UNKNOWN (no preferences DB at {db})")
        return
    try:
        con = _sq.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT value FROM preferences WHERE key = ?",
                ("ui:telemetry_enabled",),
            ).fetchone()
        finally:
            con.close()
    except Exception as exc:
        print(f"\n  tracing switch : UNKNOWN ({type(exc).__name__}: {exc})")
        return

    if row is None:
        print("\n  tracing switch : never changed in the GUI, so it follows")
        print("                   TELEMETRY_ENABLED in .env (default True).")
        print("                   So zero voice rows means voice turns are not")
        print("                   reaching the recorder -- not that tracing is off.")
    elif str(row[0]).strip() == "1":
        print("\n  tracing switch : ON (saved GUI preference)")
        print("                   So zero voice rows means voice turns are not")
        print("                   reaching the recorder -- not that tracing is off.")
    else:
        print("\n  tracing switch : *** OFF (saved GUI preference) ***")
        print("                   Nothing was recorded, however much you spoke.")
        print("                   Turn tracing back on in the GUI's performance")
        print("                   panel, then speak and re-run this.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=("voice", "text", "all"), default="all")
    ap.add_argument("--since-hours", type=float, default=None,
                    help="only turns from the last N hours")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--csv", type=str, default=None, help="also write raw rows here")
    args = ap.parse_args(argv)

    try:
        path = _db_path()
    except Exception as exc:  # config import problems must not traceback
        print(f"could not resolve telemetry.sqlite: {exc}", file=sys.stderr)
        return 3
    if not path.exists():
        print(f"no telemetry database at {path}", file=sys.stderr)
        print("Telemetry is on by default (TELEMETRY_ENABLED). Use SARA, then "
              "re-run this.", file=sys.stderr)
        return 3

    columns = ["turn_id", "ts", "source", "lang", "route", "intent", "outcome"]
    columns += [c for c, _ in _STAGES]
    where, params = [], []
    if args.source != "all":
        where.append("source = ?")
        params.append(args.source)
    if args.since_hours:
        where.append("ts >= ?")
        params.append(time.time() - args.since_hours * 3600.0)
    sql = f"SELECT {', '.join(columns)} FROM turn_trace"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(args.limit)

    conn = _connect(path)
    try:
        conn.row_factory = sqlite3.Row
        rows = list(conn.execute(sql, params))
    except sqlite3.Error as exc:
        print(f"could not read turn_trace: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()

    print(f"telemetry: {path}")
    if not rows:
        print("no matching turns.")
        return 0

    voice = [r for r in rows if r["source"] == "voice"]
    text = [r for r in rows if r["source"] == "text"]

    if args.source in ("all", "voice"):
        _report(voice, "VOICE turns (microphone -> first audio)")
    if args.source in ("all", "text"):
        _report(text, "TEXT turns (typed; NO wake/capture/STT -- a lower bound)")

    if args.source == "all" and not voice:
        print(
            "\nNo voice turns recorded, so there is no microphone-to-first-audio\n"
            "baseline yet. The text figures above exclude wake word, capture,\n"
            "endpointing and transcription, so they are a lower bound and must\n"
            "not be quoted as voice latency."
        )
        # D53: do not just tell the user to speak more. Zero voice rows has
        # two completely different causes and this script can distinguish
        # them, so it must. A whole measurement session was lost to the
        # first cause while the message above blamed the second.
        _report_telemetry_import()
        _report_telemetry_switch()
        print(
            "\nIf tracing is ON above, capture a baseline: speak to SARA for\n"
            "15-20 wake cycles (mix English, Hindi and Hinglish, and interrupt\n"
            "it a few times), then re-run\n"
            "    python -m bench.voice_latency --source voice"
        )

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(columns)
            for r in rows:
                writer.writerow([r[c] for c in columns])
        print(f"\nraw rows written to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
