"""
sara.gui.app.setup_wizard
ApiSetupWizardMixin -- first-run onboarding. Detects whether Ollama, the
configured LLM model, the embedding model, and the Kokoro TTS files are
actually present and working, and lets the frontend walk a new user
through fixing whichever aren't, one click each. Progress for long-running
fixes (an `ollama pull`) streams back via the same events._push()
Python -> JS bridge every other async Api method already uses (see
sara/gui/app/events.py) rather than blocking the js_api call itself.

This never touches sara/orchestrator/core_wiring.py's own startup path --
it is purely advisory tooling for the GUI's first-run screen. A machine
that's missing everything below still starts exactly as it always did
(with whatever warnings config.py already prints); this just gives a new
user a guided, one-click way to notice and fix that instead of reading
console warnings.
"""
import json
import os
import shutil
import subprocess
import threading
import urllib.request
import webbrowser

from config import Config

from . import events


def _check_ollama() -> dict:
    """
    Best-effort, never-raises probe of a locally running Ollama server:
    is it installed (binary on PATH), is it actually running right now,
    and are the two models this app cares about already pulled.
    """
    result = {
        "ollama_installed": shutil.which("ollama") is not None,
        "ollama_running": False,
        "llm_model_pulled": False,
        "embedding_model_pulled": False,
    }
    try:
        req = urllib.request.Request(f"{Config.OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in data.get("models", [])]
        result["ollama_running"] = True
        result["llm_model_pulled"] = any(
            getattr(Config, "OLLAMA_MODEL", "") in n for n in names
        )
        result["embedding_model_pulled"] = any(
            getattr(Config, "EMBEDDING_MODEL", "") in n for n in names
        )
    except Exception:
        pass  # Ollama not running / not reachable -- flags above stay False
    return result


class ApiSetupWizardMixin:
    # ── First-run gate ───────────────────────────────────────────────────
    def get_setup_wizard_seen(self):
        try:
            return {"seen": self.db.get_preference("setup_wizard_completed") == "1"}
        except Exception as e:
            print(f"[get_setup_wizard_seen error] {e}")
            return {"seen": False}

    def mark_setup_wizard_seen(self):
        try:
            self.db.set_preference("setup_wizard_completed", "1")
            return {"ok": True}
        except Exception as e:
            print(f"[mark_setup_wizard_seen error] {e}")
            return {"ok": False}

    # ── Status check ─────────────────────────────────────────────────────
    def check_setup_status(self):
        try:
            backend = getattr(Config, "LLM_BACKEND", "ollama")
            ollama = _check_ollama()  # checked regardless of backend -- RAG's
            # embedding model always goes through Ollama even when
            # LLM_BACKEND=gemini is doing the actual chat replies.

            gemini_key_set = None
            llm_model_pulled = ollama["llm_model_pulled"]
            if backend == "gemini":
                gemini_key_set = bool(getattr(Config, "GEMINI_API_KEY", ""))
                llm_model_pulled = True  # not applicable -- nothing to pull

            status = {
                "llm_backend": backend,
                "gemini_key_set": gemini_key_set,
                "ollama_installed": ollama["ollama_installed"],
                "ollama_running": ollama["ollama_running"],
                "llm_model_pulled": llm_model_pulled,
                "llm_model_name": getattr(Config, "OLLAMA_MODEL", ""),
                "rag_enabled": bool(getattr(Config, "RAG_ENABLED", True)),
                "embedding_model_pulled": ollama["embedding_model_pulled"],
                "embedding_model_name": getattr(Config, "EMBEDDING_MODEL", ""),
                "kokoro_model_present": os.path.exists(
                    getattr(Config, "KOKORO_MODEL_PATH", "")
                ),
                "kokoro_voices_present": os.path.exists(
                    getattr(Config, "KOKORO_VOICES_PATH", "")
                ),
            }
            llm_ok = (
                status["gemini_key_set"]
                if backend == "gemini"
                else (status["ollama_running"] and status["llm_model_pulled"])
            )
            status["all_ready"] = bool(
                llm_ok
                and status["kokoro_model_present"]
                and status["kokoro_voices_present"]
            )
            return status
        except Exception as e:
            print(f"[check_setup_status error] {e}")
            return {"error": str(e), "all_ready": False}

    # ── One-click fixes ──────────────────────────────────────────────────
    def run_setup_fix(self, action):
        """
        Kicks off a fix in a background thread and returns immediately --
        progress and the final result arrive via events._push("setup_progress",
        action, state, message) where state is "running" | "done" | "error".
        """
        if action == "open_ollama_download":
            try:
                webbrowser.open("https://ollama.com/download")
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        valid_pulls = {
            "pull_llm_model": getattr(Config, "OLLAMA_MODEL", "qwen2.5"),
            "pull_embedding_model": getattr(Config, "EMBEDDING_MODEL", "nomic-embed-text"),
        }
        if action not in valid_pulls:
            return {"ok": False, "error": f"unknown action '{action}'"}

        model = valid_pulls[action]

        def _run():
            try:
                events._push(
                    "setup_progress", action, "running",
                    f"Pulling {model} — this can take a few minutes on a slow connection...",
                )
                proc = subprocess.Popen(
                    ["ollama", "pull", model],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                last_line = ""
                for line in proc.stdout:
                    last_line = line.strip()
                    if last_line:
                        events._push("setup_progress", action, "running", last_line)
                proc.wait(timeout=1800)
                if proc.returncode == 0:
                    events._push("setup_progress", action, "done", f"{model} is ready.")
                else:
                    events._push(
                        "setup_progress", action, "error",
                        f"Pull failed: {last_line or f'exit code {proc.returncode}'}",
                    )
            except FileNotFoundError:
                events._push(
                    "setup_progress", action, "error",
                    "Ollama isn't installed, or isn't on your system PATH.",
                )
            except Exception as e:
                events._push("setup_progress", action, "error", str(e))

        threading.Thread(target=_run, daemon=True, name="sara-setup-fix").start()
        return {"ok": True, "started": action}