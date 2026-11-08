"""Bounded push-to-talk sample buffer."""

from __future__ import annotations


class BoundedCapture:
    def __init__(self, max_samples: int) -> None:
        if max_samples < 1:
            raise ValueError("capture cap must be positive")
        self.max_samples = max_samples
        self.dropped = 0
        self._samples: list[int] = []

    def extend(self, samples: list[int]) -> None:
        room = self.max_samples - len(self._samples)
        if room <= 0:
            self.dropped += len(samples)
            return
        if len(samples) > room:
            self._samples.extend(samples[:room])
            self.dropped += len(samples) - room
            return
        self._samples.extend(samples)

    def snapshot(self) -> list[int]:
        return list(self._samples)

    def clear(self) -> None:
        self._samples.clear()
        self.dropped = 0
