"""Stable config and Whisper directories. This module does not search the disk."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from voice_clone_mic.ptt.whisper_backend import DEFAULT_MODEL, MODEL_SHA256


def support_dir(home: Path | None = None) -> Path:
    override = os.environ.get("VOICE_CLONE_MIC_SUPPORT")
    if override:
        return Path(override)
    root = Path.home() if home is None else home
    return root / "Library" / "Application Support" / "VoiceCloneMic"


def known_whisper_dirs(home: Path, cwd: Path) -> list[Path]:
    """Return the only directories a model check is allowed to read."""

    return [
        home / "Library" / "Application Support" / "VoiceCloneMic" / "whisper",
        home / "Applications" / "VoiceCloneMicRuntime" / "runtime" / ".vcm" / "whisper",
        cwd / ".vcm" / "whisper",
    ]


def find_whisper_model(
    directories: list[Path],
    name: str = DEFAULT_MODEL,
    expected: str | None = None,
) -> Path | None:
    digest = expected if expected is not None else MODEL_SHA256.get(name)
    if not digest:
        return None
    filename = f"{name}.pt"
    for directory in directories:
        path = directory / filename
        if path.is_file() and _sha256(path) == digest:
            return path
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
