"""Segmented conversion session. The output path accepts converted audio only."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable

from voice_clone_mic.devices import DeviceInfo, plan_stream
from voice_clone_mic.errors import ConversionError, validate_request_pcm
from voice_clone_mic.mock_client import MockVoiceClient
from voice_clone_mic.ordering import Released, SequenceGate
from voice_clone_mic.pcm import PcmFrameAssembler, bytes_to_samples
from voice_clone_mic.playback import PlaybackBuffer
from voice_clone_mic.resample import StreamResampler
from voice_clone_mic.segmenter import Segment, SpeechSegmenter

logger = logging.getLogger("voice_clone_mic")

API_INPUT_RATE = 16000
API_OUTPUT_RATE = 24000


class Session:
    def __init__(
        self,
        voice_id: str,
        client: object,
        input_device: DeviceInfo,
        output_device: DeviceInfo,
        clock: Callable[[], float] | None = None,
        segmenter: SpeechSegmenter | None = None,
        max_unsent_ms: int = 8000,
        max_in_flight: int = 2,
        request_timeout_s: float = 20.0,
        failure_pause_after: int = 5,
        max_hold_ms: int = 12000,
    ) -> None:
        input_rate, _channels = plan_stream(input_device, "input")
        output_rate, output_channels = plan_stream(output_device, "output")
        self.voice_id = voice_id
        self.client = client
        self.input_device = input_device
        self.output_device = output_device
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.output_channels = output_channels
        self.clock = clock or time.monotonic
        self.segmenter = segmenter or SpeechSegmenter(generation=1)
        if self.segmenter.sample_rate != API_INPUT_RATE:
            raise ValueError("segmenter must run at 16000 Hz")
        self.max_unsent_ms = max_unsent_ms
        self.max_in_flight = max_in_flight
        self.request_timeout_s = request_timeout_s
        self.failure_pause_after = failure_pause_after
        self.capture_resampler = StreamResampler(input_rate, API_INPUT_RATE)
        self.playback = PlaybackBuffer(output_rate, output_channels)
        self.generation = self.playback.generation
        self.segmenter.generation = self.generation
        self.gate = SequenceGate(max(1, output_rate * max_hold_ms // 1000))
        self.gate.reset(self.generation)
        self.unsent: deque[Segment] = deque()
        self.unsent_ms = 0
        self.tasks: dict[int, asyncio.Task[None]] = {}
        self._cancels: dict[int, asyncio.Event] = {}
        self._inflight_duration: dict[int, int] = {}
        self.submitted: set[int] = set()
        self._ready: dict[int, float] = {}
        self._timed: set[int] = set()
        self._played_ids: set[int] = set()
        self._failed_ids: set[int] = set()
        self.user_muted = False
        self.fatal = False
        self.paused = False
        self._stopped = False
        self.device_fault: str | None = None
        self.consecutive_failures = 0
        self.dropped_segments = 0
        self.sent_segments = 0
        self.failed_segments = 0
        self.observed_last_segment_ms: int | None = None
        self.last_error: str | None = None
        self.capture = None

    def status(self) -> dict[str, object]:
        return {
            "state": self._state(),
            "mock": isinstance(self.client, MockVoiceClient),
            "passthrough": False,
            "generation": self.generation,
            "input_name": self.input_device.name,
            "output_name": self.output_device.name,
            "sent_segments": self.sent_segments,
            "played_segments": len(self._played_ids),
            "failed_segments": self.failed_segments,
            "dropped_segments": self.dropped_segments,
            "capture_backlog_ms": self._capture_backlog_ms(),
            "unsent_ms": self.unsent_ms,
            "inflight_ms": sum(self._inflight_duration.values()),
            "held_ms": self.gate.held_samples() * 1000 // self.output_rate,
            "playback_ms": self.playback.queued_ms(),
            "underruns": self.playback.underruns,
            "in_flight_count": len(self.tasks),
            "observed_last_segment_ms": self.observed_last_segment_ms,
            "last_error": self.last_error,
            "fatal": self.fatal,
            "paused": self.paused,
        }

    async def feed(self, device_pcm: bytes) -> None:
        if not device_pcm or not self._accepting_audio():
            return
        try:
            samples = bytes_to_samples(device_pcm)
        except ValueError:
            self.last_error = "Ignored a partial capture frame."
            return
        self._queue(self.segmenter.push(self.capture_resampler.process(samples)))

    async def finish(self) -> None:
        if self._accepting_audio():
            self._queue(self.segmenter.push(self.capture_resampler.flush()))
            self._queue(self.segmenter.flush())
        await self.wait_idle()

    async def wait_idle(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
            if self._accepting_audio():
                self._pump()
            else:
                break

    async def mute(self) -> None:
        self.user_muted = True
        await self._discard()

    async def unmute(self) -> str:
        if self.fatal:
            return "staying muted: authentication failed"
        if self.device_fault:
            return "staying muted: audio device failed"
        self.user_muted = False
        self.paused = False
        self.consecutive_failures = 0
        self._pump()
        return "unmuted"

    async def stop(self) -> None:
        self._stopped = True
        self.user_muted = True
        await self._discard()

    async def on_device_fault(self, message: str) -> None:
        self.device_fault = message
        self.last_error = "Audio device disconnected. Output stays silent."
        self.user_muted = True
        await self._discard()

    def _capture_backlog_ms(self) -> int:
        capture = self.capture
        if capture is None or self.input_rate <= 0:
            return 0
        return capture.queued_bytes() * 1000 // (self.input_rate * 2)

    def _accepting_audio(self) -> bool:
        return not (
            self.user_muted or self.fatal or self.paused or self._stopped or self.device_fault
        )

    def _state(self) -> str:
        if self._stopped:
            return "stopped"
        if self.device_fault:
            return "device_fault"
        if self.fatal:
            return "fatal"
        if self.paused:
            return "paused"
        if self.user_muted:
            return "muted"
        return "running"

    def _queue(self, segments: list[Segment]) -> None:
        for segment in segments:
            self._ready[segment.sequence] = self.clock()
            self.unsent.append(segment)
            self.unsent_ms += segment.duration_ms
            while self.unsent_ms > self.max_unsent_ms and len(self.unsent) > 1:
                old = self.unsent.popleft()
                self.unsent_ms -= old.duration_ms
                self.dropped_segments += 1
                self._fail(self.generation, old.sequence, count_failure=False)
            if self.unsent and self.unsent_ms > self.max_unsent_ms:
                only = self.unsent.popleft()
                self.unsent_ms -= only.duration_ms
                self.dropped_segments += 1
                self._fail(self.generation, only.sequence, count_failure=False)
        self._pump()

    def _pump(self) -> None:
        if not self._accepting_audio():
            return
        while self.unsent and len(self.tasks) < self.max_in_flight:
            segment = self.unsent.popleft()
            self.unsent_ms -= segment.duration_ms
            if segment.sequence in self.submitted:
                continue
            self.submitted.add(segment.sequence)
            self._inflight_duration[segment.sequence] = segment.duration_ms
            self.tasks[segment.sequence] = asyncio.create_task(self._convert(segment))

    async def _convert(self, segment: Segment) -> None:
        generation = segment.generation
        cancel = asyncio.Event()
        self._cancels[segment.sequence] = cancel
        assembler = PcmFrameAssembler()
        resampler = StreamResampler(API_OUTPUT_RATE, self.output_rate)
        try:
            if generation != self.generation or not self._accepting_audio():
                self._fail(generation, segment.sequence, count_failure=False)
                return
            try:
                validate_request_pcm(segment.pcm)
            except ConversionError as exc:
                self.last_error = str(exc)
                self._fail(generation, segment.sequence, count_failure=False)
                return
            logger.info(
                "segment_send seq=%s duration_ms=%s bytes=%s",
                segment.sequence,
                segment.duration_ms,
                len(segment.pcm),
            )
            async with asyncio.timeout(self.request_timeout_s):
                async for chunk in self.client.stream(self.voice_id, segment.pcm, cancel):
                    if not self._live(generation):
                        return
                    self._emit(
                        generation,
                        segment.sequence,
                        resampler.process(assembler.feed(chunk)),
                        finished=False,
                    )
                if not self._live(generation):
                    return
                assembler.flush()
                self._emit(generation, segment.sequence, resampler.flush(), finished=True)
            self.consecutive_failures = 0
            self.sent_segments += 1
        except TimeoutError:
            cancel.set()
            self.last_error = "Conversion timed out. This segment was not retried."
            self._fail(generation, segment.sequence, count_failure=True)
            if self.paused:
                self._silence_pending()
        except asyncio.CancelledError:
            self._fail(generation, segment.sequence, count_failure=False)
            raise
        except ConversionError as exc:
            self.last_error = str(exc)
            self._fail(generation, segment.sequence, count_failure=False)
            if exc.fatal:
                self.fatal = True
                self.user_muted = True
                self._silence_pending()
            elif exc.pause:
                self.paused = True
                self._silence_pending()
            else:
                self._note_failure()
                if self.paused:
                    self._silence_pending()
        finally:
            self.tasks.pop(segment.sequence, None)
            self._cancels.pop(segment.sequence, None)
            self._inflight_duration.pop(segment.sequence, None)
            if self._live(generation) and self._accepting_audio():
                self._pump()

    def _emit(self, generation: int, sequence: int, samples: list[int], finished: bool) -> None:
        if not self._live(generation):
            return
        released = self.gate.push(generation, sequence, samples, finished)
        wrote = False
        failed = False
        for item in released:
            if item.sequence == sequence and item.samples and not item.failed:
                wrote = True
            if item.sequence == sequence and item.failed:
                failed = True
            self._take(generation, item)
        if finished and not failed and (wrote or sequence in self._timed):
            self._played_ids.add(sequence)

    def _take(self, generation: int, item: Released) -> None:
        if generation != self.generation:
            return
        if item.failed:
            if item.sequence not in self._failed_ids:
                self._failed_ids.add(item.sequence)
                self.failed_segments += 1
            return
        if not item.samples:
            return
        if self.playback.write(generation, item.samples) and item.sequence not in self._timed:
            self._timed.add(item.sequence)
            ready = self._ready.get(item.sequence)
            if ready is not None:
                self.observed_last_segment_ms = int((self.clock() - ready) * 1000)

    def _fail(self, generation: int, sequence: int, count_failure: bool) -> None:
        if count_failure:
            self._note_failure()
        for item in self.gate.fail(generation, sequence):
            self._take(generation, item)

    def _note_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_pause_after:
            self.paused = True

    def _live(self, generation: int) -> bool:
        return generation == self.generation and not self._stopped

    async def _discard(self) -> None:
        tasks = self._silence_pending()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _silence_pending(self) -> list[asyncio.Task[None]]:
        generation = self.playback.clear_and_bump()
        self.generation = generation
        self.gate.reset(generation)
        self.segmenter.reset()
        self.segmenter.generation = generation
        self.capture_resampler.reset()
        self.unsent.clear()
        self.unsent_ms = 0
        current = asyncio.current_task()
        pending: list[asyncio.Task[None]] = []
        for event in list(self._cancels.values()):
            event.set()
        for task in list(self.tasks.values()):
            if task is not current and not task.done():
                task.cancel()
                pending.append(task)
        return pending
