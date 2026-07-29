# -*- mode: python ; coding: utf-8 -*-
# sara_ai.spec — PyInstaller build spec for SaraAI (Windows, one-folder build)
#
# Build with:
#     pyinstaller sara_ai.spec
#
# Produces: dist/SaraAI/SaraAI.exe
#
# After building, per BUILD.md step 4, manually copy next to the exe:
#     - your real .env (never bundled — see below)
#     - models/kokoro-v1.0.onnx
#     - models/voices-v1.0.bin
# These are intentionally NOT collected into `datas` below, so they can
# be swapped/updated without a full rebuild.

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

PROJECT_ROOT = os.path.abspath(".")

# ---------------------------------------------------------------------
# Hidden imports
# These packages (onnxruntime execution providers, faster-whisper's
# ctranslate2 backend, kokoro_onnx, google-genai, pywin32/pycaw for
# Windows audio-endpoint control) all rely on dynamic/plugin-style
# imports that PyInstaller's static analysis misses.
# ---------------------------------------------------------------------
hiddenimports = []
hiddenimports += collect_submodules("onnxruntime")
hiddenimports += collect_submodules("kokoro_onnx")
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += collect_submodules("ctranslate2")
hiddenimports += collect_submodules("google.genai")
hiddenimports += [
    # pywin32
    "win32api", "win32con", "win32gui", "win32com", "win32com.client",
    "win32comext", "win32comext.shell", "pythoncom", "pywintypes",
    # pycaw / comtypes (Windows volume control) — comtypes.gen is the
    # wrapper-module cache; see BUILD.md's note on comtypes caching in a
    # frozen build if the first volume-control call fails at runtime.
    "pycaw", "pycaw.pycaw", "comtypes", "comtypes.gen", "comtypes.stream",
    "comtypes.client",
]

# ---------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------
datas = []
datas += collect_data_files("onnxruntime")
datas += collect_data_files("kokoro_onnx")
datas += collect_data_files("faster_whisper")

# GUI HTML/JS/CSS assets — the pywebview window loads these at a
# relative path, so they must live inside the frozen bundle.
datas += [(os.path.join(PROJECT_ROOT, "sara", "gui"), "sara/gui")]

# App icons / static assets folder.
datas += [(os.path.join(PROJECT_ROOT, "assets"), "assets")]

# Reference-only template, real .env is copied in manually post-build.
datas += [(os.path.join(PROJECT_ROOT, ".env.example"), ".")]

# NOTE: models/ (kokoro-v1.0.onnx, voices-v1.0.bin) is deliberately NOT
# added here — see BUILD.md step 4.

a = Analysis(
    ["main.py"],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # one-folder build, binaries go via COLLECT
    name="SaraAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                # set False if AV false-positives are an issue (see BUILD.md)
    console=False,           # windowed app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SaraAI",
)