"""Part 3, P2: non-blocking, redacted logging."""
import io
import json
import logging
import logging.handlers
import queue
import sys
import time

from sara.core import log as L


def _logger(tmp_path):
    q = queue.SimpleQueue()
    stream = io.StringIO()
    lg = logging.getLogger("sara.test.pipeline")
    lg.handlers.clear()
    lg.propagate = False
    qh = logging.handlers.QueueHandler(q)
    qh.addFilter(L.RedactFilter())
    qh.addFilter(L.RateLimitFilter(window_s=5.0))
    lg.addHandler(qh)
    lg.setLevel(logging.DEBUG)
    jpath = tmp_path / "t.jsonl"
    jh = logging.FileHandler(jpath, encoding="utf-8")
    jh.setFormatter(L.JsonLinesFormatter())
    sh = logging.StreamHandler(stream)
    sh.setFormatter(logging.Formatter("%(message)s"))
    listener = logging.handlers.QueueListener(q, jh, sh)
    listener.start()
    return lg, listener, stream, jpath


def test_secrets_never_reach_the_log_or_the_jsonl(tmp_path):
    lg, lis, stream, jpath = _logger(tmp_path)
    fake = "AIza" + "x" * 35
    lg.info("calling with key %s and refresh_token: 1//0abcdefghijklmnop and GOCSPX-abcdefghijk1234", fake)  # secret-scan: allow (made-up test value)
    time.sleep(0.2)
    lis.stop()
    text = stream.getvalue() + jpath.read_text(encoding="utf-8")
    assert fake not in text and "GOCSPX-abcdef" not in text and "1//0abcdefghijklmnop" not in text and "[REDACTED]" in text
    rec = json.loads(jpath.read_text(encoding="utf-8").splitlines()[0])
    assert set(rec) == {"ts", "level", "component", "turn_id", "msg"} and rec["component"] == "sara.test.pipeline"


def test_repeating_warning_from_one_call_site_is_rate_limited(tmp_path):
    lg, lis, stream, _ = _logger(tmp_path)
    for _ in range(50):
        lg.warning("mic stream stalled")
    time.sleep(0.2)
    lis.stop()
    assert stream.getvalue().count("mic stream stalled") == 1


def test_a_log_call_is_cheap_on_the_caller_thread(tmp_path):
    lg, lis, _, _ = _logger(tmp_path)
    t = time.perf_counter()
    for i in range(2000):
        lg.info("frame %d processed", i)
    per_call_us = (time.perf_counter() - t) / 2000 * 1e6
    lis.stop()
    assert per_call_us < 200          # measured ~20-40 us on this PC; generous bound for CI


def test_async_stdout_does_not_block_and_redacts():
    class Slow(io.StringIO):
        def write(self, s):
            time.sleep(0.05)
            return super().write(s)

    real = Slow()
    out = L.AsyncStdout(real)
    t = time.perf_counter()
    for _ in range(20):
        out.write("[STT] line\n")
    assert time.perf_counter() - t < 0.2        # 20 x 50 ms of console time was not paid by the caller
    out.write("key AIza" + "y" * 35 + "\n")
    out.flush_and_stop()
    assert "AIzayyyy" not in real.getvalue() and "[REDACTED]" in real.getvalue()


def test_setup_logging_end_to_end_in_a_fresh_process(tmp_path):
    """The real entry point (logging_config.setup_logging) must start, write both log files and redact a key.
    Regression: a live start-up crashed with UnboundLocalError after the queue pipeline replaced the handler block."""
    import os
    import subprocess

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import logging, logging_config as lc\n"
        "lc.setup_logging()\n"
        "logging.getLogger('t').warning('key AIzaSy' + 'A' * 33 + ' leaked')\n"
        "from sara.core import log; log.shutdown()\n"
    )
    env = dict(os.environ, PYTHONPATH=root)
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    text = (tmp_path / "logs" / "sara.log").read_text(encoding="utf-8")
    assert "Logging initialized" in text and "leaked" in text and "AIzaSy" + "A" * 33 not in text
    assert (tmp_path / "logs" / "sara.jsonl").read_text(encoding="utf-8").count("\n") >= 2
