# PyInstaller spec for the supported Windows one-folder build.
# Run from the repository root with: pyinstaller --noconfirm sara_ai.spec
from __future__ import annotations

import json
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


ROOT = Path(SPECPATH).resolve()
GUI_ROOT = ROOT / "sara" / "gui"


def required_file(relative_path: str) -> Path:
    path = ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(
            f"Required packaging input is missing: {path}\n"
            "Install the runtime requirements and place the documented model "
            "files under models/ before building."
        )
    return path


# GUI source files are included because bootstrap calls sara.gui.build.ensure_built()
# at launch. That helper rebuilds bundle/app.js and app.css from assets.json.
# The manifest deliberately excludes js/dev and js/tests.
asset_manifest_path = required_file("sara/gui/assets.json")
asset_manifest = json.loads(asset_manifest_path.read_text(encoding="utf-8"))
datas = [(str(asset_manifest_path), "sara/gui")]
datas.append((str(required_file("sara/gui/index.html")), "sara/gui"))
datas.append((str(required_file("sara/gui/build.py")), "sara/gui"))
datas.extend(
    [
        (str(required_file(f"sara/gui/{relative_path}")), f"sara/gui/{Path(relative_path).parent.as_posix()}")
        for kind in ("css", "js")
        for relative_path in asset_manifest[kind]
    ]
)
datas.extend(
    [
        (str(required_file(f"sara/gui/bundle/{name}")), "sara/gui/bundle")
        for name in ("app.css", "app.js")
    ]
)
datas.extend(
    [
        (str(required_file(f"sara/gui/fonts/{name}")), "sara/gui/fonts")
        for name in (
            "instrument-sans-latin-400-normal.woff2",
            "instrument-sans-latin-500-normal.woff2",
            "instrument-sans-latin-600-normal.woff2",
            "OFL-LICENSE.txt",
        )
    ]
)

# These files are read-only runtime resources resolved relative to PyInstaller's
# resource root by config.resource_root(). They are intentionally bundled from
# the local checkout; the build never downloads models.
datas.extend(
    [
        (str(required_file("models/kokoro-v1.0.onnx")), "models"),
        (str(required_file("models/voices-v1.0.bin")), "models"),
        (str(required_file("models/wakewords/hey_sara.onnx")), "models/wakewords"),
    ]
)

# openwakeword 0.6.0 does not include its ONNX feature models in the wheel.
# Bundle the exact local resources required by the custom ONNX wake detector.
datas.extend(
    [
        (str(required_file(f"packaging_resources/openwakeword/{name}")), "packaging_resources/openwakeword")
        for name in ("melspectrogram.onnx", "embedding_model.onnx", "LICENSE")
    ]
)
datas.extend(collect_data_files("kokoro_onnx", includes=["config.json"]))

# Collect the native libraries that are part of the selected Python wheels.
# CUDA toolkit/cuDNN DLLs are deliberately not swept in: the GPU environment
# contains multi-gigabyte vendor packages, and the project documents the GPU
# runtime as a machine prerequisite. The ONNX Runtime CPU or CUDA provider DLLs
# that ship inside the selected onnxruntime wheel are included here.
binaries = []
for package in ("onnxruntime", "ctranslate2", "_sounddevice_data"):
    binaries.extend(collect_dynamic_libs(package))

# Some imports are selected by string or are reached only through optional
# feature paths, so Analysis cannot discover them from the normal import graph.
skill_hiddenimports = sorted(
    f"sara.skills.{path.stem}"
    for path in (ROOT / "sara" / "skills").glob("*.py")
    if path.name != "__init__.py"
)
hiddenimports = skill_hiddenimports + [
    "comtypes.client",
    "google.genai",
    "google.genai.types",
    "google_auth_oauthlib.flow",
    "googleapiclient.discovery",
    "onnxruntime.capi.onnxruntime_pybind11_state",
    "onnxruntime.capi._pybind_state",
    "sara.core.memory2.decay",
    "sara.core.memory2.retrieve",
    "sara.core.router",
    "sara.core.telemetry",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "win32api",
    "win32con",
    "win32crypt",
    "win32event",
    "win32gui",
    "win32job",
    "win32process",
    "winerror",
    "winsdk.windows.devices.radios",
    "winsdk.windows.media.control",
    "winsdk.windows.storage.streams",
]

a = Analysis(
    [str(required_file("main.py"))],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["bench", "tests", "pytest", "_pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SaraAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SaraAI",
)
