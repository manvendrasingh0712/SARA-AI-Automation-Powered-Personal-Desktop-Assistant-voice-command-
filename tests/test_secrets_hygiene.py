"""Task 0 of the audit: a secret scanner and a release zip that cannot contain private files."""
import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scanner_flags_planted_keys_without_printing_them(tmp_path, capsys):
    scan = _load("check_no_secrets")
    fake_key = "AIza" + "x" * 35
    bad = tmp_path / "leak.py"
    bad.write_text(f'GEMINI = "{fake_key}"\nrefresh_token = "1//0abcdefghijklmnopqrstuvwxyz"\n', encoding="utf-8")  # secret-scan: allow
    assert scan.main([str(bad)]) == 1
    out = capsys.readouterr().out
    assert "leak.py:1" in out and "leak.py:2" in out and fake_key not in out


def test_scanner_passes_a_clean_file(tmp_path):
    scan = _load("check_no_secrets")
    ok = tmp_path / "ok.py"
    ok.write_text("refresh_token = load_token()\nAPI_KEY = os.getenv('X')\n", encoding="utf-8")
    assert scan.main([str(ok)]) == 0


def test_tracked_tree_is_clean():
    scan = _load("check_no_secrets")
    assert scan.main([]) == 0


def test_release_zip_has_no_private_files(tmp_path):
    rel = _load("make_release_zip")
    out = tmp_path / "r.zip"
    assert rel.main([str(out)]) == 0
    names = {Path(n).name for n in zipfile.ZipFile(out).namelist()}
    assert ".env" not in names and "token.json" not in names
    assert not any(n.endswith((".db", ".sqlite")) for n in names)
    assert ".env.example" in names
