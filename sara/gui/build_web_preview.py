"""Create an isolated, static browser preview of the existing SARA GUI.

The output intentionally contains only the GUI bundle, local font assets, and
the preview-only bridge guardrails. It never copies backend code or user data.
"""
from __future__ import annotations

import argparse
import re
import shutil
import tempfile
from pathlib import Path

from sara.gui.build import is_stale

ROOT = Path(__file__).resolve().parents[2]
GUI = ROOT / "sara" / "gui"
DEFAULT_OUTPUT = ROOT / "deploy" / "sara-ai-preview"


def _copy_text(source: Path, destination: Path) -> None:
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")


def _populate(output: Path) -> list[Path]:
    if is_stale():
        raise RuntimeError("GUI bundle is stale; run python -m sara.gui.build first")
    output.mkdir()
    (output / "bundle").mkdir()
    (output / "fonts").mkdir()

    for name in ("app.css", "app.js"):
        _copy_text(GUI / "bundle" / name, output / "bundle" / name)
    _copy_text(GUI / "web_preview.css", output / "web_preview.css")
    _copy_text(GUI / "web_preview.js", output / "web_preview.js")
    for font in sorted((GUI / "fonts").glob("*.woff2")):
        shutil.copy2(font, output / "fonts" / font.name)
    license_file = GUI / "fonts" / "OFL-LICENSE.txt"
    if license_file.is_file():
        _copy_text(license_file, output / "fonts" / license_file.name)

    html = (GUI / "index.html").read_text(encoding="utf-8")
    html = html.replace(
        "<div class=\"offline-strip\" id=\"offlineStrip\" hidden>Preview mode — backend not connected (sample data)</div>",
        "<div class=\"offline-strip\" id=\"offlineStrip\" hidden>Web preview — desktop backend unavailable.</div>",
    )
    html = re.sub(
        r"<!-- The preview mock \(js/dev/\).*?-->",
        "<!-- Development mock APIs are excluded from this static preview. -->",
        html,
        flags=re.DOTALL,
    )
    stylesheet = re.compile(r'(<link rel="stylesheet" href="bundle/app\.css\?v=[^"]+">)')
    html, css_count = stylesheet.subn(
        r'\1\n<link rel="stylesheet" href="web_preview.css">', html, count=1
    )
    if css_count != 1:
        raise RuntimeError("Could not locate the generated GUI stylesheet link")
    html, head_count = re.subn(
        r"</head>",
        '<script>window.__SARA_WEB_PREVIEW__ = true;</script>\n</head>',
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if head_count != 1:
        raise RuntimeError("Could not add the isolated preview-mode flag")
    html, script_count = re.subn(
        r'(<script src="bundle/app\.js\?v=[^"]+"></script>)',
        r'\1\n<script src="web_preview.js"></script>',
        html,
        count=1,
    )
    if script_count != 1:
        raise RuntimeError("Could not locate the generated GUI script link")
    (output / "index.html").write_text(html, encoding="utf-8", newline="\n")
    return sorted(path for path in output.rglob("*") if path.is_file())


def _same_tree(left: Path, right: Path) -> bool:
    left_files = {path.relative_to(left) for path in left.rglob("*") if path.is_file()}
    right_files = {path.relative_to(right) for path in right.rglob("*") if path.is_file()}
    return left_files == right_files and all(
        (left / relative).read_bytes() == (right / relative).read_bytes()
        for relative in left_files
    )


def build(output: Path = DEFAULT_OUTPUT) -> list[Path]:
    """Build into an empty destination, or verify an existing build is identical.

    Existing deployment files are never overwritten. This makes repeated builds
    safe and catches stale output while preserving any local edits in that path.
    """
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".sara-preview-", dir=output.parent) as temporary:
        candidate = Path(temporary) / "preview"
        files = _populate(candidate)
        if output.exists():
            if not output.is_dir() or not _same_tree(output, candidate):
                raise FileExistsError(
                    f"Preview output exists and differs from generated files; "
                    f"choose a new output path: {output}"
                )
            return sorted(path for path in output.rglob("*") if path.is_file())
        candidate.replace(output)
    return sorted(output / path.relative_to(Path(temporary) / "preview") for path in files)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build an isolated SARA AI browser preview.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"new deployment directory (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    files = build(args.output)
    print(f"Verified isolated browser preview: {args.output.resolve()}")
    for path in files:
        print(f"  {path.relative_to(args.output.resolve())} ({path.stat().st_size} bytes)")
