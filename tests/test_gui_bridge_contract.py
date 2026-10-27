"""
Static contract tests for the Python <-> JavaScript pywebview bridge.

These are deliberately static (no browser, no pywebview, no Windows), so they
run everywhere CI runs and catch the whole class of defect found in the Phase 1
audit:

  D3  sara/gui/js/music.js called SARA.callApi('toggle_media_mute'), but the
      Python side implements toggle_session_mute(app_id) / toggle_master_mute().
      Nothing failed loudly -- js/core.js fell through to the preview mock,
      which answered ok({muted: ...}), so the Music page's mute button flipped
      its icon while nothing was muted.

  D2  js/mock.js was in index.html's script list, so that preview scaffolding
      was live in the packaged app and could answer a call meant for Python.

test_every_frontend_api_name_exists_in_python is the regression guard: any
future rename on either side of the bridge fails CI instead of silently
degrading a control into fabricated data.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GUI_APP = _REPO_ROOT / "sara" / "gui" / "app"
_GUI_JS = _REPO_ROOT / "sara" / "gui" / "js"
_INDEX_HTML = _REPO_ROOT / "sara" / "gui" / "index.html"

# Names reaching Python go through js/core.js's SARA.callApi. Three call shapes
# exist in the codebase and all three must be picked up, or the guard has holes:
#   SARA.callApi('name', ...)            -- the common case
#   origCall.call(SARA, 'name', ...)     -- the -plus interceptor files
#   api('name', ...)                     -- memory-view.js's local forwarder
_CALL_RE = re.compile(
    r"""(?:SARA\.)?(?:callApi|origCall|api)\s*"""
    r"""(?:\.(?:call|apply)\s*\(\s*SARA\s*,|\()\s*"""
    r"""['"]([a-z_][a-z0-9_]*)['"]""",
    re.VERBOSE,
)


def _python_api_surface() -> dict[str, str]:
    """Every public method on the Api*Mixin classes, name -> 'file:line'."""
    surface: dict[str, str] = {}
    for path in sorted(_GUI_APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ClassDef) and node.name.startswith("Api")):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not item.name.startswith("_"):
                        rel = path.relative_to(_REPO_ROOT).as_posix()
                        surface[item.name] = f"{rel}:{item.lineno}"
    return surface


def _frontend_api_calls() -> dict[str, list[str]]:
    """Every backend name the frontend asks for, name -> ['file:line', ...]."""
    calls: dict[str, list[str]] = {}
    for path in sorted(_GUI_JS.rglob("*.js")):
        if "tests" in path.parts:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in _CALL_RE.finditer(line):
                rel = path.relative_to(_REPO_ROOT).as_posix()
                calls.setdefault(match.group(1), []).append(f"{rel}:{lineno}")
    return calls


def test_python_api_surface_is_discoverable() -> None:
    """Guard the guard: if this breaks, the other tests pass vacuously."""
    surface = _python_api_surface()
    assert len(surface) > 50, f"expected a large Api surface, found {len(surface)}"
    for expected in ("send_text_command", "get_system_stats", "toggle_master_mute"):
        assert expected in surface, f"{expected} missing from the discovered Api surface"


def test_frontend_api_calls_are_discoverable() -> None:
    """Guard the guard, frontend side."""
    calls = _frontend_api_calls()
    assert len(calls) > 50, f"expected many callApi names, found {len(calls)}"
    assert "send_text_command" in calls


def test_every_frontend_api_name_exists_in_python() -> None:
    """
    Every name the GUI asks the bridge for must be implemented in Python.

    A name here that Python does not implement is a dead control: with the
    post-D2 core.js it now returns {ok: false, error: 'unknown_method'} and
    logs, but the control is still broken for the user.
    """
    surface = _python_api_surface()
    calls = _frontend_api_calls()

    missing = {name: sites for name, sites in calls.items() if name not in surface}
    assert not missing, "frontend calls backend methods that do not exist:\n" + "\n".join(
        f"  {name!r} called from {', '.join(sites)}" for name, sites in sorted(missing.items())
    )


def test_mock_is_not_loaded_in_production_html() -> None:
    """
    js/mock.js is preview-only scaffolding. It must not be in index.html's
    script list: js/core.js fetches it on demand only when no bridge exists.
    """
    html = _INDEX_HTML.read_text(encoding="utf-8")
    assert '<script src="js/mock.js"></script>' not in html, (
        "index.html statically loads js/mock.js -- preview scaffolding would be "
        "live in the packaged app and could answer calls meant for Python"
    )
    assert '<script src="js/core.js"></script>' in html, "index.html must still load js/core.js"


def test_mock_unknown_name_is_an_explicit_failure() -> None:
    """mock.js's default branch must not fabricate ok() for unknown names."""
    mock = (_GUI_JS / "mock.js").read_text(encoding="utf-8")
    assert not re.search(r"default:\s*return ok\(\);", mock), (
        "mock.js still has `default: return ok();` -- an unimplemented backend "
        "method would look like a successful call"
    )
    assert "unknown_method" in mock, "mock.js default branch should return an unknown_method failure"


def test_core_js_fails_loudly_on_a_connected_bridge() -> None:
    """
    A connected bridge missing the requested name must produce an explicit
    failure rather than falling through to the preview mock.
    """
    core = (_GUI_JS / "core.js").read_text(encoding="utf-8")
    assert "unknown_method" in core, "core.js should return an unknown_method failure"
    assert "_ensureMock" in core, "core.js should load the preview mock on demand"
    assert "apiIsBound" in core, (
        "core.js must distinguish an empty (still-binding) pywebview.api from a "
        "bound one, or a startup race is misreported as a missing method"
    )


@pytest.mark.parametrize(
    "name, caller",
    [("toggle_media_mute", "sara/gui/js/music.js")],
)
def test_audited_dead_controls_stay_wired(name: str, caller: str) -> None:
    """
    Named regression guard for controls the Phase 1 audit proved dead.

    D3: the Music page's mute button called toggle_media_mute, which no mixin
    implemented. The frontend call site was correct -- the Python method was
    missing -- so the repair added the method rather than changing the caller.
    Both halves are asserted here so neither can quietly disappear again.
    """
    surface = _python_api_surface()
    calls = _frontend_api_calls()

    assert name in surface, f"{name!r} must stay implemented on the Api"
    sites = calls.get(name, [])
    assert any(site.startswith(caller) for site in sites), (
        f"{name!r} should still be called from {caller}; found: {sites}"
    )


def test_media_mute_methods_are_distinct() -> None:
    """
    The three mute paths act on different targets and must not be collapsed:
    the selected session, one named app, and the Windows output device.
    """
    surface = _python_api_surface()
    for name in ("toggle_media_mute", "toggle_session_mute", "toggle_master_mute"):
        assert name in surface, f"{name} missing from the Api surface"
