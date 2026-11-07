# Build SARA AI for Windows

This guide builds the project as a PyInstaller **one-folder** distribution.
The checked-in [`sara_ai.spec`](./sara_ai.spec) is the source of truth for the
entry point, GUI assets, local model files, hidden imports, and native runtime
libraries. It fails early if a required model or GUI resource is missing.

## Prerequisites

- Windows x64 and Python 3.11.
- A virtual environment with the runtime and build dependencies installed.
- The three model files below already present in the checkout. The build does
  not download them:

  ```text
  models/kokoro-v1.0.onnx
  models/voices-v1.0.bin
  models/wakewords/hey_sara.onnx
  ```

- Microsoft Edge WebView2 Runtime installed on the target computer. Pywebview
  and its x64 WebView2 loader are packaged; the browser runtime is a Windows
  prerequisite.

## Install dependencies

Choose exactly one ONNX Runtime profile. The CPU profile is the portable
default. A GPU build packages the CUDA provider DLLs from its selected ONNX
Runtime wheel, but the CUDA and cuDNN runtime libraries must also be installed
on the target computer and available on `PATH`.

```powershell
# CPU build
python -m pip install -r requirements.txt -r requirements-cpu.txt -r requirements-build.txt
```

```powershell
# GPU build instead of the CPU build
python -m pip install -r requirements.txt -r requirements-gpu.txt -r requirements-build.txt
```

The GPU requirements file describes a known transitive ONNX Runtime conflict;
check the installed environment before packaging:

```powershell
python -c "import onnxruntime; print(onnxruntime.__version__, onnxruntime.get_available_providers())"
```

For a CPU build, the provider list should include `CPUExecutionProvider`. For
a GPU build, confirm `CUDAExecutionProvider` is present before creating the
distribution.

## Build

From the repository root, using the same virtual environment that has the
chosen runtime profile installed:

```powershell
python -m PyInstaller --noconfirm sara_ai.spec
```

The output is `dist/SaraAI/`, with `SaraAI.exe` as the GUI entry point. The
spec packages only the GUI bundle and its source inputs (so the app's startup
asset check can run), the local Kokoro and wake-word models, locally bundled
fonts, and runtime libraries collected from the selected environment. It does
not package `.env`, `credentials.json`, OAuth tokens, databases, logs, backups,
tests, benchmarks, or the repository's development caches. PyInstaller's
standard hooks collect the Windows GUI, Python.NET, audio, and numerical
dependencies; the spec explicitly collects ONNX Runtime, CTranslate2, and
PortAudio DLLs and includes dynamically discovered SARA skills.

The model files add roughly 354 MB to the distribution. Keep the generated
`build/` and `dist/` directories out of source control.

## Configure and launch

Secrets and per-user data stay outside the frozen application. Create a local
`.env` from `.env.example` and place it beside `SaraAI.exe`; do not put real
keys in the project or the distribution archive. Since `python-dotenv` looks
for `.env` from the process working directory when the app is frozen, launch
from the distribution directory:

```powershell
Set-Location .\dist\SaraAI
.\SaraAI.exe
```

For a Windows shortcut, set **Start in** to the `dist\SaraAI` directory. The
app stores writable state under `%LOCALAPPDATA%\SARA-AI`. Calendar OAuth
credentials and tokens are user-provided runtime files and must be configured
separately; never copy the repository's ignored `credentials.json` into a
release.

## Verify the distribution

Run these checks from the repository root before distributing:

```powershell
python -m PyInstaller --noconfirm sara_ai.spec
python -m pytest tests/test_gui_assets.py tests/test_env_example.py tests/test_secrets_hygiene.py -q
```

Then launch `dist\SaraAI\SaraAI.exe` from its directory and confirm that the
GUI opens, backend boot completes, and the log at
`%LOCALAPPDATA%\SARA-AI\logs\sara.log` has no startup traceback. The build
machine's test result does not replace a target-machine test with the
WebView2 Runtime, audio devices, and model execution providers installed.

## GPU and Windows runtime notes

- The spec does not sweep every DLL from the `nvidia` Python namespace into
  the output; this project environment can contain multiple gigabytes of
  CUDA libraries. A GPU distribution therefore depends on compatible CUDA
  12.x and cuDNN 9.x libraries installed on the target and available through
  `PATH`. Validate GPU execution on the target rather than assuming that a
  successful packaging run proves CUDA is available.
- If the GPU provider is unavailable, SARA's ONNX paths should use their
  configured CPU fallback where supported. Faster-Whisper/CTranslate2 GPU
  operation also requires compatible CUDA/cuDNN libraries.
- Pycaw uses COM through `comtypes`. COM wrapper-cache behavior can differ in
  a frozen, read-only install. If the first endpoint-volume operation fails,
  verify that its COM cache can be written under the user's profile before
  changing the application startup code.
- The target must have a compatible Microsoft Edge WebView2 Runtime. The
  spec includes pywebview's bridge assemblies and loader DLL, not the full
  browser runtime.
- UPX is disabled for the one-folder build to avoid compressing native audio,
  ONNX, and WebView binaries and to keep packaging behavior predictable.
