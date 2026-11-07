"""Speech segmentation with pre-roll, hangover, and forced cuts."""

from __future__ import annotations

import math
from dataclasses import dataclass

from voice_clone_mic.pcm import samples_to_bytes


@dataclass
class Segment:
    sequence: int
    generation: int
    pcm: bytes
    duration_ms: int


class SpeechSegmenter:
    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        threshold: float = 500.0,
        pre_roll_ms: int = 200,
        hangover_ms: int = 400,
        min_speech_ms: int = 200,
        max_segment_ms: int = 4000,
        generation: int = 0,
    ) -> None:
        if frame_ms <= 0 or sample_rate <= 0:
            raise ValueError("frame and sample rate must be positive")
        for name, value in (
            ("hangover_ms", hangover_ms),
            ("min_speech_ms", min_speech_ms),
            ("max_segment_ms", max_segment_ms),
        ):
            if value <= 0 or value % frame_ms != 0:
                raise ValueError(f"{name} must be a positive multiple of frame_ms")
        if pre_roll_ms < 0 or pre_roll_ms % frame_ms != 0:
            raise ValueError("pre_roll_ms must be a non-negative multiple of frame_ms")
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_len = sample_rate * frame_ms // 1000
        if self.frame_len <= 0:
            raise ValueError("frame length is zero")
        self.threshold = float(threshold)
        self.pre_roll_frames = pre_roll_ms // frame_ms
        self.hangover_frames = hangover_ms // frame_ms
        self.min_speech_frames = min_speech_ms // frame_ms
        self.max_frames = max_segment_ms // frame_ms
        self.generation = generation
        self._pending: list[int] = []
        self._preroll: list[list[int]] = []
        self._current: list[list[int]] = []
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._seq = 0
        self._phrase_committed = False

    def reset(self) -> None:
        self._pending.clear()
        self._preroll.clear()
        self._current.clear()
        self._in_speech = False
        self._speech_frames = 0
        self._silence_frames = 0
        self._seq = 0
        self._phrase_committed = False

    def push(self, samples: list[int]) -> list[Segment]:
        if samples:
            self._pending.extend(samples)
        emitted: list[Segment] = []
        while len(self._pending) >= self.frame_len:
            frame = list(self._pending[: self.frame_len])
            del self._pending[: self.frame_len]
            emitted.extend(self._consume(frame))
        return emitted

    def flush(self) -> list[Segment]:
        self._pending.clear()
        if not self._in_speech:
            return []
        segment = self._close(require_min=True)
        self._idle()
        return [segment] if segment is not None else []

    def _consume(self, frame: list[int]) -> list[Segment]:
        loud = _rms(frame) >= self.threshold
        if not self._in_speech:
            if not loud:
                self._remember_preroll(frame)
                return []
            self._in_speech = True
            self._current = [list(item) for item in self._preroll]
            self._preroll.clear()
            self._speech_frames = 0
            self._silence_frames = 0
        self._current.append(frame)
        if loud:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1
        if len(self._current) >= self.max_frames:
            segment = self._close(require_min=False)
            stay = loud and segment is not None
            self._current = []
            self._speech_frames = 0
            self._silence_frames = 0
            self._in_speech = stay
            self._phrase_committed = stay
            return [segment] if segment is not None else []
        if self._silence_frames >= self.hangover_frames:
            segment = self._close(require_min=True)
            self._idle()
            return [segment] if segment is not None else []
        return []

    def _remember_preroll(self, frame: list[int]) -> None:
        if self.pre_roll_frames <= 0:
            return
        self._preroll.append(frame)
        extra = len(self._preroll) - self.pre_roll_frames
        if extra > 0:
            del self._preroll[:extra]

    def _close(self, require_min: bool) -> Segment | None:
        if self._speech_frames <= 0:
            return None
        if (
            require_min
            and not self._phrase_committed
            and self._speech_frames < self.min_speech_frames
        ):
            return None
        samples: list[int] = []
        for frame in self._current:
            samples.extend(frame)
        segment = Segment(
            sequence=self._seq,
            generation=self.generation,
            pcm=samples_to_bytes(samples),
            duration_ms=len(samples) * 1000 // self.sample_rate,
        )
        self._seq += 1
        return segment

    def _idle(self) -> None:
        self._current = []
        self._speech_frames = 0
        self._silence_frames = 0
        self._in_speech = False
        self._phrase_committed = False


def _rms(frame: list[int]) -> float:
    if not frame:
        return 0.0
    total = 0
    for sample in frame:
        total += sample * sample
    return math.sqrt(total / len(frame))
