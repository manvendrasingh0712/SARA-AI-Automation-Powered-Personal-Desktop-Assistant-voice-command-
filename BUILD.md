# Building SARA AI for Windows

This guide builds the Windows one-folder application with PyInstaller. The
specification and its hooks/resources are tracked source inputs; do not build
from a checkout that omits `sara_ai.spec`, `packaging_hooks/`, or
`packaging_resources/`.

## Requirements

- Windows x64 and Python 3.11.
- Install the locked CPU runtime and build dependencies in a clean environment:

```powershell
py -3.11 -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1
python -m pip install -r requirements-build.lock.txt
python -m pip check
```

`requirements-build.lock.txt` is the complete Python 3.11 Windows CPU runtime
plus PyInstaller build profile. `requirements-cpu.lock.txt` is the runtime-only
profile. The GPU profile is currently unsupported: Kokoro, faster-whisper and
OpenWakeWord require the `onnxruntime` distribution, while the GPU wheel is
published as `onnxruntime-gpu`. pip installs both into the same package path;
installing only the GPU wheel leaves declared dependencies unsatisfied. Do not
combine `requirements-gpu.txt` with the runtime requirements.

The locks pin the complete resolved package versions. To update them, review
the direct requirement changes, then in a disposable Python 3.11 Windows
environment install the pinned lock generator and run:

```powershell
python -m pip install -r requirements-lock-tools.txt
pip-compile --resolver=backtracking --allow-unsafe --output-file=requirements-cpu.lock.txt requirements.txt requirements-cpu.txt
pip-compile --resolver=backtracking --allow-unsafe --output-file=requirements-build.lock.txt requirements.txt requirements-cpu.txt requirements-build.txt
pip-compile --resolver=backtracking --allow-unsafe --output-file=requirements-dev.lock.txt requirements.txt requirements-cpu.txt requirements-dev.txt
```

Review lock diffs and validate each profile with `python -m pip check` before
releasing. Never install both CPU and GPU ONNX Runtime distributions in one
environment.

## Check and build

The generated GUI bundle is committed. Verify it matches its source before
packaging:

```powershell
py -3.11 -m sara.gui.build --check
```

Use a unique temporary output directory so existing `build/` and `dist/`
artifacts are left untouched:

```powershell
$out = Join-Path $env:TEMP ("SaraAI-build-" + [guid]::NewGuid().ToString("N"))
py -3.11 -m PyInstaller --clean --noconfirm --distpath (Join-Path $out "dist") --workpath (Join-Path $out "work") sara_ai.spec
```

The executable is `$out\dist\SaraAI\SaraAI.exe`. This is an onedir build;
the Python application, GUI, fonts and required small openwakeword preprocessing
models are placed beside the executable in the generated directory.

## Runtime profiles and external configuration

The tested CPU profile uses `onnxruntime==1.21.0`. GPU inference is not a
supported install profile until the upstream audio packages can depend on the
GPU distribution without also pulling the CPU distribution. The intended ORT
1.21 GPU provider requires compatible NVIDIA drivers, CUDA 12 and cuDNN 9; a
successful package resolution alone would not verify hardware inference.

Secrets and user data are never included in the executable. Create a private
`.env` beside the executable (or set the environment variables before launch)
for the selected LLM provider and other local settings. Do not copy
`credentials.json`, OAuth tokens, databases, logs, or a development `.env`
into a release.

Kokoro voice models are large runtime assets and are intentionally external:
place `kokoro-v1.0.onnx` and `voices-v1.0.bin` in a `models` directory beside
the executable, or set `KOKORO_MODEL_PATH` and `KOKORO_VOICES_PATH` to their
locations. Relative model paths resolve from the repository root in source
mode and from the executable directory in a frozen build, not from the
current working directory. Absolute paths are also supported, including
paths containing spaces.

The small OpenWakeWord ONNX preprocessing assets (`melspectrogram.onnx` and
`embedding_model.onnx`) are bundled at the package resource path expected by
OpenWakeWord. The custom wake-word model (`hey_sara.onnx`) is a separate,
optional model and is not supplied by OpenWakeWord or this project. Place a
compatible custom ONNX model at `models/wakewords/hey_sara.onnx` beside the
executable, or set `WAKE_WORD_MODEL_PATH` to its absolute path. If it is
missing, passive wake detection remains disabled; SARA reports the missing
path and setup status exposes that the model is absent. Installing a custom
model is not required to build or launch SARA AI. None of these model files
are copied from the developer's ignored `models/` directory.

The target machine needs the Microsoft Edge WebView2 Runtime for the GUI.
The documented CPU build does not require NVIDIA drivers or CUDA/cuDNN. A
compatible, supported GPU dependency profile is not currently available; the
requirements file records the upstream package-name conflict.

## Runtime check

Launch `SaraAI.exe` from the generated folder and inspect the console output
for startup errors. Confirm `sara/gui/index.html` loads, the setup status is
available, and the expected model files are detected. A completed PyInstaller
build alone does not verify audio devices, model inference, OAuth services, or
optional GPU execution on the target machine.
