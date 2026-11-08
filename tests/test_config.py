import json

import pytest

from voice_clone_mic.config import load_settings, save_voice_id
from voice_clone_mic.errors import ConversionError


def test_dotenv_does_not_override_existing_environment(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("ELEVENLABS_API_KEY=from-file\nELEVENLABS_VOICE_ID=fromfileid\n", encoding="utf-8")
    kept = load_settings({"ELEVENLABS_API_KEY": "from-env"}, tmp_path, dotenv_file=dotenv)
    assert kept.api_key == "from-env"
    filled = load_settings({}, tmp_path, dotenv_file=dotenv)
    assert filled.api_key == "from-file"
    assert filled.voice_id == "fromfileid"


def test_config_file_key_is_ignored_and_voice_id_round_trips(tmp_path):
    folder = tmp_path / ".vcm"
    folder.mkdir()
    (folder / "config.json").write_text(
        json.dumps({"api_key": "super-secret-key", "voice_id": "JBFqnCBsd6RMkjVDRZzb"}),
        encoding="utf-8",
    )
    settings = load_settings({}, tmp_path, dotenv_file=tmp_path / "missing.env")
    assert settings.api_key is None
    assert settings.ignored_file_key is True
    assert settings.voice_id == "JBFqnCBsd6RMkjVDRZzb"

    save_voice_id(tmp_path, "voice_id_ok")
    stored = json.loads((tmp_path / ".vcm" / "config.json").read_text(encoding="utf-8"))
    assert stored == {"voice_id": "voice_id_ok"}
    with pytest.raises(ConversionError):
        save_voice_id(tmp_path, "../x")
