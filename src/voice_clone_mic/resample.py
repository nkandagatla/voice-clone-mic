"""Streaming linear resampler with carried phase."""

from __future__ import annotations

from voice_clone_mic.pcm import clip_i16


class StreamResampler:
    def __init__(self, source_rate: int, target_rate: int) -> None:
        if source_rate < 1 or target_rate < 1:
            raise ValueError("sample rates must be positive")
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._step = source_rate / target_rate
        self._buf: list[int] = []
        self._origin = 0
        self._pos = 0.0

    def reset(self) -> None:
        self._buf.clear()
        self._origin = 0
        self._pos = 0.0

    def process(self, samples: list[int]) -> list[int]:
        if samples:
            self._buf.extend(int(sample) for sample in samples)
        output: list[int] = []
        while self._buf and self._available(self._pos):
            output.append(self._interpolate(self._pos, endpoint=False))
            self._pos += self._step
        self._drop_consumed()
        return output

    def flush(self) -> list[int]:
        output: list[int] = []
        if self._buf:
            last = self._origin + len(self._buf) - 1
            while self._pos <= last + 1e-9:
                output.append(self._interpolate(self._pos, endpoint=True))
                self._pos += self._step
        self.reset()
        return output

    def _available(self, pos: float) -> bool:
        if not self._buf:
            return False
        index = int(pos)
        needed = index if pos - index <= 1e-9 else index + 1
        return needed <= self._origin + len(self._buf) - 1

    def _drop_consumed(self) -> None:
        discard = int(self._pos) - self._origin
        if discard <= 0:
            return
        discard = min(discard, len(self._buf))
        del self._buf[:discard]
        self._origin += discard

    def _interpolate(self, pos: float, endpoint: bool) -> int:
        last = self._origin + len(self._buf) - 1
        index = int(pos)
        if index >= last:
            return clip_i16(self._buf[-1])
        frac = pos - index
        local = index - self._origin
        left = self._buf[local]
        if frac <= 1e-9:
            return clip_i16(left)
        right_index = local + 1
        if right_index >= len(self._buf):
            if not endpoint:
                return clip_i16(left)
            right = self._buf[-1]
        else:
            right = self._buf[right_index]
        return clip_i16(int(round(left * (1.0 - frac) + right * frac)))
