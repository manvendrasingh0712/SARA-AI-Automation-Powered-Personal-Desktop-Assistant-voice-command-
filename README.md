<div align="center">

# 🎙️ SARA AI

### An always-listening, bilingual, local-first desktop AI assistant

**Wake word → speech → intent → action, entirely on your own machine.**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](./LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=flat-square&logo=windows&logoColor=white)](#-installation)
[![GitHub stars](https://img.shields.io/github/stars/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant?style=flat-square)](https://github.com/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant/stargazers)
[![GitHub last commit](https://img.shields.io/github/last-commit/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant?style=flat-square)](https://github.com/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant/commits)
[![GitHub issues](https://img.shields.io/github/issues/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant?style=flat-square)](https://github.com/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant/issues)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant?style=flat-square)](https://github.com/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant/pulls)

[Overview](#-project-overview) • [Features](#-key-features) • [Installation](#-installation) • [Configuration](#-configuration-reference) • [Usage](#-usage-examples) • [Roadmap](#-roadmap)

</div>

---

## 📖 Project Overview

**Sara AI** is a Windows desktop AI assistant in the JARVIS mold: it wakes on a spoken name, listens, understands, talks back in a synthesized voice, and can reach out and actually *do things* on the machine it runs on — open apps, control volume and brightness, manage windows, set reminders, search the web, read your screen, and hold a genuine bilingual (English / Hindi / Hinglish) conversation. It can also speak up on its own — a low battery, an upcoming reminder, a long idle break, a talk-streak milestone — without you having to say the wake word first, and it can explain why it spoke up if you ask.

It exists as a **local-first alternative to cloud voice assistants**: speech-to-text (`faster-whisper`), text-to-speech (Kokoro ONNX), and the conversational brain (Ollama) all run on-device by default. Nothing has to leave the machine unless you explicitly opt into the optional Gemini backend for chat or vision. That constraint — **fast, free, low-latency, no forced cloud dependency** — shapes almost every design decision in the codebase, from the regex-first intent router that resolves 100+ common commands without ever calling an LLM, to the GPU/CPU auto-fallback in the TTS pipeline.

**Who it's for:** it's a portfolio-grade reference implementation of a full voice-assistant stack — wake-word detection, VAD, real acoustic echo cancellation, streaming LLM inference, sentence-level TTS streaming with barge-in, long-term semantic memory, a plugin-style skills system, and a native-feeling desktop GUI — useful to anyone building a similar assistant, or evaluating the engineering behind one.

> For the full internal architecture (module dependency graph, voice-pipeline sequence diagrams, config internals, verification methodology), see `PROJECT_MEMORY.md`. This README stays focused on what the assistant does and how to run it.

---

## ✨ Key Features

### 🧠 AI & Language
- **Dual LLM backend** — local [Ollama](https://ollama.com) (default, e.g. `qwen2.5`) or cloud [Gemini](https://ai.google.dev) (optional, opt-in via `.env`), with automatic retry/backoff and a warm-up wait for the local model
- **Streaming responses**, split sentence-by-sentence so speech can start before the full reply has finished generating
- **Bilingual conversation** — English, Hindi, and Hinglish, with distinct persona-tuned system prompts per language and a thread-safe language toggle driven from the GUI
- **Long-term semantic memory (RAG)** — every exchange is embedded and stored in SQLite; relevant past memories are retrieved by similarity and injected back into context, so Sara can recall things mentioned sessions ago
- **Rule-based tool-call fallback** — when the fast regex intent router finds no match, a lightweight resolver checks whether a known tool (weather, news, web search, app control, calculator, etc.) still applies before falling back to free-form chat

### 🔔 Proactive & Explainable
- **Proactive Engine** — Sara can speak up without a wake word for a low battery warning, an upcoming-reminder heads-up, an idle/break nudge after a long silence, or a talk-streak milestone. Each has its own cooldown, can be turned off entirely or per-trigger, and is silenced automatically by Focus Mode or Pause Listening.
- **"Why did you say that?"** — ask Sara (in English or Hinglish, e.g. "kyu bola?") why she spoke up proactively, and she'll tell you the actual logged reason.
- **Daily talk-streak tracking** — with milestone call-outs at 3/7/14/30/50/100/200/365 days.

### 🧩 Skills (plugin system)
- Drop a new skill file into `sara/skills/` and it's picked up automatically at startup — no manual wiring. Shipped out of the box: a **daily briefing** (weather + today's reminders + a headline, spoken as one summary), an **offline joke bank**, **notes Q&A** (ask questions about your own notes via retrieval-augmented search), and the streak tracker above.

### 🎙️ Voice
- **Wake-word activation** with a built-in STT-based multi-variant fallback (`sara`, `sarah`, `hey sara`, `hey sarah`, customizable), or a custom-trained `openwakeword` model
- **Local speech-to-text** via `faster-whisper`, tuned with configurable beam size and hallucination filtering
- **Local text-to-speech** via **Kokoro ONNX**, GPU-accelerated (CUDA) with automatic CPU fallback, per-language voice/speed routing, and a phrase-level cache
- **Real Acoustic Echo Cancellation (AEC)** — cancels Sara's own speaker output from the mic input in real time
- **Barge-in** — interrupt Sara mid-sentence just by speaking
- **Continuous conversation mode** with an idle timeout, so a full wake word isn't needed for every follow-up turn

### 🤖 Automation & System Control
- **100+ system actions** — app launching/closing, volume & brightness, window management, media keys, keyboard shortcuts, Wi-Fi/Bluetooth toggling, dark/light mode, folder shortcuts, Windows Settings deep-links, and system info
- **Reminders & timers**, **notes**, **clipboard read/write**
- **Screen understanding** — takes a screenshot and describes it via a Gemini vision model
- **Web tools** — search, news, weather, article summarization, opening URLs, launching YouTube/Spotify queries
- **Safe in-app calculator** — bounded custom expression evaluator instead of raw `eval()`

### 🖥️ UI
- Native-feeling desktop GUI (`pywebview`) — no Electron/browser overhead
- 11 pages: Home, Chat, Voice, Memory, Brain, Automation, Apps, System, Web Search, Notes, Reminders, Settings
- Media player card with album art, shuffle/repeat, and correct active-session detection
- **First-run Setup Wizard** — checks whether Ollama, the LLM model, the embedding model, and the TTS voice files are ready, and lets you fix each with one click
- Live system stats, glassmorphic styling, boot progress, and a persistent backend-disconnection banner

### 🧠 Memory
- **Short-term**: a bounded in-process conversation window
- **Structured**: SQLite (WAL mode) — preferences, conversation log, and a proactive-nudge log
- **Long-term/semantic**: the RAG store described above

---

## 🚀 Installation

> **Platform note:** Sara AI is currently **Windows-only** — several dependencies (`pycaw`, `comtypes`, `pywin32`, `screen_brightness_control`, `keyboard`) and system-control code paths are Windows-specific.

### Prerequisites

- Python 3.10 or later
- [Ollama](https://ollama.com) installed and on `PATH` (for the default local LLM backend)
- A working microphone and speaker
- (Optional, for GPU-accelerated TTS/STT) an NVIDIA GPU with a compatible CUDA toolkit installed

### Windows

```bash
# 1. Clone the repository
git clone https://github.com/manvendrasingh0712/SARA-AI-Automation-Powered-Personal-Desktop-Assistant.git
cd SARA-AI-Automation-Powered-Personal-Desktop-Assistant

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install runtime dependencies
pip install -r requirements.txt

# 4. Pull the local LLM and embedding models
ollama pull qwen2.5
ollama pull nomic-embed-text

# 5. Create your own .env file (see Configuration below) and place the
#    Kokoro TTS model files under models/ (kokoro-v1.0.onnx, voices-v1.0.bin).
#    Note: this repo does not ship a .env.example — build your .env from
#    the reference table below.

# 6. Run Sara
python main.py
```

On first run, the **Setup Wizard** will check your Ollama install, LLM
model, embedding model, and TTS files, and offer one-click fixes for
anything missing.

### Linux / macOS

The core Python logic is cross-platform, but the system-automation layer
and several dependencies (`pycaw`, `comtypes`, `pywin32`,
`screen_brightness_control`, `keyboard`) are Windows-specific and not
currently supported on Linux or macOS. Cross-platform support is a
listed roadmap item.

---

## ⚙️ Configuration

All configuration is centralized in `config.py` and driven by environment
variables loaded via `python-dotenv`. Create a `.env` file in the project
root — **there is no `.env.example` checked into this repo by design**
(the maintainer keeps their own `.env` outside version control) — use
the reference table below to build one. Every setting has a built-in
default, and `Config.validate()` clamps every numeric setting into a
safe range automatically on import.

Two file paths are resolved relative to `config.py`'s own location (not
the process's working directory): `DB_PATH` (`sara_data.db`) and
`NOTES_FILE_PATH` (`sara_notes.txt`).

---

## ▶️ Running the Project

```bash
python main.py
```

This runs logging setup, then builds every core object (LLM, TTS, STT,
memory, reminders, vision, the Proactive Engine, and the skills
auto-discovery), starts the always-on conversation loop on a background
thread, and opens the desktop window.

> **Packaging note:** a PyInstaller-based standalone `.exe` build is a
> planned feature — `BUILD.md`, `sara_ai.spec`, and
> `requirements-build.txt` have been designed in past sessions but are
> **not yet actually present in this repo**. Don't expect
> `pyinstaller sara_ai.spec` to work until those files are actually
> added and confirmed.

---

## 🖼️ Screenshots

### Home
![Home Screen](./assets/screenshots/home.png)

### Chat
![Chat](./assets/screenshots/chat.png)

### Memory
![Memory](./assets/screenshots/memory.png)

### Automation / System
![Automation](./assets/screenshots/automation.png)

### Settings
![Settings](./assets/screenshots/settings.png)

### Reminders
![Reminders](./assets/screenshots/reminders.png)

---

## 💬 Usage Examples

Once running, activate Sara by saying her wake word (default: **"Sara"**
or **"Sarah"**) and speak naturally, or type a command in the Chat page.

```
"Sara, what's the weather in Ajmer?"
"Open Chrome"
"Set a timer for 10 minutes"
"Remind me to call mom at 6 pm"
"Take a note: buy groceries after work"
"Tell me a joke"
"What did I write in my notes about photosynthesis?"
"Give me my daily briefing"
"Kyu bola?"                     ← ask why Sara spoke up proactively
"Mera naam kya hai?"             ← recalled via long-term memory
"Lock my PC"
```

Fast, common commands (100+ patterns, plus anything shipped as a skill
in `sara/skills/`) are matched instantly with no LLM round-trip.
Anything that doesn't match falls through to the rule-based tool
router, and finally to a full conversational LLM reply — all
transparently, in the same voice loop. Separately, the Proactive Engine
may speak up on its own (battery low, an upcoming reminder, a long
idle break, a streak milestone) without you saying anything first.

---

## 📋 Configuration Reference

The tables below list every setting that actually exists in `config.py`,
grouped by subsystem, with its default value.

<details>
<summary><b>LLM Backend</b></summary>

| Variable | Default | Description |
|---|---|---|
| `LLM_BACKEND` | `ollama` | `ollama` or `gemini` |
| `OLLAMA_MODEL` | `qwen2.5` | Local chat model name |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_TIMEOUT` | `30` | Request timeout (s) |
| `OLLAMA_NUM_CTX` | `2048` | Context window (tokens) |
| `OLLAMA_SUMMARY_NUM_CTX` | `4096` | Context window for summarization calls |
| `OLLAMA_NUM_PREDICT` | `300` | Max tokens generated per reply |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long Ollama keeps the model loaded |
| `GEMINI_API_KEY` | *(empty)* | Required if `LLM_BACKEND=gemini` |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini chat model |
| `GEMINI_MAX_HISTORY_TOKENS` | `30000` | History trim budget |
| `LLM_MAX_RETRIES` | `2` | Retry attempts on failure |
| `LLM_RETRY_BASE_DELAY_S` | `1.5` | Base backoff delay |
| `LLM_RETRY_MAX_DELAY_S` | `8.0` | Max backoff delay |
| `LLM_WARMUP_WAIT_S` | `20.0` | Wait allowance for a cold-start local model |

</details>

<details>
<summary><b>Text-to-Speech (Kokoro ONNX)</b></summary>

| Variable | Default | Description |
|---|---|---|
| `KOKORO_MODEL_PATH` | `models/kokoro-v1.0.onnx` | Model weights path |
| `KOKORO_VOICES_PATH` | `models/voices-v1.0.bin` | Voice bank path |
| `KOKORO_USE_GPU` | `True` | Use CUDA execution provider if available |
| `CUDA_GPU_MEM_LIMIT_BYTES` | 3 GB | CUDA memory cap |
| `ORT_INTRA_THREADS` / `ORT_INTER_THREADS` | auto / `1` | ONNX Runtime threading |
| `KOKORO_VOICE_EN` / `KOKORO_LANG_EN` | `af_heart` / `en-us` | English voice |
| `KOKORO_VOICE_HI` / `KOKORO_LANG_HI` | `hf_alpha` / `hi` | Hindi voice |
| `KOKORO_SPEED` | `1.0` | Base speed (inherited by EN/HI unless overridden) |
| `KOKORO_SPEED_EN` / `KOKORO_SPEED_HI` | inherits `KOKORO_SPEED` | Per-language speed |
| `TTS_VOLUME` | `1.0` | Playback volume |
| `TTS_PLAYBACK_BUFFER_MS` | `40` | Output buffer size |
| `TTS_SD_LATENCY` | `low` | `sounddevice` latency mode |
| `TTS_WARMUP_WAIT_S` | `2.0` | Warm-up wait |
| `TTS_SYNTH_QUEUE_SIZE` / `TTS_PLAY_QUEUE_SIZE` | `12` / `6` | Pipeline queue sizes |
| `TTS_PHRASE_CACHE_SIZE` / `TTS_PHRASE_CACHE_MAXLEN` | `64` / `40` | Phrase-level synthesis cache |
| `TTS_BLEED_GUARD_MULTIPLIER` | `1.6` | Guard multiplier for TTS echo bleed detection |

</details>

<details>
<summary><b>Speech-to-Text (faster-whisper)</b></summary>

| Variable | Default | Description |
|---|---|---|
| `WHISPER_MODEL_SIZE` | `large-v3` | Whisper model size |
| `WHISPER_BEAM_SIZE` | `3` | Decoding beam size |
| `STT_NO_SPEECH_THRESHOLD` | `0.6` | No-speech probability threshold |
| `STT_LOG_PROB_THRESHOLD` | `-1.0` | Log-probability threshold |
| `STT_COMPRESSION_RATIO_THRESHOLD` | `2.4` | Hallucination heuristic |
| `STT_HALLUCINATION_MIN_REPEATS` | `3` | Repeats before flagged as hallucinated |
| `STT_LANGUAGE` | *(unset)* | Force a specific STT language |
| `STT_FORCE_LANG_FOR_HINGLISH` | `True` | Force Whisper to `hi` when `SARA_LANGUAGE` is Hindi/Hinglish |
| `STT_SETTLE_MIN_GAP_S` | `1.3` | Mic settle time after TTS stops |

</details>

<details>
<summary><b>Wake Word</b></summary>

| Variable | Default | Description |
|---|---|---|
| `WAKE_WORD` | `sara , sarah` | Primary wake word(s) |
| `WAKE_WORDS` | `sara,sarah,hey sara,hey sarah` | STT-fallback variant list |
| `WAKE_WORD_ALLOW_CUSTOM_ONLY` | `False` | Disable forced built-in variants |
| `WAKE_WORD_MODEL_PATH` | *(unset)* | Path to a custom-trained `openwakeword` model |
| `WAKE_WORD_COOLDOWN_S` | `2.0` | Re-trigger cooldown |
| `WAKE_WORD_THRESHOLD` | `0.5` | Detection confidence threshold |
| `WAKE_WORD_BEAM_SIZE` | `1` | Whisper beam size for wake detection |

</details>

<details>
<summary><b>Acoustic Echo Cancellation (AEC)</b></summary>

| Variable | Default | Description |
|---|---|---|
| `AEC_ENABLED` | `True` | Enable WebRTC APM echo cancellation |
| `AEC_SAMPLE_RATE` | `16000` | Must be 8000/16000/32000/48000 |
| `AEC_STREAM_DELAY_MS` | `80` | Speaker→mic round-trip estimate |
| `AEC_ENABLE_NS` | `True` | Noise suppression |
| `AEC_ENABLE_AGC` | `False` | Automatic gain control |
| `AEC_ENABLE_VAD` | `False` | APM's built-in VAD |

</details>

<details>
<summary><b>Barge-in, Continuous Mode & Assistant Identity</b></summary>

| Variable | Default | Description |
|---|---|---|
| `BARGE_IN_ENABLED` | `True` | Allow interrupting Sara mid-speech |
| `BARGE_IN_ENERGY_THRESHOLD` | `600` | Energy threshold to detect an interruption |
| `CONTINUOUS_MODE_TIMEOUT` | `180` | Idle seconds before returning to wake-word mode |
| `SARA_NAME` | `Sara` | Assistant's spoken name |
| `SARA_TIMEZONE` | `Asia/Kolkata` | Timezone for time-of-day prompt context |
| `SARA_LANGUAGE` | `hinglish` | `english` / `hindi` / `hinglish` |
| `LANG_DETECTION_MODE` | `auto` | `auto` or `manual` |
| `DEBUG_MODE` | `False` | Verbose debug logging |

</details>

<details>
<summary><b>Memory, RAG & Tool-Calling</b></summary>

| Variable | Default | Description |
|---|---|---|
| `MAX_MEMORY_EXCHANGES` | `6` | Short-term conversation window |
| `RAG_ENABLED` | `True` | Enable long-term semantic memory |
| `EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding model |
| `EMBEDDING_TIMEOUT_S` | `4.0` | Embedding call timeout |
| `RAG_TOP_K` | `4` | Memories retrieved per turn |
| `RAG_MIN_SIMILARITY` | `0.55` | Minimum similarity to include a memory |
| `RAG_MAX_IN_MEMORY` | `5000` | Max memory rows cached in RAM |
| `TOOL_CALLING_ENABLED` | `True` | Enable the rule-based tool-router fallback |
| `TOOL_CALLING_TIMEOUT_S` | `5.0` | Fallback resolution timeout |
| `VISION_MODEL` | `gemini-2.5-flash` | Gemini vision model for screen description |
| `REMINDER_CHECK_INTERVAL` | `5` | Reminder polling interval (s) |
| `DB_PATH` / `NOTES_FILE_PATH` | project-root-relative | Shared SQLite DB / notes file paths |

</details>

<details>
<summary><b>Proactive Engine & Skills</b></summary>

| Variable | Default | Description |
|---|---|---|
| `PROACTIVE_ENABLED` | `True` | Master on/off switch for proactive speaking |
| `PROACTIVE_CHECK_INTERVAL_S` | `60` | How often the background check runs |
| `PROACTIVE_BATTERY_LOW_PERCENT` | `15` | Battery-low trigger threshold (unplugged only) |
| `PROACTIVE_REMINDER_LEAD_MINUTES` | `15` | Heads-up lead time before a reminder is due |
| `PROACTIVE_IDLE_BREAK_MINUTES` | `90` | Idle time before a break nudge |
| `PROACTIVE_COOLDOWN_MINUTES` | `30` | Per-trigger cooldown |
| `PROACTIVE_LLM_PHRASING` | *(project default)* | Use the LLM to rephrase nudges, with template fallback |
| `DAILY_BRIEFING_LOCATION` | `Ajmer,IN` | Location used by the daily briefing skill |
| `NOTES_FOLDER` | `<project_root>/sara_class_notes` | Folder scanned by the notes Q&A skill |
| `NOTES_CHUNK_CHARS` | `800` | Chunk size for notes RAG indexing |
| `NOTES_QA_TOP_K` | `4` | Chunks retrieved per notes question |
| `NOTES_MAX_CHUNKS_PER_FILE` | `200` | Cap per file |
| `DIWALI_DATE` / `HOLI_DATE` | *(blank)* | Lunar-calendar festival dates — not safe to hardcode, left blank by default |

</details>

---

## 🚄 Performance

- Regex-based intent matching (100+ built-in patterns, plus anything
  registered by a skill) runs entirely locally before any network/LLM
  call is attempted
- Repeated identical commands are served from a cache without
  re-running regex matching
- TTS synthesis is cached at the phrase level so repeated short replies
  don't re-synthesize
- Conversation logging is fire-and-forget on a background thread, off
  the voice-loop hot path
- Heavy objects (LLM client, TTS/STT engines, vision) are constructed
  lazily in the background so the GUI can render before every model has
  finished loading
- SQLite runs in WAL mode, tuned for a single-writer, low-latency
  workload

---

## 🗺️ Roadmap

- [ ] Custom-trained wake-word model (currently relies on the
      STT-based fallback by default)
- [ ] Cross-platform support (currently Windows-only)
- [ ] Actually add the CI workflow and PyInstaller packaging files —
      designed multiple times, not yet committed to the repo (see
      `NEXT_STEPS.md`)
- [ ] Deeper automated test coverage for the pure-logic modules beyond
      the current smoke-test suite
- [ ] Real hardware/runtime validation of the skills auto-discovery
      system and the Setup Wizard (currently verified via stubbed
      imports and static analysis only)

---

## 🤝 Contributing

Contributions are welcome — this started as a solo portfolio project,
but issues, bug reports, and pull requests are encouraged.

1. Fork the repository and create a branch off `main`
2. Keep pull requests focused — one logical change each
3. Confirm `python -m py_compile` passes on any file you touch, and that
   `python main.py` still boots
4. New environment-configurable behavior should go through `config.py`,
   not be hardcoded
5. Follow the existing package-per-concern structure
   (`sara/audio/{stt,tts}/`, `sara/core/{llm,intent}/`,
   `sara/tools/system/`, `sara/gui/app/`, `sara/orchestrator/`,
   `sara/skills/`) rather than growing a single file into a monolith

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for the full guide.

---

## 📄 License

Released under the **MIT License** — see [`LICENSE`](./LICENSE) for the
full text.

```
Copyright (c) 2026 Manav
```

---

## 🙏 Acknowledgements

Built on top of these open-source projects and services:

- [Ollama](https://ollama.com) — local LLM serving
- [Google Gemini](https://ai.google.dev) (`google-genai`) — optional cloud LLM & vision backend
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — speech-to-text
- [Kokoro ONNX](https://github.com/thewh1teagle/kokoro-onnx) — text-to-speech
- [openwakeword](https://github.com/dscripka/openWakeWord) — wake-word detection
- [ONNX Runtime](https://onnxruntime.ai/) — TTS model inference (GPU/CPU)
- [WebRTC Audio Processing](https://webrtc.org/) (via `aec-audio-processing`) — acoustic echo cancellation
- [pywebview](https://pywebview.flowrl.com/) — native desktop GUI shell
- [pycaw](https://github.com/AndreMiras/pycaw) — Windows audio endpoint control
- [ddgs](https://github.com/deedy5/ddgs) / `duckduckgo_search` — web search
- [mss](https://python-mss.readthedocs.io/) — screenshot capture
- `numpy`, `sounddevice`, `PyAudio`, `pygame`, `webrtcvad`, `psutil`, `comtypes`, `keyboard`, `screen_brightness_control`, `dateparser`, `pyperclip`, `beautifulsoup4`, `requests`, `python-dotenv`, `Pillow`

---

<div align="center">

**Built by [Manvendra singh](mailto:manvendrasinghchauhan0712@gmail.com)**

</div>
