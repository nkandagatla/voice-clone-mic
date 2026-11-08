import subprocess
import sys
from pathlib import Path

import httpx

from voice_clone_mic.cli import main


def _forbid_network(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network client constructed")

    monkeypatch.setattr(httpx, "AsyncClient", boom)


def test_ptt_demo_and_mock_run_do_not_open_a_client(capsys, monkeypatch):
    _forbid_network(monkeypatch)
    assert main(["ptt", "demo"]) == 0
    demo = capsys.readouterr().out
    assert "mode=ptt" in demo
    assert "mock=true" in demo
    assert "passthrough=false" in demo
    assert "preview=hello there" in demo
    assert "No audio was sent to ElevenLabs." in demo
    assert main(["ptt", "run", "--mock", "--fake", "--once", "--output", "BlackHole"]) == 0
    mock_run = capsys.readouterr().out
    assert "passthrough=false" in mock_run
    assert "No audio was sent to ElevenLabs." in mock_run


def test_ptt_live_run_sends_nothing_without_billing_flag(capsys, monkeypatch):
    _forbid_network(monkeypatch)
    assert main(["ptt", "run", "--fake", "--output", "BlackHole"]) == 2
    assert "No request was sent" in capsys.readouterr().err


def test_prepare_model_does_not_download_when_extra_is_missing(capsys):
    assert main(["ptt", "prepare-model", "--dir", ".vcm/whisper"]) == 2
    error = capsys.readouterr().err
    assert "no model download was started" in error


def test_importing_ptt_does_not_import_whisper():
    root = Path(__file__).resolve().parents[1]
    env = dict(**{key: value for key, value in __import__("os").environ.items()})
    env["PYTHONPATH"] = str(root / "src")
    script = (
        "import sys, voice_clone_mic.ptt.session, voice_clone_mic.ptt.whisper_backend; "
        "raise SystemExit(1 if 'whisper' in sys.modules else 0)"
    )
    completed = subprocess.run([sys.executable, "-c", script], check=False, cwd=root, env=env)
    assert completed.returncode == 0


def test_config_shows_tts_model_separately(capsys, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "super-secret-key")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_sts_v2")
    monkeypatch.setenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
    assert main(["config"]) == 0
    output = capsys.readouterr().out
    assert "super-secret-key" not in output
    assert "model_id=eleven_multilingual_sts_v2" in output
    assert "tts_model=eleven_flash_v2_5" in output
