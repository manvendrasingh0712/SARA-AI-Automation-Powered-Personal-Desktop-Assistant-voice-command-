"""Audit part 2, O17: installed-app index."""
from pathlib import Path

from sara.tools.system import app_index as A
from sara.tools.system import apps


def _make(tmp_path):
    d = tmp_path / "Programs" / "Sub"
    d.mkdir(parents=True)
    for n in ("Spotify", "Visual Studio Code", "Zorblax Studio", "Uninstall Spotify", "Some App Readme"):
        (d / f"{n}.lnk").write_text("x")
    return [tmp_path / "Programs"]


def test_scan_skips_uninstallers_and_builds_a_table(tmp_path):
    A.build(_make(tmp_path), include_uwp=False)
    assert A.size() == 3
    assert A.resolve("spotify").name == "Spotify"
    assert A.resolve("visual studio code").name == "Visual Studio Code"
    assert A.resolve("notepad plus") is None or A.resolve("notepad plus").name.startswith("Notepad")


def test_fuzzy_match_is_unique_or_nothing(tmp_path):
    root = tmp_path / "P"
    root.mkdir()
    for n in ("Chrome Remote Desktop", "Chrome Canary"):
        (root / f"{n}.lnk").write_text("x")
    A.build([root], include_uwp=False)
    assert A.resolve("chrome") is None          # two candidates: no guess
    assert A.resolve("ab") is None              # too short


def test_open_application_uses_the_index_before_windows_search(tmp_path, monkeypatch):
    A.build(_make(tmp_path), include_uwp=False)
    launched = []
    monkeypatch.setattr(A, "launch", lambda e: launched.append(e.name) or True)
    monkeypatch.setattr(apps, "_open_via_windows_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("search used")))
    monkeypatch.setattr(apps, "_search_fallback_applies", lambda key: True)
    monkeypatch.setattr(apps, "_IS_WINDOWS", True, raising=False)
    out = apps.open_application("zorblax studio")
    assert out.startswith("Opened") and launched == ["Zorblax Studio"]
