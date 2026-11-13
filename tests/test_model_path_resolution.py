from pathlib import Path

from config import Config
import config
from sara.audio.stt import engine as stt_engine
from sara.gui.app import setup_wizard


def test_source_defaults_resolve_under_project_root_and_find_models(tmp_path, monkeypatch):
    project = tmp_path / "SARA Project With Spaces"
    kokoro = project / "models" / "kokoro-v1.0.onnx"
    voices = project / "models" / "voices-v1.0.bin"
    kokoro.parent.mkdir(parents=True)
    kokoro.touch()
    voices.touch()
    monkeypatch.setattr(config, "_PROJECT_ROOT", project)
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)

    assert Path(config._resolve_model_path("models/kokoro-v1.0.onnx")) == kokoro
    assert Path(config._resolve_model_path("models/voices-v1.0.bin")) == voices
    assert kokoro.is_file() and voices.is_file()


def test_relative_model_paths_ignore_the_launch_working_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    launch_dir = tmp_path / "elsewhere"
    launch_dir.mkdir()
    monkeypatch.setattr(config, "_PROJECT_ROOT", project)
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)
    monkeypatch.chdir(launch_dir)

    assert config._resolve_model_path("models/kokoro-v1.0.onnx") == str(
        (project / "models" / "kokoro-v1.0.onnx").resolve()
    )


def test_frozen_relative_models_resolve_beside_exe_even_with_spaces(tmp_path, monkeypatch):
    install = tmp_path / "SARA AI Install Folder"
    exe = install / "SaraAI.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "executable", str(exe))

    expected = install / "models" / "kokoro-v1.0.onnx"
    assert Path(config._resolve_model_path("models/kokoro-v1.0.onnx")) == expected


def test_absolute_user_model_path_is_preserved(tmp_path, monkeypatch):
    configured = tmp_path / "Custom Models" / "voice model.onnx"
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)

    assert Path(config._resolve_model_path(configured)) == configured.resolve()


def test_spec_places_required_openwakeword_onnx_assets_at_library_resource_path():
    spec = (Path(__file__).resolve().parents[1] / "sara_ai.spec").read_text(encoding="utf-8")

    for filename in ("melspectrogram.onnx", "embedding_model.onnx"):
        assert f'OWW_RESOURCES / "{filename}"' in spec
    assert '"openwakeword/resources/models"' in spec
    assert '"hey_sara.onnx"' not in spec


def test_missing_models_are_reported_without_claiming_wakeword_available(tmp_path, monkeypatch):
    missing_kokoro = tmp_path / "missing" / "kokoro.onnx"
    missing_voices = tmp_path / "missing" / "voices.bin"
    missing_wake = tmp_path / "missing" / "hey_sara.onnx"
    monkeypatch.setattr(Config, "LLM_BACKEND", "ollama")
    monkeypatch.setattr(Config, "KOKORO_MODEL_PATH", str(missing_kokoro))
    monkeypatch.setattr(Config, "KOKORO_VOICES_PATH", str(missing_voices))
    monkeypatch.setattr(Config, "WAKE_WORD_MODEL_PATH", str(missing_wake))
    monkeypatch.setattr(setup_wizard, "_check_ollama", lambda: {
        "ollama_installed": True,
        "ollama_running": True,
        "llm_model_pulled": True,
    })
    monkeypatch.setattr(setup_wizard, "_check_embedding_backend", lambda: {
        "ok": True,
        "error": "",
    })

    status = setup_wizard.ApiSetupWizardMixin().check_setup_status()

    assert status["kokoro_model_present"] is False
    assert status["kokoro_voices_present"] is False
    assert status["wake_word_model_present"] is False
    assert "WAKE_WORD_MODEL_PATH" in status["wake_word_setup_message"]
    assert status["all_ready"] is False


def test_missing_optional_custom_wakeword_disables_detection_with_actionable_message(
    tmp_path, monkeypatch, capsys
):
    missing = tmp_path / "models with spaces" / "hey_sara.onnx"
    monkeypatch.setattr(Config, "WAKE_WORD_MODEL_PATH", str(missing))
    monkeypatch.setattr(stt_engine, "_HAS_WAKEWORD", True)
    monkeypatch.setattr(stt_engine, "_OWWModel", object)

    detector = object.__new__(stt_engine.SpeechToText)
    assert detector._load_wakeword() is None
    output = capsys.readouterr().out
    assert "Passive wake detection is disabled" in output
    assert "set WAKE_WORD_MODEL_PATH" in output
