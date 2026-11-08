import asyncio

import httpx
import pytest

from voice_clone_mic.ptt.tts import HttpxTtsClient, MockTtsClient


def test_mock_tts_records_one_text_and_odd_chunks():
    client = MockTtsClient()

    async def run():
        chunks = []
        async for chunk in client.stream("voice123", "hello", asyncio.Event()):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())
    assert client.texts == ["hello"]
    assert chunks[0] == chunks[0][:1]
    assert b"".join(chunks)
    assert len(b"".join(chunks)) % 2 == 0


def test_tts_stream_contract_and_redacted_auth_error():
    secret = "super-secret-key"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, content=b"\x01\x02\x03\x04")

    client = HttpxTtsClient(
        secret,
        "https://api.elevenlabs.io",
        "eleven_flash_v2_5",
        transport=httpx.MockTransport(handler),
    )

    async def run():
        data = b""
        async for chunk in client.stream("voice123", "hello there", asyncio.Event()):
            data += chunk
        return data

    assert asyncio.run(run()) == b"\x01\x02\x03\x04"
    assert seen["url"].endswith("/v1/text-to-speech/voice123/stream?output_format=pcm_24000")
    assert b"optimize_streaming_latency" not in seen["url"].encode()
    assert b"optimize_streaming_latency" not in seen["body"]
    assert b"eleven_flash_v2_5" in seen["body"]
    assert b"hello there" in seen["body"]
    assert b"language_code" in seen["body"]

    multi = {}

    def multilingual(request: httpx.Request) -> httpx.Response:
        multi["body"] = request.content
        return httpx.Response(401, json={"detail": {"message": "bad super-secret-key"}})

    failing = HttpxTtsClient(
        secret,
        "https://api.elevenlabs.io",
        "eleven_multilingual_v2",
        transport=httpx.MockTransport(multilingual),
    )

    async def boom():
        async for _chunk in failing.stream("voice123", "hello", asyncio.Event()):
            pass

    with pytest.raises(Exception) as caught:
        asyncio.run(boom())
    assert caught.value.fatal is True
    assert secret not in str(caught.value)
    assert b"language_code" not in multi["body"]
