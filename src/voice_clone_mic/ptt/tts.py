"""ElevenLabs streaming TTS. Mock client is used by demo and tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx

from voice_clone_mic.errors import ConversionError, error_from_status

OUTPUT_FORMAT = "pcm_24000"
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"


class HttpxTtsClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_id: str = DEFAULT_TTS_MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.transport = transport
        self.timeout_s = timeout_s

    async def stream(self, voice_id: str, text: str, cancel: asyncio.Event) -> AsyncIterator[bytes]:
        if cancel.is_set():
            raise asyncio.CancelledError()
        cleaned = " ".join(text.split())
        if not cleaned:
            raise ConversionError("rejected", "Empty transcript was not sent.")
        url = f"{self.base_url}/v1/text-to-speech/{quote(voice_id, safe='')}/stream"
        payload: dict[str, str] = {"text": cleaned, "model_id": self.model_id}
        if "multilingual_v2" not in self.model_id:
            payload["language_code"] = "en"
        timeout = httpx.Timeout(connect=10.0, read=self.timeout_s, write=self.timeout_s, pool=10.0)
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    url,
                    params={"output_format": OUTPUT_FORMAT},
                    headers={
                        "xi-api-key": self.api_key,
                        "Accept": "audio/pcm",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(payload),
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        raise error_from_status(response.status_code, body[:2048], self.api_key)
                    async for chunk in response.aiter_bytes():
                        if cancel.is_set():
                            raise asyncio.CancelledError()
                        if chunk:
                            yield chunk
        except ConversionError:
            raise
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as exc:
            raise ConversionError(
                "timeout",
                "Text-to-speech timed out. This utterance was not retried.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ConversionError(
                "disconnected",
                "Connection to ElevenLabs failed. This utterance was not retried.",
            ) from exc


class MockTtsClient:
    """Deterministic stand-in. The bytes are not a voice and not the microphone."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def stream(self, voice_id: str, text: str, cancel: asyncio.Event) -> AsyncIterator[bytes]:
        del voice_id
        if cancel.is_set():
            return
        self.texts.append(text)
        raw = text.encode("utf-8")
        pcm = bytes((byte ^ 0x5A) for byte in raw)
        if len(pcm) % 2:
            pcm += b"\x00"
        pcm *= 20
        yield pcm[:1]
        if len(pcm) == 1:
            return
        yield pcm[1:4]
        if len(pcm) > 4:
            yield pcm[4:]
