"""Command line for the phrase-buffered voice-clone microphone."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import httpx

from voice_clone_mic import __version__
from voice_clone_mic.audio_host import CaptureBuffer, SoundDeviceHost
from voice_clone_mic.client import HttpxVoiceClient
from voice_clone_mic.config import load_settings, save_voice_id
from voice_clone_mic.devices import DeviceSelectionError, fake_catalog, select_devices
from voice_clone_mic.errors import ConversionError, error_from_status, require_little_endian, validate_api_base
from voice_clone_mic.mock_client import MockVoiceClient
from voice_clone_mic.pcm import samples_to_bytes
from voice_clone_mic.session import Session

DESCRIPTION = (
    "Phrase-buffered ElevenLabs voice changer. Speech is cut into segments, "
    "converted, and written to a virtual cable. Delay includes phrase buffering "
    "plus network time. The app writes the playback side of BlackHole on macOS "
    "or VB-CABLE on Windows. It does not create that device."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-clone-mic", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version=f"voice-clone-mic {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    devices = sub.add_parser("devices", help="List input and output devices")
    devices.add_argument("--fake", action="store_true", help="List the built-in fixture devices")
    devices.add_argument("--json", action="store_true")

    sub.add_parser("config", help="Show redacted local configuration")

    use_voice = sub.add_parser("use-voice", help="Store a voice id in .vcm/config.json")
    use_voice.add_argument("voice_id")

    voices = sub.add_parser("voices", help="List ElevenLabs voices. Sends the API key")
    voices.add_argument("--i-accept-api-billing", action="store_true")

    sub.add_parser("demo", help="Run an offline mock conversion and exit")

    run = sub.add_parser("run", help="Convert microphone audio into a virtual cable")
    run.add_argument("--input", help="Input device index or name substring")
    run.add_argument("--output", help="Output virtual-cable index or name substring")
    run.add_argument("--voice-id")
    run.add_argument("--mock", action="store_true", help="Use the local mock converter")
    run.add_argument("--fake", action="store_true", help="Use fixture devices instead of PortAudio")
    run.add_argument("--once", action="store_true", help="Convert one synthetic phrase and exit")
    run.add_argument("--allow-any-output", action="store_true")
    run.add_argument("--i-accept-api-billing", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "devices":
            return _devices(args)
        if args.command == "config":
            return _config()
        if args.command == "use-voice":
            return _use_voice(args.voice_id)
        if args.command == "voices":
            return asyncio.run(_voices(args.i_accept_api_billing))
        if args.command == "demo":
            return asyncio.run(_demo())
        if args.command == "run":
            return asyncio.run(_run(args))
    except DeviceSelectionError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ConversionError as exc:
        print(str(exc), file=sys.stderr)
        return 2 if exc.code == "config" else 1
    return 2


def format_status(status: dict[str, object]) -> str:
    observed = status["observed_last_segment_ms"]
    observed_text = "none" if observed is None else str(observed)
    last_error = status["last_error"] or "none"
    return "\n".join(
        [
            (
                f"state={status['state']} mock={str(status['mock']).lower()} "
                f"passthrough=false generation={status['generation']}"
            ),
            f"input={status['input_name']} output={status['output_name']}",
            (
                f"segments_sent={status['sent_segments']} segments_played={status['played_segments']} "
                f"segments_failed={status['failed_segments']} segments_dropped={status['dropped_segments']}"
            ),
            (
                f"capture_ms={status['capture_backlog_ms']} unsent_ms={status['unsent_ms']} "
                f"inflight_ms={status['inflight_ms']} held_ms={status['held_ms']} "
                f"playback_ms={status['playback_ms']} in_flight={status['in_flight_count']} "
                f"underruns={status['underruns']}"
            ),
            f"observed_last_segment_ms={observed_text}",
            f"last_error={last_error}",
        ]
    )


def demo_capture(sample_rate: int) -> bytes:
    silence_pre = [0] * (sample_rate * 200 // 1000)
    speech = [8000] * (sample_rate * 300 // 1000)
    silence_post = [0] * (sample_rate * 500 // 1000)
    return samples_to_bytes(silence_pre + speech + silence_post)


def _load_devices(fake: bool):
    if fake:
        return fake_catalog()
    try:
        return SoundDeviceHost().list_devices()
    except OSError as exc:
        print(
            "PortAudio could not be loaded, so no live devices were opened. "
            "macOS and Windows wheels normally include PortAudio. This app does not install a driver. "
            f"{exc}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"Could not query audio devices. {type(exc).__name__}", file=sys.stderr)
    return None


def _devices(args: argparse.Namespace) -> int:
    require_little_endian()
    devices = _load_devices(args.fake)
    if devices is None:
        return 1
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "index": device.index,
                        "name": device.name,
                        "max_input_channels": device.max_input_channels,
                        "max_output_channels": device.max_output_channels,
                        "default_samplerate": device.default_samplerate,
                        "default_input": device.default_input,
                        "default_output": device.default_output,
                    }
                    for device in devices
                ]
            )
        )
        return 0
    for device in devices:
        print(
            f"{device.index}\t{device.name}\tin={device.max_input_channels}\t"
            f"out={device.max_output_channels}\trate={int(device.default_samplerate)}\t"
            f"default_input={str(device.default_input).lower()}\t"
            f"default_output={str(device.default_output).lower()}"
        )
    return 0


def _config() -> int:
    settings = load_settings(dict(os.environ), Path.cwd())
    print(f"api_key={'set' if settings.api_key else 'missing'}")
    print(f"voice_id={settings.voice_id or 'missing'}")
    print(f"model_id={settings.model_id}")
    print(f"api_base={settings.api_base}")
    print(f"input_device={settings.input_device or 'default'}")
    print(f"output_device={settings.output_device or 'required'}")
    if settings.ignored_file_key:
        print("ignored_file_api_key=true")
    return 0


def _use_voice(voice_id: str) -> int:
    save_voice_id(Path.cwd(), voice_id)
    print(f"Stored voice id {voice_id} in .vcm/config.json")
    return 0


async def _voices(accept_billing: bool) -> int:
    if not accept_billing:
        print(
            "Refusing to contact ElevenLabs without --i-accept-api-billing. No request was sent.",
            file=sys.stderr,
        )
        return 2
    require_little_endian()
    settings = load_settings(dict(os.environ), Path.cwd())
    if not settings.api_key:
        print("ELEVENLABS_API_KEY is missing. No request was sent.", file=sys.stderr)
        return 2
    base = validate_api_base(settings.api_base)
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False) as http:
        response = await http.get(f"{base}/v1/voices", headers={"xi-api-key": settings.api_key})
    if response.status_code != 200:
        print(error_from_status(response.status_code, response.content[:2048], settings.api_key), file=sys.stderr)
        return 1
    try:
        payload = response.json()
    except json.JSONDecodeError:
        print("Voice list could not be read. Set ELEVENLABS_VOICE_ID directly.", file=sys.stderr)
        return 1
    voices = payload.get("voices") if isinstance(payload, dict) else None
    if not isinstance(voices, list):
        print("Voice list could not be read. Set ELEVENLABS_VOICE_ID directly.", file=sys.stderr)
        return 1
    for voice in voices:
        if isinstance(voice, dict) and isinstance(voice.get("voice_id"), str) and isinstance(voice.get("name"), str):
            print(f"{voice['voice_id']}\t{voice['name']}")
    return 0


async def _demo() -> int:
    require_little_endian()
    chosen_in, chosen_out = select_devices(fake_catalog(), None, "BlackHole", False)
    session = Session("mock-voice", MockVoiceClient(), chosen_in, chosen_out)
    await session.feed(demo_capture(session.input_rate))
    await session.finish()
    if not any(session.playback.snapshot()):
        print("demo produced no converted audio", file=sys.stderr)
        return 1
    print(format_status(session.status()))
    print("No audio was sent to ElevenLabs.")
    return 0


async def _run(args: argparse.Namespace) -> int:
    require_little_endian()
    if not args.mock and not args.i_accept_api_billing:
        print(
            "Refusing to contact ElevenLabs without --i-accept-api-billing. No request was sent.",
            file=sys.stderr,
        )
        return 2
    settings = load_settings(dict(os.environ), Path.cwd())
    voice_id = args.voice_id or settings.voice_id
    if args.mock and not voice_id:
        voice_id = "mock-voice"
    if not voice_id:
        print("A voice id is required. Set ELEVENLABS_VOICE_ID or run use-voice. No request was sent.", file=sys.stderr)
        return 2
    output_query = args.output or settings.output_device
    input_query = args.input or settings.input_device
    devices = _load_devices(args.fake)
    if devices is None:
        return 1
    chosen_in, chosen_out = select_devices(devices, input_query, output_query, args.allow_any_output)
    if args.mock:
        client: object = MockVoiceClient()
    else:
        if not settings.api_key:
            print("ELEVENLABS_API_KEY is missing. No request was sent.", file=sys.stderr)
            return 2
        base = validate_api_base(settings.api_base)
        client = HttpxVoiceClient(settings.api_key, base, settings.model_id)
    session = Session(voice_id, client, chosen_in, chosen_out)
    if args.once:
        if not args.mock:
            print(
                "run --once is an offline smoke path and requires --mock. No request was sent.",
                file=sys.stderr,
            )
            return 2
        await session.feed(demo_capture(session.input_rate))
        await session.finish()
        print(format_status(session.status()))
        if not any(session.playback.snapshot()):
            print("run produced no converted audio", file=sys.stderr)
            return 1
        return 0
    if args.fake:
        print("Fixture devices do not capture a microphone. Use demo, or run --fake --once.", file=sys.stderr)
        return 2
    host = SoundDeviceHost()
    capture = CaptureBuffer(max_bytes=session.input_rate * 2 * 2)
    session.capture = capture
    try:
        host.open_streams(session, chosen_in, chosen_out, capture)
    except Exception as exc:
        print(f"Could not open the selected audio devices. {type(exc).__name__}", file=sys.stderr)
        host.close()
        return 1
    try:
        await _interactive(session, host, capture)
    finally:
        host.close()
        if session.status()["state"] != "stopped":
            await session.stop()
    return 0


async def _interactive(session: Session, host: SoundDeviceHost, capture: CaptureBuffer) -> None:
    stop = asyncio.Event()

    async def pump() -> None:
        while not stop.is_set():
            fault = host.poll_inactive()
            if fault is None:
                try:
                    fault = host.faults.get_nowait()
                except Exception:
                    fault = None
            if fault and session.device_fault is None:
                await session.on_device_fault(fault)
            data = capture.pop_all()
            if data and session.device_fault is None and not session.user_muted:
                await session.feed(data)
            await asyncio.sleep(0.02)

    async def commands() -> None:
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str] = asyncio.Queue()

        def reader() -> None:
            try:
                for line in sys.stdin:
                    loop.call_soon_threadsafe(lines.put_nowait, line.strip())
            finally:
                loop.call_soon_threadsafe(lines.put_nowait, "quit")

        threading.Thread(target=reader, name="voice-clone-mic-stdin", daemon=True).start()
        print("commands: status, mute, unmute, stop, quit, help")
        while True:
            line = (await lines.get()).strip().lower()
            if line in {"quit", "stop"}:
                await session.stop()
                print(format_status(session.status()))
                stop.set()
                return
            if line == "mute":
                await session.mute()
                print("muted")
            elif line == "unmute":
                print(await session.unmute())
            elif line == "status":
                print(format_status(session.status()))
            elif line == "help":
                print("commands: status, mute, unmute, stop, quit, help")
            elif line:
                print("commands: status, mute, unmute, stop, quit, help")

    await asyncio.gather(pump(), commands())
