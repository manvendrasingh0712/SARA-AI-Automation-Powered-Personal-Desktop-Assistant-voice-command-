# Contributing to Sara AI

Thanks for considering a contribution. This is a solo portfolio project,
but issues, bug reports, and pull requests are welcome.

## Reporting bugs

Open an issue using the **Bug report** template. Include:

- Your OS/Python version and whether you're running from source or a
  built `.exe`
- Relevant lines from `logs/` (see `logging_config.py` — logs rotate
  under `logs/`)
- Steps to reproduce

## Suggesting features

Open an issue using the **Feature request** template. Since the project
prioritizes staying fast/free/low-latency (see [README](./README.md)),
please note if your suggestion has a paid-API or heavy-dependency
tradeoff.

## Development setup

```bash
git clone https://github.com/<your-username>/sara-ai.git
cd sara-ai
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

See the main [README](./README.md) for full setup, and
[BUILD.md](./BUILD.md) if you're working on the PyInstaller packaging.

## Project structure

Each subsystem (audio, LLM, GUI, system tools, orchestration) lives in
its own small package under `sara/` — see the **Architecture** section
in the [README](./README.md) for the full breakdown before adding new
code, so new functionality lands in the right file/module rather than
growing an existing one back into a monolith.

## Pull requests

1. Fork the repo and create a branch off `main`
2. Keep changes focused — one logical change per PR
3. Make sure `python -m py_compile` passes on any file you touch, and
   that the app still boots (`python main.py`) before opening the PR
4. Describe what changed and why in the PR description; link the issue
   it resolves, if any

## Code style

- Keep functions/files scoped to one concern — the existing package
  split (e.g. `sara/audio/tts/`, `sara/tools/system/`) is the pattern to
  follow for new code, not a one-off restructure
- Prefer explicit, readable code over cleverness; this project has a
  history of production bugs traced to overly-terse one-liners (see
  `CHANGELOG.md` for examples) — favor clarity
- New environment-configurable behavior goes through `config.py` and
  gets documented in `.env.example`, not hardcoded
