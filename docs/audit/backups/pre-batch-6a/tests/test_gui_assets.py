"""
Static tests for the GUI's asset wiring (index.html <link>/<script> lists).

Regression cover for Phase 1 audit finding D6 and its consequence:

  style/motion-pack.css was built to REPLACE animations.css, premium.css,
  luxe.css and aurum.css, carrying their rules as groups "ax", "pm", "lx" and
  "au", each re-scoped behind :where(html:not([data-fx-off~="<id>"])) so the
  Quality panel's FX fine-tune switches can turn them off.

  The consolidation was left half-finished: aurum.css was still linked, AFTER
  motion-pack.css. :where() contributes zero specificity, so aurum.css's
  unscoped copy of the same 166 rules won every conflict -- and the
  user-facing "Detail effects" toggle (js/quality.js FX registry, id "au")
  set data-fx-off="au" with no visible effect. luxe.css meanwhile sat on disk
  linked by nothing.

Linking a superseded stylesheet again would silently re-break its toggle, so
that specific mistake is asserted against by name.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GUI = _REPO_ROOT / "sara" / "gui"
_INDEX_HTML = _GUI / "index.html"

# Folded into style/motion-pack.css. Linking any of these again reintroduces an
# unscoped duplicate that overrides the data-fx-off gate for its group.
_SUPERSEDED_BY_MOTION_PACK = {
    "animations.css": "ax",
    "premium.css": "pm",
    "luxe.css": "lx",
    "aurum.css": "au",
}

# Loaded on demand by js/core.js (SARA._ensureMock), never statically.
_LAZY_JS = {"mock.js"}

# Superseded by motion-pack.css and already unlinked, but the files may still
# be sitting in sara/gui/style/ until someone deletes them. They are inert
# while nothing links them (and test_superseded_stylesheet_is_not_linked keeps
# it that way), so their mere presence should not fail the suite. Delete the
# files and drop them from this set to close the cleanup out.
_PENDING_DELETION = {"aurum.css", "luxe.css"}


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _linked_css() -> set[str]:
    return set(re.findall(r'href="style/([^"]+)"', _html()))


def _linked_js() -> set[str]:
    return set(re.findall(r'src="js/([^"]+)"', _html()))


def test_every_linked_stylesheet_exists() -> None:
    on_disk = {p.name for p in (_GUI / "style").glob("*.css")}
    missing = sorted(_linked_css() - on_disk)
    assert not missing, f"index.html links stylesheets that do not exist: {missing}"


def test_every_linked_script_exists() -> None:
    on_disk = {p.name for p in (_GUI / "js").glob("*.js")}
    missing = sorted(_linked_js() - on_disk)
    assert not missing, f"index.html loads scripts that do not exist: {missing}"


def test_no_dead_stylesheets_on_disk() -> None:
    on_disk = {p.name for p in (_GUI / "style").glob("*.css")}
    dead = sorted(on_disk - _linked_css() - _PENDING_DELETION)
    assert not dead, (
        f"stylesheets on disk that nothing loads: {dead}. Either link them or "
        f"delete them -- a dead stylesheet is a trap for the next reader."
    )


def test_no_dead_scripts_on_disk() -> None:
    on_disk = {p.name for p in (_GUI / "js").glob("*.js")}
    dead = sorted(on_disk - _linked_js() - _LAZY_JS)
    assert not dead, f"scripts on disk that nothing loads: {dead}"


@pytest.mark.parametrize("sheet, group", sorted(_SUPERSEDED_BY_MOTION_PACK.items()))
def test_superseded_stylesheet_is_not_linked(sheet: str, group: str) -> None:
    """
    These were folded into motion-pack.css. Re-linking one puts an UNSCOPED
    copy of its rules into the cascade, which overrides the
    :where(html:not([data-fx-off~="<group>"])) gate and silently kills that
    switch in the Quality panel.
    """
    assert sheet not in _linked_css(), (
        f"style/{sheet} is linked again; its rules live in motion-pack.css "
        f'GROUP "{group}". An unscoped duplicate disables the data-fx-off '
        f'"{group}" toggle.'
    )


def test_every_css_fx_switch_has_a_motion_pack_group() -> None:
    """
    Each 'css'-type entry in js/quality.js's FX registry gates a group inside
    motion-pack.css via <html data-fx-off="...">. A switch with no matching
    group is a control that cannot do anything.
    """
    quality = (_GUI / "js" / "quality.js").read_text(encoding="utf-8")
    motion_pack = (_GUI / "style" / "motion-pack.css").read_text(encoding="utf-8")

    css_ids = re.findall(r"\['(\w+)',\s*'[^']*',\s*'[^']*',\s*'css'\]", quality)
    assert css_ids, "could not parse the FX registry out of js/quality.js"

    groups = set(re.findall(r'GROUP "(\w+)"', motion_pack))
    orphans = sorted(set(css_ids) - groups)
    assert not orphans, (
        f"FX switches with no motion-pack.css group: {orphans}. "
        f"Groups present: {sorted(groups)}"
    )

    for fx_id in css_ids:
        assert f'data-fx-off~="{fx_id}"' in motion_pack, (
            f'FX switch "{fx_id}" has a GROUP header but no rule is actually '
            f'gated on data-fx-off~="{fx_id}"'
        )


def test_mock_js_is_lazy_not_linked() -> None:
    """Preview scaffolding must stay out of the static script list (see D2)."""
    assert "mock.js" not in _linked_js()
    assert (_GUI / "js" / "mock.js").exists(), "mock.js should still exist for preview use"
