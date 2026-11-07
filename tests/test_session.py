import asyncio
import logging

from voice_clone_mic.devices import DeviceInfo
from voice_clone_mic.errors import ConversionError
from voice_clone_mic.mock_client import MockVoiceClient
from voice_clone_mic.pcm import PcmFrameAssembler, bytes_to_samples, samples_to_bytes
from voice_clone_mic.resample import StreamResampler
from voice_clone_mic.segmenter import SpeechSegmenter
from voice_clone_mic.session import Session

MIC = DeviceInfo(0, "Built-in Microphone", 1, 0, 48000, True, False)
CABLE = DeviceInfo(2, "BlackHole 2ch", 2, 2, 48000, False, False)
MIC_16 = DeviceInfo(0, "Built-in Microphone", 1, 0, 16000, True, False)
CABLE_16 = DeviceInfo(2, "BlackHole 2ch", 2, 2, 16000, False, False)


def _phrase(sample_rate: int, loud_ms: int, lead_ms: int = 0, tail_ms: int = 0, amplitude: int = 8000) -> bytes:
    samples = (
        [0] * (sample_rate * lead_ms // 1000)
        + [amplitude] * (sample_rate * loud_ms // 1000)
        + [0] * (sample_rate * tail_ms // 1000)
    )
    return samples_to_bytes(samples)


def _reference(device_pcm: bytes, input_rate: int, output_rate: int, segmenter_kwargs: dict) -> tuple[list[int], int]:
    resampler = StreamResampler(input_rate, 16000)
    api = resampler.process(bytes_to_samples(device_pcm)) + resampler.flush()
    segmenter = SpeechSegmenter(**segmenter_kwargs)
    parts = segmenter.push(api) + segmenter.flush()
    played: list[int] = []
    for part in parts:
        assembler = PcmFrameAssembler()
        converted = assembler.feed(bytes(byte ^ 0xFF for byte in part.pcm))
        assembler.flush()
        output = StreamResampler(24000, output_rate)
        played.extend(output.process(converted) + output.flush())
    return played, len(parts)


def _drain(session: Session) -> list[int]:
    count = session.playback.queued_samples()
    return session.playback.read_mono(count) if count else []


def test_mock_playback_matches_converted_reference_and_not_the_mic():
    audio = _phrase(48000, loud_ms=300, lead_ms=200, tail_ms=500)

    async def run():
        session = Session("mock-voice", MockVoiceClient(), MIC, CABLE, clock=lambda: 10.0)
        await session.feed(audio)
        await session.finish()
        return session

    session = asyncio.run(run())
    got = _drain(session)
    expected, count = _reference(audio, 48000, 48000, {})
    assert count == 1
    assert got == expected
    assert got != bytes_to_samples(audio)
    assert any(got)
    assert session.status()["passthrough"] is False
    assert session.status()["mock"] is True


def test_out_of_order_responses_play_in_segment_order_once_each():
    kwargs = {"pre_roll_ms": 0, "max_segment_ms": 200, "min_speech_ms": 200}
    audio = _phrase(16000, loud_ms=500)

    async def run():
        client = MockVoiceClient(delay_s=[0.05, 0.0, 0.0])
        session = Session(
            "mock-voice",
            client,
            MIC_16,
            CABLE_16,
            clock=lambda: 0.0,
            segmenter=SpeechSegmenter(**kwargs),
            max_in_flight=2,
        )
        await session.feed(audio)
        await session.finish()
        return session, client

    session, client = asyncio.run(run())
    expected, count = _reference(audio, 16000, 16000, kwargs)
    assert len(client.payloads) == count
    assert _drain(session) == expected


def test_mute_cancels_a_blocked_conversion_and_discards_late_audio():
    started = asyncio.Event()
    release = asyncio.Event()

    class Blocker:
        def __init__(self) -> None:
            self.payloads: list[bytes] = []

        async def stream(self, voice_id, pcm, cancel):
            self.payloads.append(pcm)
            started.set()
            await release.wait()
            if cancel.is_set():
                return
            yield b"\x01\x02" * 50

    async def run():
        session = Session("mock-voice", Blocker(), MIC_16, CABLE_16, clock=lambda: 0.0)
        audio = _phrase(16000, loud_ms=200, tail_ms=400)
        feeding = asyncio.create_task(session.feed(audio))
        await asyncio.wait_for(started.wait(), timeout=1)
        await feeding
        old = session.generation
        session.playback.write(old, [7, 7, 7, 7])
        await session.mute()
        release.set()
        await session.wait_idle()
        assert session.playback.snapshot() == []
        assert session.playback.read_mono(4) == [0, 0, 0, 0]
        assert session.playback.write(old, [3, 3]) is False

    asyncio.run(run())


def test_fatal_auth_stays_muted_and_rate_limit_pauses():
    class Boom:
        def __init__(self, exc: ConversionError) -> None:
            self.exc = exc
            self.payloads: list[bytes] = []

        async def stream(self, voice_id, pcm, cancel):
            self.payloads.append(pcm)
            raise self.exc
            yield b""

    async def auth():
        client = Boom(ConversionError("401", "Authentication failed (HTTP 401).", fatal=True))
        session = Session("mock-voice", client, MIC_16, CABLE_16, clock=lambda: 0.0)
        phrase = _phrase(16000, loud_ms=200, tail_ms=400)
        await session.feed(phrase)
        await session.finish()
        await session.feed(phrase)
        await session.finish()
        message = await session.unmute()
        return client, session, message

    client, session, message = asyncio.run(auth())
    assert len(client.payloads) == 1
    assert session.fatal
    assert "authentication failed" in message

    async def limited():
        client = Boom(ConversionError("429", "Rate limited (HTTP 429).", pause=True))
        kwargs = {"pre_roll_ms": 0, "max_segment_ms": 200}
        session = Session(
            "mock-voice",
            client,
            MIC_16,
            CABLE_16,
            segmenter=SpeechSegmenter(**kwargs),
            max_in_flight=1,
        )
        await session.feed(_phrase(16000, loud_ms=600))
        await session.finish()
        return client, session

    limited_client, limited_session = asyncio.run(limited())
    assert len(limited_client.payloads) == 1
    assert limited_session.paused


def test_five_failures_pause_and_timeout_is_not_retried():
    class Boom:
        def __init__(self) -> None:
            self.payloads: list[bytes] = []

        async def stream(self, voice_id, pcm, cancel):
            self.payloads.append(pcm)
            raise ConversionError("5xx", "ElevenLabs server error (HTTP 500).", fatal=False)
            yield b""

    async def fail_many():
        client = Boom()
        kwargs = {"pre_roll_ms": 0, "max_segment_ms": 200, "min_speech_ms": 200}
        session = Session(
            "mock-voice",
            client,
            MIC_16,
            CABLE_16,
            segmenter=SpeechSegmenter(**kwargs),
            max_in_flight=1,
            failure_pause_after=5,
        )
        audio = _phrase(16000, loud_ms=1200)
        await session.feed(audio)
        await session.finish()
        _expected, count = _reference(audio, 16000, 16000, kwargs)
        return client, session, count

    client, session, count = asyncio.run(fail_many())
    assert count > 5
    assert len(client.payloads) == 5
    assert session.paused

    class Slow:
        def __init__(self) -> None:
            self.payloads: list[bytes] = []

        async def stream(self, voice_id, pcm, cancel):
            self.payloads.append(pcm)
            await asyncio.sleep(1)
            yield b"\x01\x02"

    async def timeout():
        slow = Slow()
        session = Session(
            "mock-voice",
            slow,
            MIC_16,
            CABLE_16,
            request_timeout_s=0.05,
        )
        await session.feed(_phrase(16000, loud_ms=200, tail_ms=400))
        await session.finish()
        return slow, session

    slow, timed = asyncio.run(timeout())
    assert len(slow.payloads) == 1
    assert timed.playback.snapshot() == []
    assert timed.last_error is not None and "not retried" in timed.last_error


def test_overflow_drops_unsent_audio_and_device_fault_clears_playback():
    async def overflow():
        client = MockVoiceClient()
        kwargs = {"pre_roll_ms": 0, "max_segment_ms": 200, "min_speech_ms": 200}
        session = Session(
            "mock-voice",
            client,
            MIC_16,
            CABLE_16,
            segmenter=SpeechSegmenter(**kwargs),
            max_unsent_ms=250,
            max_in_flight=1,
        )
        await session.feed(_phrase(16000, loud_ms=1000))
        await session.finish()
        return client, session

    client, session = asyncio.run(overflow())
    assert session.dropped_segments > 0
    assert len(client.payloads) < 5

    async def fault():
        session = Session("mock-voice", MockVoiceClient(), MIC_16, CABLE_16)
        session.playback.write(session.generation, [1, 2, 3, 4])
        await session.on_device_fault("disconnected")
        return session

    failed = asyncio.run(fault())
    assert failed.playback.read_mono(4) == [0, 0, 0, 0]
    assert failed.device_fault == "disconnected"


def test_logs_do_not_include_pcm_bytes(caplog):
    caplog.set_level(logging.INFO, logger="voice_clone_mic")

    async def run():
        session = Session("mock-voice", MockVoiceClient(), MIC_16, CABLE_16)
        await session.feed(_phrase(16000, loud_ms=200, tail_ms=400, amplitude=12345))
        await session.finish()
        return session

    session = asyncio.run(run())
    assert session.client.payloads
    assert session.client.payloads[0][:12].hex() not in caplog.text
    assert "super-secret-key" not in caplog.text
