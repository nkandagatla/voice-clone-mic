"""HTTP client for POST /v1/voice-changer/{voice_id}/stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx

from voice_clone_mic.errors import ConversionError, error_from_status, validate_request_pcm

OUTPUT_FORMAT = "pcm_24000"
INPUT_FORMAT = "pcm_s16le_16"
DEFAULT_MODEL = "eleven_multilingual_sts_v2"


class HttpxVoiceClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_id: str = DEFAULT_MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.transport = transport
        self.timeout_s = timeout_s

    async def stream(
        self,
        voice_id: str,
        pcm: bytes,
        cancel: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        validate_request_pcm(pcm)
        if cancel.is_set():
            raise asyncio.CancelledError()
        url = f"{self.base_url}/v1/voice-changer/{quote(voice_id, safe='')}/stream"
        timeout = httpx.Timeout(connect=10.0, read=self.timeout_s, write=self.timeout_s, pool=10.0)
        headers = {"xi-api-key": self.api_key, "Accept": "audio/pcm"}
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
                    headers=headers,
                    data={"file_format": INPUT_FORMAT, "model_id": self.model_id},
                    files={"audio": ("segment.pcm", pcm, "application/octet-stream")},
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
        except httpx.HTTPError as exc:
            raise ConversionError(
                "disconnected",
                "Connection to ElevenLabs failed. This segment was not retried.",
            ) from exc
