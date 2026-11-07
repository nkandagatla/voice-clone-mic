import asyncio

import httpx
import pytest

from voice_clone_mic.client import HttpxVoiceClient
from voice_clone_mic.errors import error_from_status, require_little_endian, validate_api_base


class _Parts(httpx.AsyncByteStream):
    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part

    async def aclose(self) -> None:
        return None


def _transport(status: int, body: bytes | None = None, parts: list[bytes] | None = None, seen: dict | None = None):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if seen is not None:
            seen["url"] = str(request.url)
            seen["body"] = request.content
            seen["calls"] = calls["n"]
        if parts is not None:
            return httpx.Response(status, stream=_Parts(parts))
        return httpx.Response(status, content=body or b"")

    return httpx.MockTransport(handler), calls


def test_stream_posts_pcm_without_deprecated_latency_param():
    seen: dict = {}
    parts = [b"\x01", b"\x02\x03", b"\x04"]
    transport, _calls = _transport(200, parts=parts, seen=seen)

    async def run() -> bytes:
        client = HttpxVoiceClient(
            "test-key",
            "https://api.elevenlabs.io",
            transport=transport,
        )
        chunks = []
        async for chunk in client.stream("voice123", b"\x10\x20\x30\x40", asyncio.Event()):
            chunks.append(chunk)
        return b"".join(chunks)

    assert asyncio.run(run()) == b"\x01\x02\x03\x04"
    assert seen["url"].endswith("/v1/voice-changer/voice123/stream?output_format=pcm_24000")
    assert b"optimize_streaming_latency" not in seen["url"].encode()
    assert b"optimize_streaming_latency" not in seen["body"]
    assert b"pcm_s16le_16" in seen["body"]
    assert b"eleven_multilingual_sts_v2" in seen["body"]
    assert b"\x10\x20\x30\x40" in seen["body"]


def test_error_bodies_are_sanitized_and_redirects_are_not_followed():
    secret = "super-secret-key"
    body = b'{"detail":[{"msg":"bad key super-secret-key"}]}'
    transport, calls = _transport(401, body)
    client = HttpxVoiceClient(secret, "https://api.elevenlabs.io", transport=transport)

    async def once(status_client=client):
        async for _chunk in status_client.stream("voice123", b"\x00\x01", asyncio.Event()):
            pass

    with pytest.raises(Exception) as exc:
        asyncio.run(once())
    assert exc.value.fatal is True
    assert secret not in str(exc.value)
    assert calls["n"] == 1

    for status, fatal, pause in ((403, True, False), (422, False, False), (429, False, True), (500, False, False)):
        transport, _calls = _transport(status, body)
        failing = HttpxVoiceClient(secret, "https://api.elevenlabs.io", transport=transport)

        async def attempt(active=failing):
            async for _chunk in active.stream("voice123", b"\x00\x01", asyncio.Event()):
                pass

        with pytest.raises(Exception) as caught:
            asyncio.run(attempt())
        assert caught.value.fatal is fatal
        assert caught.value.pause is pause
        assert secret not in str(caught.value)

    transport, calls = _transport(302, b"go-away")
    redirected = HttpxVoiceClient(secret, "https://api.elevenlabs.io", transport=transport)

    async def follow():
        async for _chunk in redirected.stream("voice123", b"\x00\x01", asyncio.Event()):
            pass

    with pytest.raises(Exception) as caught:
        asyncio.run(follow())
    assert "redirect" in str(caught.value).lower() or caught.value.code == "redirect"
    assert calls["n"] == 1


def test_api_base_allow_list_and_endian_guard():
    assert validate_api_base("https://api.eu.residency.elevenlabs.io/") == (
        "https://api.eu.residency.elevenlabs.io"
    )
    with pytest.raises(Exception):
        validate_api_base("http://api.elevenlabs.io")
    with pytest.raises(Exception):
        validate_api_base("https://evil.elevenlabs.io.attacker.test")
    with pytest.raises(Exception):
        require_little_endian("big")
    assert error_from_status(500, b"\xff\xfe", "secret").code == "5xx"
