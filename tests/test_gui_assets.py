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

import json
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

# Superseded stylesheets still sitting in sara/gui/style/ awaiting deletion.
# They are inert while nothing links them (test_superseded_stylesheet_is_not_linked
# keeps it that way), so their presence alone should not fail the suite.
# aurum.css and luxe.css were deleted from the working tree on 2026-10-27, so
# this set is now empty and test_no_dead_stylesheets_on_disk enforces the
# invariant with no exemptions.
_PENDING_DELETION: set[str] = set()


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads((_GUI / "assets.json").read_text(encoding="utf-8"))


def _linked_css() -> set[str]:
    """File names that the bundle loads (assets.json), e.g. "motion-pack.css"."""
    return {Path(p).name for p in _manifest()["css"]}


def _linked_js() -> set[str]:
    return {Path(p).name for p in _manifest()["js"]}


def _css_on_disk() -> set[str]:
    return {p.name for p in (_GUI / "style").rglob("*.css")}


def _js_on_disk() -> set[str]:
    return {
        p.name
        for p in (_GUI / "js").rglob("*.js")
        if "tests" not in p.parts
    }


def test_index_html_loads_only_the_bundle() -> None:
    html = _html()
    assert re.findall(r'href="(style/[^"]+)"', html) == []
    assert re.findall(r'src="(js/[^"]+)"', html) == []
    assert re.search(r'href="bundle/app\.css\?v=[0-9a-f]+"', html) and re.search(r'src="bundle/app\.js\?v=[0-9a-f]+"', html)


def test_bundle_is_up_to_date() -> None:
    """Sources changed without `python -m sara.gui.build` => the app would run old code."""
    from sara.gui.build import is_stale

    assert is_stale() == [], "run: python -m sara.gui.build"


def test_manifest_files_exist_and_are_unique() -> None:
    man = _manifest()
    for kind in ("css", "js"):
        assert len(man[kind]) == len(set(man[kind])), f"duplicate {kind} entry"
        for rel in man[kind]:
            assert (_GUI / rel).is_file(), f"assets.json lists a missing file: {rel}"


def test_main_js_is_the_last_script() -> None:
    assert _manifest()["js"][-1].endswith("/main.js")


def test_every_linked_stylesheet_exists() -> None:
    on_disk = _css_on_disk()
    missing = sorted(_linked_css() - on_disk)
    assert not missing, f"index.html links stylesheets that do not exist: {missing}"


def test_every_linked_script_exists() -> None:
    on_disk = _js_on_disk()
    missing = sorted(_linked_js() - on_disk)
    assert not missing, f"index.html loads scripts that do not exist: {missing}"


def test_no_dead_stylesheets_on_disk() -> None:
    on_disk = _css_on_disk()
    dead = sorted(on_disk - _linked_css() - _PENDING_DELETION)
    assert not dead, (
        f"stylesheets on disk that nothing loads: {dead}. Either link them or "
        f"delete them -- a dead stylesheet is a trap for the next reader."
    )


def test_no_dead_scripts_on_disk() -> None:
    on_disk = _js_on_disk()
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
    quality = (_GUI / "js" / "fx" / "quality.js").read_text(encoding="utf-8")
    motion_pack = (_GUI / "style" / "fx" / "motion-pack.css").read_text(encoding="utf-8")

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
    assert (_GUI / "js" / "dev" / "mock.js").exists(), "mock.js should still exist for preview use"


# ──────────────────────────────────────────────────────────────────────
# Theme list: three declarations, one order
# ──────────────────────────────────────────────────────────────────────
#
# The same theme list is declared in three places:
#   sara/gui/js/core/theme.js            LIST      -- the picker's display order
#   sara/gui/app/ui_prefs.py        _THEMES   -- backend validation
#   sara/gui/js/tests/...test.js    expected  -- the assertion
#
# They had drifted: theme.js placed 'lime' between 'aurora' and 'neon' while
# the other two placed it between 'oat' and 'custom', so ui_prefs_theme.test.js
# failed. Nothing broke at runtime because _THEMES is only ever used as a
# membership test (`text in _THEMES`), never indexed -- but three copies of one
# list with no check between them is how that drift happened in the first place.


def _theme_ids_from_js() -> list[str]:
    src = (_GUI / "js" / "core" / "theme.js").read_text(encoding="utf-8")
    body = src.split("const LIST = [", 1)[1].split("];", 1)[0]
    return re.findall(r"id: '(\w+)'", body)


def _theme_ids_from_python() -> list[str]:
    src = (_GUI / "app" / "ui_prefs.py").read_text(encoding="utf-8")
    body = src.split("_THEMES = (", 1)[1].split(")", 1)[0]
    return re.findall(r'"(\w+)"', body)


def test_theme_list_matches_backend_preference_list() -> None:
    """theme.js's picker order and ui_prefs.py's _THEMES must stay in step."""
    js_ids, py_ids = _theme_ids_from_js(), _theme_ids_from_python()
    assert js_ids, "could not parse LIST out of theme.js"
    assert js_ids == py_ids, (
        "theme.js LIST and ui_prefs.py _THEMES disagree.\n"
        f"  theme.js : {js_ids}\n"
        f"  ui_prefs : {py_ids}"
    )


def test_every_theme_is_accepted_by_the_backend_validator() -> None:
    """
    Stronger than ordering: a theme the picker offers but the backend rejects
    would make the swatch look broken (set_ui_pref returns 'Invalid UI
    preference'). Exercises the real validator, not a copy of the list.
    """
    from sara.gui.app.ui_prefs import normalize_ui_pref

    for theme_id in _theme_ids_from_js():
        assert normalize_ui_pref("theme", theme_id) == theme_id, (
            f"theme {theme_id!r} is offered by the picker but rejected by "
            f"ui_prefs.normalize_ui_pref()"
        )
    assert normalize_ui_pref("theme", "not_a_theme") is None
    assert normalize_ui_pref("theme", "") == "", "'' means back-to-default"


def test_lime_sits_between_oat_and_custom() -> None:
    """Named guard for the ordering decision made in batch 6a."""
    ids = _theme_ids_from_js()
    assert ids.index("oat") < ids.index("lime") < ids.index("custom")


def test_pm_overlays_stay_out_of_flow_even_when_the_pack_is_off() -> None:
    """
    js/fx/motion-pack.js creates the pm-* overlays regardless of the "pm" switch. If their positioning is
    only inside the gated group, switching "pm" off turns the dust canvas into a full-window block child of
    <body> and the app renders ~1000px below the fold (black screen at launch).
    """
    css = (_GUI / "style" / "fx" / "motion-pack.css").read_text(encoding="utf-8")
    ungated = [
        line for line in css.splitlines()
        if line.startswith(".pm-aura,.pm-dust") and "position:fixed" in line
    ]
    assert ungated, "pm overlays need an ungated position:fixed rule"
    assert 'html[data-fx-off~="pm"] :is(.pm-aura,.pm-dust' in css
