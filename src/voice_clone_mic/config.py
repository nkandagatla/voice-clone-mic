"""Local settings. The API key is read from the environment and never written."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from voice_clone_mic.errors import ConversionError

VOICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    voice_id: str | None
    model_id: str
    api_base: str
    input_device: str | None
    output_device: str | None
    ignored_file_key: bool = False


def load_settings(
    environ: dict[str, str],
    cwd: Path,
    dotenv_file: Path | None = None,
) -> Settings:
    env = dict(environ)
    path = cwd / ".env" if dotenv_file is None else dotenv_file
    if path.is_file():
        from dotenv import dotenv_values

        for key, value in dotenv_values(path).items():
            if key and value is not None and key not in env:
                env[key] = value
    ignored = False
    file_voice = None
    config_path = cwd / ".vcm" / "config.json"
    if config_path.is_file():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConversionError("config", "Could not read .vcm/config.json.") from exc
        if not isinstance(data, dict):
            raise ConversionError("config", "Could not read .vcm/config.json.")
        if "api_key" in data or "ELEVENLABS_API_KEY" in data:
            ignored = True
        voice = data.get("voice_id")
        if isinstance(voice, str) and voice:
            file_voice = voice
    voice_id = env.get("ELEVENLABS_VOICE_ID") or file_voice or None
    if voice_id == "":
        voice_id = None
    api_key = env.get("ELEVENLABS_API_KEY") or None
    if api_key == "":
        api_key = None
    return Settings(
        api_key=api_key,
        voice_id=voice_id,
        model_id=env.get("ELEVENLABS_MODEL_ID") or "eleven_multilingual_sts_v2",
        api_base=env.get("ELEVENLABS_API_BASE") or "https://api.elevenlabs.io",
        input_device=env.get("VCM_INPUT_DEVICE") or None,
        output_device=env.get("VCM_OUTPUT_DEVICE") or None,
        ignored_file_key=ignored,
    )


def save_voice_id(cwd: Path, voice_id: str) -> None:
    if VOICE_ID_RE.fullmatch(voice_id) is None:
        raise ConversionError(
            "config",
            "Voice id must be 8-64 characters of letters, digits, underscore, or hyphen.",
        )
    path = cwd / ".vcm" / "config.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"voice_id": voice_id}) + "\n", encoding="utf-8")
