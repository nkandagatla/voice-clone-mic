"""CLI for push-to-talk. Demo and mock runs do not open devices or the network."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

from voice_clone_mic.audio_host import CaptureBuffer
from voice_clone_mic.config import load_settings
from voice_clone_mic.devices import fake_catalog, select_devices
from voice_clone_mic.errors import require_little_endian, validate_api_base
from voice_clone_mic.pcm import samples_to_bytes
from voice_clone_mic.ptt.session import PttSession, Transcript
from voice_clone_mic.ptt.tts import DEFAULT_TTS_MODEL, HttpxTtsClient, MockTtsClient
from voice_clone_mic.ptt.whisper_backend import (
    DEFAULT_MODEL,
    LocalModelMissing,
    LocalWhisperTranscriber,
    WhisperNotInstalled,
    prepare_official_model,
)


class _DemoTranscriber:
    def transcribe(self, samples) -> Transcript:
        del samples
        return Transcript(
            "hello there",
            [
                {
                    "text": "hello there",
                    "no_speech_prob": 0.05,
                    "avg_logprob": -0.2,
                    "compression_ratio": 1.1,
                }
            ],
        )


def _demo_pcm(sample_rate: int) -> bytes:
    return samples_to_bytes([8000] * (sample_rate * 300 // 1000))


def dispatch(args) -> int:
    try:
        if args.ptt_command == "demo":
            return asyncio.run(_demo())
        if args.ptt_command == "prepare-model":
            return _prepare(args)
        if args.ptt_command == "run":
            return asyncio.run(_run(args))
    except WhisperNotInstalled as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except LocalModelMissing as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


async def _demo() -> int:
    require_little_endian()
    chosen_in, chosen_out = select_devices(fake_catalog(), None, "BlackHole", False)
    session = PttSession(_DemoTranscriber(), MockTtsClient(), "mock-voice", chosen_in, chosen_out)
    result = await session.submit(_demo_pcm(session.input_rate))
    if not result.spoken or not any(session.playback.snapshot()):
        print("ptt demo produced no speech", file=sys.stderr)
        return 1
    print("mode=ptt mock=true passthrough=false")
    print(f"preview={result.text}")
    print("No audio was sent to ElevenLabs.")
    return 0


def _prepare(args) -> int:
    path = prepare_official_model(Path(args.dir), args.model)
    print(f"Prepared {path}")
    return 0


async def _run(args) -> int:
    require_little_endian()
    if not args.mock and not args.i_accept_api_billing:
        print(
            "Refusing to contact ElevenLabs without --i-accept-api-billing. No request was sent.",
            file=sys.stderr,
        )
        return 2
    settings = load_settings(dict(os.environ), Path.cwd())
    voice_id = args.voice_id or settings.tts_voice_id or settings.voice_id
    if args.mock and not voice_id:
        voice_id = "mock-voice"
    if not voice_id:
        print("A voice id is required. No request was sent.", file=sys.stderr)
        return 2
    if args.fake:
        devices = fake_catalog()
    else:
        from voice_clone_mic.audio_host import SoundDeviceHost

        try:
            devices = SoundDeviceHost().list_devices()
        except OSError as exc:
            print(
                "PortAudio could not be loaded, so no live devices were opened. "
                f"{exc}",
                file=sys.stderr,
            )
            return 1
        except Exception as exc:
            print(f"Could not query audio devices. {type(exc).__name__}", file=sys.stderr)
            return 1
    chosen_in, chosen_out = select_devices(
        devices,
        args.input or settings.input_device,
        args.output or settings.output_device,
        args.allow_any_output,
    )
    if args.mock:
        transcriber = _DemoTranscriber()
        tts = MockTtsClient()
    else:
        root = Path(".vcm/whisper")
        transcriber = LocalWhisperTranscriber(root, DEFAULT_MODEL)
        if not settings.api_key:
            print("ELEVENLABS_API_KEY is missing. No request was sent.", file=sys.stderr)
            return 2
        base = validate_api_base(settings.api_base)
        tts = HttpxTtsClient(settings.api_key, base, settings.tts_model or DEFAULT_TTS_MODEL)
    reviewer = _review_from_stdin if args.review else None
    session = PttSession(
        transcriber,
        tts,
        voice_id,
        chosen_in,
        chosen_out,
        review=reviewer,
    )
    if args.once:
        result = await session.submit(_demo_pcm(session.input_rate))
        _print_result(result, args.mock)
        return 0 if result.spoken else 1
    if args.fake:
        return await _fake_enter_loop(session, args.mock)
    return await _device_enter_loop(session, chosen_in, args.mock)


def _review_from_stdin(text: str) -> str | None:
    print(f"preview={text}")
    print("Press Enter to speak this text, type a replacement, or type cancel.")
    line = sys.stdin.readline()
    if not line or line.strip().lower() == "cancel":
        return None
    if line.strip() == "":
        return text
    return line.strip()


def _print_result(result, mock: bool) -> None:
    print("mode=ptt mock=true passthrough=false" if mock else "mode=ptt mock=false passthrough=false")
    if result.text:
        print(f"preview={result.text}")
    print(f"reason={result.reason}")
    if mock:
        print("No audio was sent to ElevenLabs.")


async def _fake_enter_loop(session: PttSession, mock: bool) -> int:
    print("Press Enter to arm, Enter again to send one fake utterance. Type quit to exit.")
    armed = False
    for line in sys.stdin:
        if line.strip().lower() in {"quit", "stop"}:
            session.cancel()
            return 0
        if not armed:
            armed = True
            print("armed")
            continue
        armed = False
        result = await session.submit(_demo_pcm(session.input_rate))
        _print_result(result, mock)
    return 0


async def _device_enter_loop(session: PttSession, input_device, mock: bool) -> int:
    from voice_clone_mic.audio_host import SoundDeviceHost

    host = SoundDeviceHost()
    capture = CaptureBuffer(max_bytes=session.input_rate * 2 * int(session.max_seconds))
    try:
        host.open_streams(session, input_device, session.output_device, capture)
    except Exception as exc:
        print(f"Could not open the selected audio devices. {type(exc).__name__}", file=sys.stderr)
        host.close()
        return 1
    print("Press Enter to start recording. Press Enter again to stop. Type quit to exit.")
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str] = asyncio.Queue()

    def reader() -> None:
        try:
            for line in sys.stdin:
                loop.call_soon_threadsafe(lines.put_nowait, line.strip())
        finally:
            loop.call_soon_threadsafe(lines.put_nowait, "quit")

    threading.Thread(target=reader, name="ptt-stdin", daemon=True).start()
    recording = False
    try:
        while True:
            line = await lines.get()
            if line.lower() == "quit":
                capture.pop_all()
                session.cancel()
                return 0
            if not recording:
                capture.pop_all()
                recording = True
                print("recording")
                continue
            recording = False
            pcm = capture.pop_all()
            result = await session.submit(pcm)
            _print_result(result, mock)
    finally:
        host.close()
        session.cancel()
