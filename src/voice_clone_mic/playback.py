"""Bounded playback of converted mono audio. Underruns are silence."""

from __future__ import annotations

import threading
from array import array

from voice_clone_mic.pcm import clip_i16


class PlaybackBuffer:
    def __init__(self, sample_rate: int, channels: int, max_ms: int = 6000) -> None:
        if sample_rate < 1 or channels < 1 or max_ms < 1:
            raise ValueError("playback settings must be positive")
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_samples = max(1, sample_rate * max_ms // 1000)
        self.generation = 1
        self.underruns = 0
        self.dropped_writes = 0
        self._mono = array("h")
        self._lock = threading.Lock()

    def write(self, generation: int, mono: list[int]) -> bool:
        with self._lock:
            if generation != self.generation:
                return False
            if not mono:
                return True
            space = self.max_samples - len(self._mono)
            if space <= 0:
                self.dropped_writes += 1
                return False
            accepted = mono[:space]
            self._mono.extend(clip_i16(sample) for sample in accepted)
            if len(accepted) < len(mono):
                self.dropped_writes += 1
            return True

    def read_mono(self, frames: int) -> list[int]:
        with self._lock:
            if frames <= 0:
                return []
            available = min(frames, len(self._mono))
            head = list(self._mono[:available])
            del self._mono[:available]
            if available < frames:
                self.underruns += 1
                head.extend([0] * (frames - available))
            return head

    def snapshot(self) -> list[int]:
        with self._lock:
            return list(self._mono)

    def clear_and_bump(self) -> int:
        with self._lock:
            self.generation += 1
            self._mono = array("h")
            return self.generation

    def queued_samples(self) -> int:
        with self._lock:
            return len(self._mono)

    def queued_ms(self) -> int:
        with self._lock:
            return len(self._mono) * 1000 // self.sample_rate
