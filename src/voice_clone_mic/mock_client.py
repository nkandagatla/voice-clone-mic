"""Offline converter used by demo, --mock, and tests. Not an ElevenLabs model."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


def xor_pcm(pcm: bytes) -> bytes:
    return bytes(byte ^ 0xFF for byte in pcm)


class MockVoiceClient:
    def __init__(self, delay_s: list[float] | None = None) -> None:
        self.delay_s = list(delay_s or [])
        self.payloads: list[bytes] = []
        self.calls = 0

    async def stream(
        self,
        voice_id: str,
        pcm: bytes,
        cancel: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        index = self.calls
        self.calls += 1
        self.payloads.append(pcm)
        delay = self.delay_s[index] if index < len(self.delay_s) else 0.0
        if delay:
            await asyncio.sleep(delay)
        if cancel.is_set():
            return
        data = xor_pcm(pcm)
        if not data:
            return
        yield data[:1]
        if len(data) == 1:
            return
        mid = min(4, len(data))
        yield data[1:mid]
        if mid < len(data):
            yield data[mid:]
