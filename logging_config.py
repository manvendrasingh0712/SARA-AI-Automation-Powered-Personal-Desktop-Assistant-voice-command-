"""
logging_config.py — root logger setup for Sara AI.

Must be imported and have setup_logging() called BEFORE any other Sara
module (database.py, web.py, system.py, gui_main.py's own logger, etc.)
so every logger.error()/warning() call anywhere in the codebase is
actually captured instead of going to Python's silent "lastResort"
handler.

Writes to logs/sara.log (rotating, so it never grows unbounded) AND to
the console, so you see errors live in the terminal while developing
and still have a persistent log file for later debugging.
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

_LOG_DIR = os.path.join(os.getcwd(), "logs")
_LOG_FILE = os.path.join(_LOG_DIR, "sara.log")

_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
_BACKUP_COUNT = 3  # keep sara.log.1 .. sara.log.3

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configures the ROOT logger once. Safe to call multiple times (e.g.
    if some other entry point also calls it) — subsequent calls are a
    no-op so handlers are never duplicated/doubled-up.
    """
    global _configured
    if _configured:
        return

    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except Exception as e:
        # Can't create the log dir (permissions, read-only FS, etc.) —
        # fall back to console-only logging rather than crashing startup.
        print(f"[logging_config] Could not create log directory '{_LOG_DIR}': {e}")

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler — so you see everything live in the terminal too.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # Rotating file handler — persists across runs, capped size.
    try:
        file_handler = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception as e:
        print(f"[logging_config] Could not open log file '{_LOG_FILE}': {e}")

    # Quiet down noisy third-party loggers so sara.log stays readable.
    for noisy in ("urllib3", "asyncio", "PIL", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    root.info("Logging initialized. Writing to console and %s", _LOG_FILE)
