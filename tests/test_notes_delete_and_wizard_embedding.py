import types

from sara.tools.system import files_notes as fn


def _notes(tmp_path, monkeypatch, *texts):
    path = tmp_path / "notes.txt"
    monkeypatch.setattr(fn, "_NOTES_FILE", str(path))
    for t in texts:
        fn.take_note(t)
    return path


def test_delete_note_removes_only_that_note(tmp_path, monkeypatch):
    _notes(tmp_path, monkeypatch, "alpha", "beta #x", "gamma")
    res = fn.delete_note(1, "beta #x")
    assert res["ok"] and res["text"] == "beta #x"
    assert [n["text"] for n in fn.get_notes()] == ["alpha", "gamma"]


def test_delete_note_refuses_a_stale_id(tmp_path, monkeypatch):
    _notes(tmp_path, monkeypatch, "alpha", "beta")
    assert not fn.delete_note(0, "beta")["ok"]
    assert [n["text"] for n in fn.get_notes()] == ["alpha", "beta"]


def test_delete_note_bad_ids(tmp_path, monkeypatch):
    _notes(tmp_path, monkeypatch, "alpha")
    assert not fn.delete_note(5)["ok"]
    assert not fn.delete_note("x")["ok"]
    assert not fn.delete_note(-1)["ok"]
    assert len(fn.get_notes()) == 1


def test_delete_note_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(fn, "_NOTES_FILE", str(tmp_path / "none.txt"))
    assert not fn.delete_note(0)["ok"]


def test_api_delete_note_goes_through_system_tools(tmp_path, monkeypatch):
    from sara.gui.app.notes import ApiNotesMixin
    from sara.tools import system as system_tools

    _notes(tmp_path, monkeypatch, "one", "two")
    api = ApiNotesMixin()
    api.system_tools = system_tools
    assert api.delete_note(0, "one")["ok"]
    assert [n["text"] for n in fn.get_notes()] == ["two"]


def test_setup_wizard_checks_ollama_embeddings_when_that_is_the_backend(monkeypatch):
    from config import Config
    from sara.gui.app import setup_wizard as sw

    monkeypatch.setattr(Config, "EMBEDDING_BACKEND", "ollama", raising=False)
    monkeypatch.setattr(Config, "OLLAMA_EMBEDDING_MODEL", "paraphrase-multilingual", raising=False)
    monkeypatch.setattr("sara.core.llm.clients._get_embedding_vector", lambda cfg, text: [0.1, 0.2])
    assert sw._embedding_model_name() == "paraphrase-multilingual"
    assert sw._check_embedding_backend() == {"ok": True, "error": ""}
    monkeypatch.setattr("sara.core.llm.clients._get_embedding_vector", lambda cfg, text: None)
    res = sw._check_embedding_backend()
    assert not res["ok"] and "paraphrase-multilingual" in res["error"]
