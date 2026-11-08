"""One finalized utterance: local transcript, then one TTS stream."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from voice_clone_mic.devices import DeviceInfo, plan_stream
from voice_clone_mic.errors import ConversionError
from voice_clone_mic.pcm import PcmFrameAssembler, bytes_to_samples
from voice_clone_mic.playback import PlaybackBuffer
from voice_clone_mic.ptt.capture import BoundedCapture
from voice_clone_mic.ptt.gate import MAX_TTS_CHARS, Decision, assess_transcript, rms
from voice_clone_mic.resample import StreamResampler

logger = logging.getLogger("voice_clone_mic.ptt")


@dataclass
class Transcript:
    text: str
    segments: list[dict] = field(default_factory=list)


@dataclass
class UtteranceResult:
    reason: str
    text: str
    spoken: bool


class PttSession:
    def __init__(
        self,
        transcriber: object,
        tts: object,
        voice_id: str,
        input_device: DeviceInfo,
        output_device: DeviceInfo,
        review=None,
        max_seconds: float = 20.0,
        timeout_s: float = 30.0,
    ) -> None:
        input_rate, _channels = plan_stream(input_device, "input")
        output_rate, output_channels = plan_stream(output_device, "output")
        self.transcriber = transcriber
        self.tts = tts
        self.voice_id = voice_id
        self.input_device = input_device
        self.output_device = output_device
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.review = review
        self.max_seconds = max_seconds
        self.timeout_s = timeout_s
        self.max_samples = max(1, int(input_rate * max_seconds))
        self.playback = PlaybackBuffer(output_rate, output_channels)
        self.generation = self.playback.generation
        self._cancel = asyncio.Event()
        self.fatal = False
        self.paused = False
        self.last_error: str | None = None
        self.last_dropped = 0
        self._capture_resampler = StreamResampler(input_rate, 16000)

    def cancel(self) -> None:
        self._cancel.set()
        self.generation = self.playback.clear_and_bump()
        self._cancel = asyncio.Event()

    async def submit(self, device_pcm: bytes) -> UtteranceResult:
        generation = self.generation
        cancel = self._cancel
        if self.fatal or self.paused:
            return UtteranceResult("paused", "", False)
        try:
            device_samples = bytes_to_samples(device_pcm)
        except ValueError:
            self.last_error = "Ignored a partial capture frame."
            return UtteranceResult("rejected", "", False)
        if generation != self.generation:
            return UtteranceResult("cancelled", "", False)
        capture = BoundedCapture(self.max_samples)
        capture.extend(device_samples)
        self.last_dropped = capture.dropped
        heard = self._capture_resampler.process(capture.snapshot())
        heard.extend(self._capture_resampler.flush())
        self._capture_resampler.reset()
        level = rms(heard)
        decision = assess_transcript("", [], level)
        if decision.reason == "silence":
            return decision_result(decision)
        try:
            transcript = await asyncio.to_thread(self.transcriber.transcribe, heard)
        except Exception:
            logger.info("transcription_failed")
            self.last_error = "Local transcription failed. Nothing was spoken."
            return UtteranceResult("stt_error", "", False)
        if generation != self.generation or cancel.is_set():
            return UtteranceResult("cancelled", "", False)
        decision = assess_transcript(transcript.text, transcript.segments, level)
        if not decision.speak:
            return decision_result(decision)
        text = decision.text
        if self.review is not None:
            edited = self.review(text)
            if edited is None or generation != self.generation or cancel.is_set():
                return UtteranceResult("cancelled", "", False)
            text = " ".join(str(edited).split())
            if not text:
                return UtteranceResult("empty", "", False)
            if len(text) > MAX_TTS_CHARS:
                return UtteranceResult("too_long", "", False)
        return await self._speak(generation, cancel, text)

    async def _speak(self, generation: int, cancel: asyncio.Event, text: str) -> UtteranceResult:
        assembler = PcmFrameAssembler()
        resampler = StreamResampler(24000, self.output_rate)
        try:
            async with asyncio.timeout(self.timeout_s):
                async for chunk in self.tts.stream(self.voice_id, text, cancel):
                    if generation != self.generation or cancel.is_set():
                        return UtteranceResult("cancelled", "", False)
                    converted = resampler.process(assembler.feed(chunk))
                    if converted and generation == self.generation:
                        self.playback.write(generation, converted)
                if generation != self.generation or cancel.is_set():
                    return UtteranceResult("cancelled", "", False)
                assembler.flush()
                tail = resampler.flush()
                if tail and generation == self.generation:
                    self.playback.write(generation, tail)
        except TimeoutError:
            self.last_error = "Text-to-speech timed out. This utterance was not retried."
            self.cancel()
            return UtteranceResult("timeout", "", False)
        except ConversionError as exc:
            self.last_error = str(exc)
            if exc.fatal:
                self.fatal = True
            elif exc.pause:
                self.paused = True
            self.cancel()
            return UtteranceResult(exc.code, "", False)
        if generation != self.generation:
            return UtteranceResult("cancelled", "", False)
        logger.info("utterance_spoken chars=%s", len(text))
        return UtteranceResult("ok", text, True)


def decision_result(decision: Decision) -> UtteranceResult:
    return UtteranceResult(decision.reason, decision.text, decision.speak)
