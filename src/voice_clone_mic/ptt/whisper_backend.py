"""Official OpenAI Whisper loader. Importing this module does not import whisper."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from voice_clone_mic.ptt.session import Transcript

DEFAULT_MODEL = "small.en"
# SHA256 published in openai-whisper 20250625 for the small.en checkpoint.
MODEL_SHA256 = {
    "small.en": "f953ad0fd29cacd07d5a9eda5624af0f6bcf2258be67c92b79389873d91e0872",
}


class WhisperNotInstalled(RuntimeError):
    """The optional ptt extra is absent, so no download was started."""


class LocalModelMissing(RuntimeError):
    """The prepared checkpoint is missing or its hash does not match."""


def model_path(root: Path, name: str = DEFAULT_MODEL) -> Path:
    return root / f"{name}.pt"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_official_model(root: Path, name: str = DEFAULT_MODEL) -> Path:
    """Download one official checkpoint. This is the only networked model step."""

    try:
        import whisper
    except ImportError as exc:
        raise WhisperNotInstalled(
            "The optional ptt extra is not installed, so no model download was started. "
            "Install it in its own virtualenv with: python -m pip install -e \".[ptt]\""
        ) from exc
    if name not in MODEL_SHA256:
        raise LocalModelMissing(f"Refusing to download an unlisted model name: {name}")
    root.mkdir(parents=True, exist_ok=True)
    print(
        "Downloading the official OpenAI Whisper checkpoint. Microphone audio is not uploaded.",
        file=sys.stderr,
    )
    whisper.load_model(name, device="cpu", download_root=str(root))
    path = model_path(root, name)
    if not path.is_file() or _sha256(path) != MODEL_SHA256[name]:
        raise LocalModelMissing("Downloaded model hash did not match the published small.en checksum.")
    return path


def assert_local_model(root: Path, name: str = DEFAULT_MODEL) -> Path:
    path = model_path(root, name)
    expected = MODEL_SHA256.get(name)
    if expected is None or not path.is_file() or _sha256(path) != expected:
        raise LocalModelMissing(
            "Local Whisper checkpoint is missing or does not match the published hash. "
            "Run `voice-clone-mic ptt prepare-model` when you intend to use the network. "
            "Transcription will not download it."
        )
    return path


class LocalWhisperTranscriber:
    def __init__(self, root: Path, name: str = DEFAULT_MODEL) -> None:
        path = assert_local_model(root, name)
        import whisper

        self._model = whisper.load_model(name, device="cpu", download_root=str(path.parent))

    def transcribe(self, samples_16k: list[int]) -> Transcript:
        import numpy as np

        audio = np.asarray(samples_16k, dtype="float32") / 32768.0
        result = self._model.transcribe(
            audio,
            fp16=False,
            language="en",
            temperature=0.0,
            condition_on_previous_text=False,
        )
        segments = []
        for segment in result.get("segments") or []:
            segments.append(
                {
                    "text": segment.get("text", ""),
                    "no_speech_prob": float(segment.get("no_speech_prob", 1.0)),
                    "avg_logprob": float(segment.get("avg_logprob", -10.0)),
                    "compression_ratio": float(segment.get("compression_ratio", 99.0)),
                }
            )
        return Transcript(str(result.get("text", "")), segments)
