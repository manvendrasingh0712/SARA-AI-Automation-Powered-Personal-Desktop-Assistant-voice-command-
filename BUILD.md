# Building Sara AI as a standalone Windows `.exe`

This guide covers packaging Sara AI with [PyInstaller](https://pyinstaller.org/)
into a distributable Windows executable. It assumes you can already run
the project from source (see the main [README](./README.md) "Getting
Started" section first).

---

## 1. Prerequisites

- Everything in `requirements.txt` installed, and the app running
  successfully via `python main.py`
- The Kokoro TTS model files present under `models/`
  (`kokoro-v1.0.onnx`, `voices-v1.0.bin`)
- A working `.env` (secrets aren't bundled into the exe — see step 4)

## 2. Install build-only dependencies

```bash
pip install -r requirements-build.txt
```

This adds `pyinstaller` and `pywin32` on top of your existing runtime
environment. It does **not** replace `requirements.txt` — both must be
installed.

## 3. Run PyInstaller

```bash
pyinstaller sara_ai.spec
```

This produces `dist/SaraAI/SaraAI.exe` (a one-folder build — faster
startup and easier debugging than a one-file build, at the cost of a
larger distributable folder). `sara_ai.spec` already bundles the GUI's
HTML/JS/CSS assets and the hidden imports PyInstaller's static analysis
tends to miss (`pywin32`, `pycaw`/`comtypes`, `onnxruntime`'s execution
provider plugins, `ctranslate2`, `google-genai`, `kokoro_onnx`).

## 4. Copy runtime files next to the built exe

The following are **not** bundled inside the exe by design, so they can
be updated without a full rebuild:

```
dist/SaraAI/
├── SaraAI.exe
├── .env                  ← copy your real .env here (never commit it)
└── models/
    ├── kokoro-v1.0.onnx
    └── voices-v1.0.bin
```

`.env.example` is bundled as a reference, but a real `.env` with your
actual `GEMINI_API_KEY` / `WEATHER_API_KEY` (if used) needs to sit next
to the exe.

## 5. Verify the build

Before distributing, run `dist/SaraAI/SaraAI.exe` directly (not through
`pyinstaller`'s own terminal) and check:

- The window opens and the boot sequence completes (no "index.html not
  found" error — if you see this, double-check `sara_ai.spec`'s `datas`
  list matched your actual `sara/gui/` folder layout)
- The wake word triggers and a basic voice round-trip works
- `health_check.py`'s pre-flight diagnostics don't report a missing
  model/backend (check the log file under `logs/`)

---

## Known rough edges

### CUDA execution providers

`onnxruntime-gpu` looks for CUDA/cuDNN DLLs at runtime via Windows'
standard DLL search path. PyInstaller does **not** automatically bundle
CUDA toolkit DLLs (they're not Python packages). Two options:

1. **Simplest**: require end users to have the NVIDIA CUDA Toolkit
   installed separately (matching the CUDA version `onnxruntime-gpu`
   was built against). This is what the current spec assumes.
2. **Fully self-contained**: manually copy `cudart64_*.dll`,
   `cudnn64_*.dll`, etc. from your CUDA installation into the `binaries`
   list in `sara_ai.spec`, then re-test on a machine *without* CUDA
   installed to confirm it's truly self-contained. This significantly
   increases the distributable's size.

If GPU execution isn't available at runtime, the app should fall back
to `CPUExecutionProvider` automatically (`onnxruntime.get_available_providers()`
degrades gracefully) — verify this fallback actually happens rather than
crashing, since it's easy for a frozen build to hit a DLL-not-found error
instead of a clean provider-unavailable fallback.

### `comtypes` caching in a frozen build

`comtypes` (used by `pycaw` for Windows audio-endpoint control) generates
and caches Python wrapper modules for COM interfaces on first use,
normally under a writable `gen/` cache directory next to the `comtypes`
package. Inside a frozen PyInstaller build, that directory may be
read-only or not exist, which can cause the first volume-control call to
fail silently or raise on some machines. If you hit this:

- Confirm `comtypes.gen` is included in `hiddenimports` (already is, in
  `sara_ai.spec`)
- If the error persists, set `comtypes.client.gen_dir` to a writable
  path (e.g. inside `%LOCALAPPDATA%`) early in `main.py`, before any
  `pycaw` call — this isn't currently wired in, so add it if you hit
  this specific failure mode.

### Antivirus false positives

PyInstaller-built executables (especially with UPX compression enabled,
as this spec does) are commonly flagged by antivirus heuristics as
suspicious, purely because that packing signature is also used by some
malware. This is a known PyInstaller ecosystem issue, not a Sara AI bug.
If it's a problem for your distribution, set `upx=False` in
`sara_ai.spec` and/or code-sign the executable.

---

## One-file vs one-folder builds

`sara_ai.spec` currently produces a one-folder build. Switching to a
single-file exe is possible by moving to `COLLECT`-less packaging (i.e.
passing `a.binaries`, `a.zipfiles`, `a.datas` directly into `EXE(...)`
with no separate `COLLECT` step), but startup becomes noticeably slower
(everything is unpacked to a temp directory on every launch) and the
`models/` folder / `.env` file placement described in step 4 becomes
less obvious to end users, since there's no visible folder to drop them
into. The current one-folder approach is the recommended default for
this project.
