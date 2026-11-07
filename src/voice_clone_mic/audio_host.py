"""Audio host boundary. PortAudio is imported only when a live device is opened."""

from __future__ import annotations

import queue
import threading

from voice_clone_mic.devices import DeviceInfo, plan_stream
from voice_clone_mic.pcm import samples_to_bytes


class CaptureBuffer:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.dropped_bytes = 0
        self._buf = bytearray()
        self._lock = threading.Lock()

    def append(self, data: bytes) -> None:
        with self._lock:
            self._buf.extend(data)
            if len(self._buf) <= self.max_bytes:
                return
            overflow = len(self._buf) - self.max_bytes
            if overflow % 2:
                overflow += 1
            overflow = min(overflow, len(self._buf))
            del self._buf[:overflow]
            self.dropped_bytes += overflow

    def pop_all(self) -> bytes:
        with self._lock:
            data = bytes(self._buf)
            self._buf.clear()
            return data

    def queued_bytes(self) -> int:
        with self._lock:
            return len(self._buf)


def copy_input_channel(indata: object) -> bytes:
    frames = len(indata)  # type: ignore[arg-type]
    samples = [int(indata[index][0]) for index in range(frames)]  # type: ignore[index]
    return samples_to_bytes(samples)


def write_output_block(outdata: object, mono: list[int]) -> None:
    for index, sample in enumerate(mono):
        row = outdata[index]  # type: ignore[index]
        for channel in range(len(row)):
            row[channel] = sample


class SoundDeviceHost:
    """Thin PortAudio wrapper. Unit tests do not construct streams."""

    def __init__(self) -> None:
        self._module = None
        self._streams: list[object] = []
        self.faults: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.status_events = 0

    def _load(self):
        if self._module is None:
            import sounddevice as sd

            self._module = sd
        return self._module

    def list_devices(self) -> list[DeviceInfo]:
        sd = self._load()
        defaults = sd.default.device
        default_in = defaults[0] if isinstance(defaults, (list, tuple)) else -1
        default_out = defaults[1] if isinstance(defaults, (list, tuple)) else -1
        listed: list[DeviceInfo] = []
        for index, info in enumerate(sd.query_devices()):
            listed.append(
                DeviceInfo(
                    index=index,
                    name=str(info["name"]),
                    max_input_channels=int(info["max_input_channels"]),
                    max_output_channels=int(info["max_output_channels"]),
                    default_samplerate=float(info["default_samplerate"]),
                    default_input=index == default_in,
                    default_output=index == default_out,
                )
            )
        return listed

    def open_streams(self, session: object, input_device: DeviceInfo, output_device: DeviceInfo, capture: CaptureBuffer) -> None:
        sd = self._load()
        in_rate, _in_channels = plan_stream(input_device, "input")
        out_rate, out_channels = plan_stream(output_device, "output")

        def remember(message: str) -> None:
            try:
                self.faults.put_nowait(message)
            except Exception:
                return

        def in_callback(indata, frames, time_info, status) -> None:
            del frames, time_info
            if status:
                self.status_events += 1
            try:
                capture.append(copy_input_channel(indata))
            except Exception:
                remember("input callback failed")

        def out_callback(outdata, frames, time_info, status) -> None:
            del time_info
            if status:
                self.status_events += 1
            try:
                mono = session.playback.read_mono(frames)  # type: ignore[attr-defined]
                for channel in range(outdata.shape[1]):
                    outdata[:, channel] = mono
            except Exception:
                outdata.fill(0)
                remember("output callback failed")

        input_stream = self._open_input(sd, input_device, in_rate, in_callback)
        output_stream = sd.OutputStream(
            device=output_device.index,
            channels=out_channels,
            samplerate=out_rate,
            dtype="int16",
            callback=out_callback,
            blocksize=max(1, out_rate // 50),
        )
        input_stream.start()
        output_stream.start()
        self._streams = [input_stream, output_stream]

    def _open_input(self, sd, device: DeviceInfo, sample_rate: int, callback):
        candidates = [1]
        if device.max_input_channels > 1:
            candidates.append(min(2, device.max_input_channels))
        last_error: Exception | None = None
        for channels in candidates:
            try:
                return sd.InputStream(
                    device=device.index,
                    channels=channels,
                    samplerate=sample_rate,
                    dtype="int16",
                    callback=callback,
                    blocksize=max(1, sample_rate // 50),
                )
            except Exception as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    def close(self) -> None:
        for stream in self._streams:
            try:
                stream.stop()  # type: ignore[attr-defined]
                stream.close()  # type: ignore[attr-defined]
            except Exception:
                continue
        self._streams = []

    def poll_inactive(self) -> str | None:
        for stream in self._streams:
            active = getattr(stream, "active", True)
            if not active:
                return "disconnected"
        return None
