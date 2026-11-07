import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from voice_clone_mic.cli import main


def _forbid_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("network client constructed")

    monkeypatch.setattr(httpx, "AsyncClient", boom)


def test_help_mentions_delay_and_cables(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "BlackHole" in output
    assert "VB-CABLE" in output
    assert "phrase" in output.lower()


def test_demo_and_mock_run_do_not_open_a_client(capsys, monkeypatch):
    _forbid_network(monkeypatch)
    assert main(["demo"]) == 0
    demo = capsys.readouterr().out
    assert "mock=true" in demo
    assert "passthrough=false" in demo
    assert "No audio was sent to ElevenLabs." in demo
    assert main(["run", "--mock", "--fake", "--once", "--output", "BlackHole"]) == 0
    mock_run = capsys.readouterr().out
    assert "mock=true" in mock_run
    assert "passthrough=false" in mock_run


def test_live_run_and_voice_list_send_nothing_without_billing_flag(capsys, monkeypatch):
    _forbid_network(monkeypatch)
    assert main(["run", "--fake", "--output", "BlackHole"]) == 2
    assert "No request was sent" in capsys.readouterr().err
    assert main(["voices"]) == 2
    assert "No request was sent" in capsys.readouterr().err
    assert main(["run", "--mock", "--fake", "--once"]) == 2


def test_config_redacts_the_api_key(capsys, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "super-secret-key")
    assert main(["config"]) == 0
    output = capsys.readouterr().out
    assert "super-secret-key" not in output
    assert "api_key=set" in output


def test_use_voice_and_fake_device_list(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(["use-voice", "../x"]) == 2
    assert main(["use-voice", "JBFqnCBsd6RMkjVDRZzb"]) == 0
    stored = json.loads((tmp_path / ".vcm" / "config.json").read_text(encoding="utf-8"))
    assert stored == {"voice_id": "JBFqnCBsd6RMkjVDRZzb"}
    assert main(["devices", "--fake"]) == 0
    listed = capsys.readouterr().out
    assert "BlackHole 2ch" in listed
    assert "CABLE Input" in listed


def test_cli_import_does_not_import_sounddevice():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    script = "import sys, voice_clone_mic.cli; raise SystemExit(1 if 'sounddevice' in sys.modules else 0)"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        cwd=root,
        env=env,
    )
    assert completed.returncode == 0
