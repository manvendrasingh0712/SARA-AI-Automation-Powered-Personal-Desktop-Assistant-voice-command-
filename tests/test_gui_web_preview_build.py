from __future__ import annotations

import re
from pathlib import Path

import pytest

from sara.gui.build_web_preview import build


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_preview_build_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    build(first)
    build(second)

    expected = _tree_bytes(first)
    assert expected == _tree_bytes(second)
    assert build(first) == sorted(path for path in first.rglob("*") if path.is_file())
    assert set(expected) == {
        "index.html",
        "bundle/app.css",
        "bundle/app.js",
        "web_preview.css",
        "web_preview.js",
        "fonts/OFL-LICENSE.txt",
        "fonts/instrument-sans-latin-400-normal.woff2",
        "fonts/instrument-sans-latin-500-normal.woff2",
        "fonts/instrument-sans-latin-600-normal.woff2",
    }


def test_preview_excludes_mock_and_backend_resources(tmp_path: Path) -> None:
    output = tmp_path / "preview"
    build(output)
    html = (output / "index.html").read_text(encoding="utf-8")

    assert "window.__SARA_WEB_PREVIEW__ = true" in html
    assert "Web preview — desktop backend unavailable." in html
    assert "sample data" not in html
    assert "js/dev/mock.js" not in html
    assert "<script src=\"web_preview.js\"></script>" in html
    assert {"home", "chat", "notes", "apps", "automation", "settings"} <= set(
        re.findall(r'data-page="([^"]+)"', html)
    )
    for relative in (
        "index.html",
        "bundle/app.css",
        "bundle/app.js",
        "web_preview.css",
        "web_preview.js",
        "fonts/OFL-LICENSE.txt",
    ):
        assert b"\r\n" not in (output / relative).read_bytes()


def test_conflicting_existing_output_is_preserved(tmp_path: Path) -> None:
    output = tmp_path / "preview"
    output.mkdir()
    marker = output / "keep-me.txt"
    marker.write_text("user data", encoding="utf-8")

    with pytest.raises(FileExistsError, match="choose a new output path"):
        build(output)

    assert marker.read_text(encoding="utf-8") == "user data"
    assert sorted(path.name for path in output.iterdir()) == ["keep-me.txt"]
