import asyncio
import logging
import threading

from voice_clone_mic.devices import DeviceInfo
from voice_clone_mic.errors import ConversionError
from voice_clone_mic.pcm import samples_to_bytes
from voice_clone_mic.ptt.session import PttSession, Transcript
from voice_clone_mic.ptt.tts import MockTtsClient

MIC = DeviceInfo(0, "Built-in Microphone", 1, 0, 48000, True, False)
CABLE = DeviceInfo(2, "BlackHole 2ch", 2, 2, 48000, False, False)


def _loud(ms: int = 300) -> bytes:
    return samples_to_bytes([8000] * (48000 * ms // 1000))


def _quiet() -> bytes:
    return samples_to_bytes([0] * 48000)


def _good(text: str) -> Transcript:
    return Transcript(
        text,
        [{"text": text, "no_speech_prob": 0.05, "avg_logprob": -0.2, "compression_ratio": 1.1}],
    )


class Scripted:
    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.calls = 0

    def transcribe(self, samples) -> Transcript:
        self.calls += 1
        self.last = list(samples)
        return self.transcript


def test_loud_utterance_is_transcribed_once_and_is_not_the_microphone():
    stt = Scripted(_good("hello there"))
    tts = MockTtsClient()
    session = PttSession(stt, tts, "voice", MIC, CABLE)
    audio = _loud()
    result = asyncio.run(session.submit(audio))
    assert result.spoken is True
    assert result.text == "hello there"
    assert stt.calls == 1
    assert tts.texts == ["hello there"]
    played = session.playback.snapshot()
    assert any(played)
    assert played != list(samples_to_bytes(audio)[: len(played)])


def test_silence_and_hallucination_prone_transcripts_do_not_call_tts():
    quiet_stt = Scripted(_good("should not run"))
    quiet = PttSession(quiet_stt, MockTtsClient(), "voice", MIC, CABLE)
    assert asyncio.run(quiet.submit(_quiet())).reason == "silence"
    assert quiet_stt.calls == 0

    hallucination = Scripted(
        Transcript(
            "Thank you",
            [{"text": "Thank you", "no_speech_prob": 0.9, "avg_logprob": -0.1, "compression_ratio": 1.0}],
        )
    )
    tts = MockTtsClient()
    session = PttSession(hallucination, tts, "voice", MIC, CABLE)
    result = asyncio.run(session.submit(_loud()))
    assert result.reason == "no_speech"
    assert tts.texts == []


def test_cancel_during_transcription_and_tts_discards_audio():
    class Slow:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.calls = 0

        def transcribe(self, samples) -> Transcript:
            del samples
            self.calls += 1
            self.started.set()
            self.release.wait(timeout=2)
            return _good("late")

    stt = Slow()
    tts = MockTtsClient()
    session = PttSession(stt, tts, "voice", MIC, CABLE)

    async def run():
        task = asyncio.create_task(session.submit(_loud()))
        await asyncio.to_thread(stt.started.wait)
        session.cancel()
        stt.release.set()
        return await task

    result = asyncio.run(run())
    assert result.reason == "cancelled"
    assert tts.texts == []
    assert session.playback.snapshot() == []

    class BlockingTts:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.texts: list[str] = []

        async def stream(self, voice_id, text, cancel):
            del voice_id
            self.texts.append(text)
            self.started.set()
            await self.release.wait()
            if cancel.is_set():
                return
            yield b"\x01\x02" * 50

    blocker = BlockingTts()
    session = PttSession(Scripted(_good("speak")), blocker, "voice", MIC, CABLE)

    async def during_tts():
        task = asyncio.create_task(session.submit(_loud()))
        await blocker.started.wait()
        session.cancel()
        blocker.release.set()
        result = await task
        return result

    spoken = asyncio.run(during_tts())
    assert spoken.spoken is False
    assert session.playback.snapshot() == []


def test_auth_failure_blocks_the_next_utterance_and_audio_stays_clear():
    class Denied:
        def __init__(self) -> None:
            self.texts: list[str] = []

        async def stream(self, voice_id, text, cancel):
            del voice_id, cancel
            self.texts.append(text)
            raise ConversionError("401", "Authentication failed (HTTP 401).", fatal=True)
            yield b""

    tts = Denied()
    session = PttSession(Scripted(_good("hello")), tts, "voice", MIC, CABLE)
    first = asyncio.run(session.submit(_loud()))
    second = asyncio.run(session.submit(_loud()))
    assert first.reason == "401"
    assert second.reason == "paused"
    assert tts.texts == ["hello"]
    assert session.playback.snapshot() == []


def test_review_cancel_does_not_call_tts_and_edit_is_sent_once():
    tts = MockTtsClient()
    cancelled = PttSession(
        Scripted(_good("hello there")),
        tts,
        "voice",
        MIC,
        CABLE,
        review=lambda text: None,
    )
    assert asyncio.run(cancelled.submit(_loud())).reason == "cancelled"
    assert tts.texts == []

    edited = PttSession(
        Scripted(_good("hello there")),
        tts,
        "voice",
        MIC,
        CABLE,
        review=lambda text: "edited line",
    )
    result = asyncio.run(edited.submit(_loud()))
    assert result.text == "edited line"
    assert tts.texts == ["edited line"]


def test_two_utterances_keep_text_order(caplog):
    caplog.set_level(logging.INFO, logger="voice_clone_mic.ptt")

    class Switching:
        def __init__(self) -> None:
            self.calls = 0

        def transcribe(self, samples) -> Transcript:
            del samples
            self.calls += 1
            return _good("alpha" if self.calls == 1 else "beta")

    tts = MockTtsClient()
    session = PttSession(Switching(), tts, "voice", MIC, CABLE, max_seconds=0.05)

    async def run():
        await session.submit(_loud(1000))
        await session.submit(_loud(80))

    asyncio.run(run())
    assert tts.texts == ["alpha", "beta"]
    assert session.last_dropped > 0
    assert "alpha" not in caplog.text
    assert "beta" not in caplog.text
    assert "super-secret-key" not in caplog.text


def test_submit_reports_transcribing_generating_and_playing():
    session = PttSession(Scripted(_good("hello there")), MockTtsClient(), "voice", MIC, CABLE)
    phases: list[str] = []
    result = asyncio.run(session.submit(_loud(), on_phase=phases.append))
    assert result.spoken is True
    assert phases == ["transcribing", "generating", "playing"]
