"""Decide whether a finalized transcript is allowed to be spoken."""

from __future__ import annotations

import math
from dataclasses import dataclass


RMS_THRESHOLD = 500.0
NO_SPEECH_PROB = 0.6
MIN_LOGPROB = -1.0
MAX_COMPRESSION = 2.4
MAX_TTS_CHARS = 500


@dataclass(frozen=True)
class Decision:
    speak: bool
    text: str
    reason: str


def rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / len(samples))


def assess_transcript(text: str, segments: list[dict], rms: float) -> Decision:
    cleaned = " ".join((text or "").split())
    if rms < RMS_THRESHOLD:
        return Decision(False, "", "silence")
    if not cleaned or not segments:
        return Decision(False, "", "empty")
    if all(float(segment.get("no_speech_prob", 1.0)) >= NO_SPEECH_PROB for segment in segments):
        return Decision(False, "", "no_speech")
    for segment in segments:
        if float(segment.get("avg_logprob", -10.0)) < MIN_LOGPROB:
            return Decision(False, "", "low_confidence")
        if float(segment.get("compression_ratio", 99.0)) > MAX_COMPRESSION:
            return Decision(False, "", "low_confidence")
    if len(cleaned) > MAX_TTS_CHARS:
        return Decision(False, "", "too_long")
    return Decision(True, cleaned, "ok")
