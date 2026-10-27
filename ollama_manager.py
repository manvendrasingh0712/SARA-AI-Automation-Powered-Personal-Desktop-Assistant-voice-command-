"""
SARA AI — Ollama lifecycle manager
----------------------------------
Owns only the Ollama server process started by SARA.

Behavior:
- Reuses an already-running Ollama server.
- Starts `ollama serve` silently when the local server is unavailable.
- Waits for the API to become ready before returning.
- Stops only the process started by this manager.
- Uses a Windows Job Object so a SARA crash/forced process exit can
  also clean up the Ollama child process tree.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen


logger = logging.getLogger("sara.ollama_manager")


class OllamaManager:
    """Lifecycle manager for a local Ollama `serve` process."""

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        ready_timeout_s: float = 60.0,
        poll_interval_s: float = 0.25,
        exe_path: Optional[str] = None,
    ) -> None:
        self.host = self._normalize_host(host)
        self.ready_timeout_s = max(1.0, float(ready_timeout_s))
        self.poll_interval_s = max(0.05, float(poll_interval_s))
        self.exe_path = exe_path or os.getenv("OLLAMA_EXE_PATH") or None

        self._process: Optional[subprocess.Popen] = None
        self._job_handle = None
        self._owns_process = False
        self._lock = threading.RLock()
        self._started = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """
        Ensure the local Ollama API is ready.

        Returns True when Ollama is ready/reused/started.
        Returns False when SARA cannot manage or start Ollama.

        This method never kills an Ollama process it did not create.
        """
        with self._lock:
            if self._started and self.is_ready():
                return True

            if not self._is_local_host():
                logger.info(
                    "Ollama host %s is not local; lifecycle management skipped.",
                    self.host,
                )
                self._started = self.is_ready()
                return self._started

            # Fast path: an already-running server is never owned by SARA.
            if self.is_ready():
                self._started = True
                self._owns_process = False
                logger.info("Existing Ollama server detected; reusing it.")
                return True

            # If an Ollama process exists but its API is still warming up,
            # wait before starting another server to avoid duplicate serves.
            if self._ollama_process_exists():
                logger.info(
                    "Ollama process already exists; waiting for its API to become ready."
                )
                if self._wait_until_ready():
                    self._started = True
                    self._owns_process = False
                    logger.info("Existing Ollama server became ready; reusing it.")
                    return True

                logger.warning(
                    "An Ollama process exists, but its API did not become ready "
                    "within %.1fs. SARA will not terminate that external process.",
                    self.ready_timeout_s,
                )
                return False

            executable = self._resolve_executable()
            if executable is None:
                logger.error(
                    "Ollama executable not found. Install Ollama or set "
                    "OLLAMA_EXE_PATH."
                )
                return False

            try:
                process = self._spawn(executable)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to start Ollama: %s", exc)
                return False

            self._process = process
            self._owns_process = True
            self._started = True

            # Best-effort crash cleanup on Windows. Even if Job Object setup
            # is unavailable, normal SARA shutdown still stops our process.
            self._attach_job_object(process)

            if self._wait_until_ready(process=process):
                logger.info(
                    "Ollama started successfully by SARA (pid=%s).",
                    process.pid,
                )
                return True

            logger.error(
                "Ollama process started (pid=%s) but API was not ready within %.1fs.",
                process.pid,
                self.ready_timeout_s,
            )
            self._stop_owned_process_locked()
            return False

    def stop(self) -> None:
        """Stop only the Ollama process created by this manager."""
        with self._lock:
            self._stop_owned_process_locked()

    def is_ready(self) -> bool:
        """Return True when the configured Ollama API responds successfully."""
        try:
            request = Request(
                f"{self.host}/api/tags",
                headers={"User-Agent": "SARA-AI-OllamaManager/1.0"},
                method="GET",
            )
            with urlopen(request, timeout=1.5) as response:
                return 200 <= int(response.status) < 300
        except Exception:
            return False

    @property
    def owns_process(self) -> bool:
        """Whether SARA started the currently tracked Ollama process."""
        return self._owns_process

    @property
    def pid(self) -> Optional[int]:
        """PID of the process SARA started, if still tracked."""
        process = self._process
        return process.pid if process is not None else None

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    def _spawn(self, executable: str) -> subprocess.Popen:
        env = os.environ.copy()
        env.setdefault("OLLAMA_HOST", self._ollama_bind_host())

        startupinfo = None
        creationflags = 0

        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

            creationflags = (
                subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )

        return subprocess.Popen(
            [executable, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=creationflags,
            close_fds=(os.name != "nt"),
            env=env,
        )

    def _stop_owned_process_locked(self) -> None:
        if not self._owns_process:
            # Important: an external Ollama server must remain untouched.
            self._close_job_handle_locked()
            self._process = None
            self._started = False
            return

        process = self._process
        if process is None:
            self._close_job_handle_locked()
            self._owns_process = False
            self._started = False
            return

        try:
            if process.poll() is None:
                logger.info("Stopping Ollama started by SARA (pid=%s).", process.pid)
                process.terminate()
                try:
                    process.wait(timeout=4.0)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "Ollama did not exit after terminate; forcing shutdown "
                        "(pid=%s).",
                        process.pid,
                    )
                    process.kill()
                    process.wait(timeout=2.0)
        except (ProcessLookupError, OSError):
            pass
        finally:
            self._close_job_handle_locked()
            self._process = None
            self._owns_process = False
            self._started = False

    # ------------------------------------------------------------------
    # Readiness / discovery
    # ------------------------------------------------------------------

    def _wait_until_ready(
        self,
        process: Optional[subprocess.Popen] = None,
    ) -> bool:
        deadline = time.monotonic() + self.ready_timeout_s

        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                logger.error(
                    "Ollama exited during startup (returncode=%s).",
                    process.returncode,
                )
                return False

            if self.is_ready():
                return True

            time.sleep(self.poll_interval_s)

        return False

    def _ollama_process_exists(self) -> bool:
        if os.name != "nt":
            return False

        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/NH"],
                capture_output=True,
                text=True,
                timeout=2.0,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            output = result.stdout.lower()
            return "ollama.exe" in output
        except Exception:
            return False

    def _resolve_executable(self) -> Optional[str]:
        # Explicit override first.
        if self.exe_path:
            explicit = Path(os.path.expandvars(os.path.expanduser(self.exe_path)))
            if explicit.is_file():
                return str(explicit)
            logger.warning("OLLAMA_EXE_PATH does not exist: %s", explicit)

        # PATH lookup.
        found = shutil.which("ollama")
        if found:
            return found

        # Standard Windows installation locations.
        if os.name == "nt":
            candidates = [
                Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path(os.getenv("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
                Path(os.getenv("PROGRAMW6432", "")) / "Ollama" / "ollama.exe",
            ]
            for candidate in candidates:
                if candidate.is_file():
                    return str(candidate)

        return None

    # ------------------------------------------------------------------
    # Windows crash cleanup
    # ------------------------------------------------------------------

    def _attach_job_object(self, process: subprocess.Popen) -> None:
        if os.name != "nt":
            return

        try:
            import win32job  # type: ignore

            job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(
                job,
                win32job.JobObjectExtendedLimitInformation,
            )
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                job,
                win32job.JobObjectExtendedLimitInformation,
                info,
            )
            win32job.AssignProcessToJobObject(job, process._handle)
            self._job_handle = job

            logger.debug(
                "Attached Ollama pid=%s to SARA cleanup Job Object.",
                process.pid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not attach Ollama to Windows Job Object: %s. "
                "Graceful shutdown cleanup remains enabled.",
                exc,
            )

    def _close_job_handle_locked(self) -> None:
        if self._job_handle is None:
            return

        try:
            import win32api  # type: ignore

            win32api.CloseHandle(self._job_handle)
        except Exception:
            try:
                self._job_handle.Close()
            except Exception:
                pass
        finally:
            self._job_handle = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_host(host: str) -> str:
        host = (host or "http://127.0.0.1:11434").strip().rstrip("/")
        if "://" not in host:
            host = f"http://{host}"
        parsed = urlparse(host)
        if not parsed.netloc:
            return "http://127.0.0.1:11434"
        return host

    def _is_local_host(self) -> bool:
        hostname = urlparse(self.host).hostname
        return hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
            "0.0.0.0",
        }

    def _ollama_bind_host(self) -> str:
        parsed = urlparse(self.host)
        hostname = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434
        return f"{hostname}:{port}"


__all__ = ["OllamaManager"]
