# PyInstaller one-folder build for Windows. Keep runtime models and secrets
# outside the application bundle; only the small openwakeword preprocessing
# models required by the installed package are embedded.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).resolve()
GUI = ROOT / "sara" / "gui"
OWW_RESOURCES = ROOT / "packaging_resources" / "openwakeword"

required_files = [
    GUI / "index.html",
    GUI / "bundle" / "app.css",
    GUI / "bundle" / "app.js",
    GUI / "fonts" / "instrument-sans-latin-400-normal.woff2",
    GUI / "fonts" / "instrument-sans-latin-500-normal.woff2",
    GUI / "fonts" / "instrument-sans-latin-600-normal.woff2",
    GUI / "fonts" / "OFL-LICENSE.txt",
    OWW_RESOURCES / "melspectrogram.onnx",
    OWW_RESOURCES / "embedding_model.onnx",
    OWW_RESOURCES / "LICENSE",
]
missing_files = [str(path.relative_to(ROOT)) for path in required_files if not path.is_file()]
if missing_files:
    raise FileNotFoundError("Required packaging files are missing: " + ", ".join(missing_files))

datas = [
    (str(GUI / "index.html"), "sara/gui"),
    (str(GUI / "bundle" / "app.css"), "sara/gui/bundle"),
    (str(GUI / "bundle" / "app.js"), "sara/gui/bundle"),
    (str(GUI / "fonts" / "instrument-sans-latin-400-normal.woff2"), "sara/gui/fonts"),
    (str(GUI / "fonts" / "instrument-sans-latin-500-normal.woff2"), "sara/gui/fonts"),
    (str(GUI / "fonts" / "instrument-sans-latin-600-normal.woff2"), "sara/gui/fonts"),
    (str(GUI / "fonts" / "OFL-LICENSE.txt"), "sara/gui/fonts"),
    (str(OWW_RESOURCES / "melspectrogram.onnx"), "openwakeword/resources/models"),
    (str(OWW_RESOURCES / "embedding_model.onnx"), "openwakeword/resources/models"),
    (str(OWW_RESOURCES / "LICENSE"), "openwakeword/resources/models"),
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("sara.skills"),
    hookspath=[str(ROOT / "packaging_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_cov", "IPython", "jupyter"],
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
    console=True,
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
    upx=False,
    name="SaraAI",
)
