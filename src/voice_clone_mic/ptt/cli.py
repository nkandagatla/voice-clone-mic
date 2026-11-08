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
        if args.ptt_command == "menu":
            return _menu(args)
        if args.ptt_command == "utility":
            return _utility(args)
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


def _menu(args) -> int:
    from voice_clone_mic.ptt.menu_controller import MenuController, MemoryWorker, scripted_smoke

    if args.smoke:
        if args.fake or args.output:
            problem = None
            output = args.output or "BlackHole 2ch"
        else:
            problem = (
                "Choose a virtual cable output before recording. "
                "BlackHole on Mac, or CABLE Input on Windows."
            )
            output = ""
        worker = MemoryWorker()
        controller = MenuController(worker, output, problem)
        print(controller.status().title)
        print(controller.status().detail)
        if problem:
            controller.start_recording()
            if controller.status().phase != "needs_setup":
                return 1
            print("record_blocked=true")
            return 0
        titles = scripted_smoke(controller, b"\x01\x00" * 200)
        print("\n".join(titles))
        print(f"submits={len(worker.submits)}")
        return 0 if len(worker.submits) == 1 else 1
    try:
        from voice_clone_mic.ptt.menubar import run_menu

        run_menu(_menu_controller(args))
    except ImportError:
        print(
            "The menu-bar extra is not installed, so Cocoa was not started. "
            'Install it in the ptt virtualenv with: python -m pip install -e ".[menubar]"',
            file=sys.stderr,
        )
        return 2
    return 0


def _menu_controller(args):
    from voice_clone_mic.ptt.menu_controller import AsyncSessionWorker, MemoryWorker, MenuController

    settings = load_settings(dict(os.environ), Path.cwd())
    output_query = getattr(args, "output", None) or settings.output_device
    if not output_query and not getattr(args, "fake", False):
        return MenuController(
            MemoryWorker(),
            "",
            "Choose a virtual cable output before recording. BlackHole on Mac, or CABLE Input on Windows.",
        )
    if getattr(args, "fake", False):
        devices = fake_catalog()
    else:
        from voice_clone_mic.audio_host import SoundDeviceHost

        try:
            devices = SoundDeviceHost().list_devices()
        except Exception as exc:
            return MenuController(MemoryWorker(), "", f"Audio devices are unavailable ({type(exc).__name__}).")
    try:
        chosen_in, chosen_out = select_devices(
            devices,
            getattr(args, "input", None) or settings.input_device,
            output_query or "BlackHole",
            False,
        )
    except Exception as exc:
        return MenuController(MemoryWorker(), "", str(exc))
    mock = getattr(args, "mock", False) or getattr(args, "fake", False)
    if not mock and not getattr(args, "i_accept_api_billing", False):
        return MenuController(
            MemoryWorker(),
            chosen_out.name,
            "Live TTS needs --i-accept-api-billing. No request was sent.",
        )
    if mock:
        transcriber = _DemoTranscriber()
        tts = MockTtsClient()
        voice_id = "mock-voice"
    else:
        if not settings.api_key:
            return MenuController(
                MemoryWorker(),
                chosen_out.name,
                "ELEVENLABS_API_KEY is missing. No request was sent.",
            )
        transcriber = LocalWhisperTranscriber(Path(".vcm/whisper"), DEFAULT_MODEL)
        tts = HttpxTtsClient(
            settings.api_key,
            validate_api_base(settings.api_base),
            settings.tts_model or DEFAULT_TTS_MODEL,
        )
        voice_id = settings.tts_voice_id or settings.voice_id or ""
        if not voice_id:
            return MenuController(MemoryWorker(), chosen_out.name, "A voice id is required. No request was sent.")
    session = PttSession(transcriber, tts, voice_id, chosen_in, chosen_out)
    if getattr(args, "fake", False):
        return MenuController(
            AsyncSessionWorker(session),
            chosen_out.name,
            capture_pcm=lambda: _demo_pcm(session.input_rate),
        )
    from voice_clone_mic.audio_host import CaptureBuffer, SoundDeviceHost

    host = SoundDeviceHost()
    capture = CaptureBuffer(max_bytes=session.input_rate * 2 * int(session.max_seconds))
    try:
        host.open_streams(session, chosen_in, chosen_out, capture)
    except Exception as exc:
        return MenuController(MemoryWorker(), chosen_out.name, f"Could not open the cable ({type(exc).__name__}).")

    def fault() -> bool:
        inactive = host.poll_inactive()
        if inactive:
            return True
        try:
            host.faults.get_nowait()
        except Exception:
            return False
        return True

    return MenuController(
        AsyncSessionWorker(session),
        chosen_out.name,
        capture_pcm=capture.pop_all,
        on_record_start=capture.pop_all,
        poll_fault=fault,
    )


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


def _utility(args) -> int:
    if args.smoke:
        return _utility_smoke()
    try:
        from voice_clone_mic.ptt.settings_window import run_utility
    except ImportError:
        _cocoa_missing()
        return 2
    try:
        run_utility(_utility_controller(args))
    except ImportError:
        _cocoa_missing()
        return 2
    return 0


def _cocoa_missing() -> None:
    print(
        "The menu-bar extra is not installed, so Cocoa was not started. "
        'Install it in the ptt virtualenv with: python -m pip install -e ".[menubar]"',
        file=sys.stderr,
    )


def _utility_smoke() -> int:
    import tempfile

    from voice_clone_mic.ptt.secrets import MemorySecretStore
    from voice_clone_mic.ptt.paths import known_whisper_dirs
    from voice_clone_mic.ptt.utility import UtilityController, UtilityMemoryWorker, scripted_utility_smoke

    class CountingAccount:
        def __init__(self) -> None:
            self.calls = 0

        def verify(self, api_key: str) -> str:
            self.calls += 1
            raise AssertionError("account verify was not requested")

        def list_voices(self, api_key: str) -> list[dict]:
            self.calls += 1
            raise AssertionError("voice list was not requested")

    worker = UtilityMemoryWorker()
    account = CountingAccount()
    downloads = {"n": 0}

    def downloader(_root, _progress) -> None:
        downloads["n"] += 1

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        controller = UtilityController(
            worker,
            MemorySecretStore(),
            account,
            support=root / "support",
            known_dirs=known_whisper_dirs(root, root / "cwd"),
            catalog_provider=fake_catalog,
            billing_required=True,
            downloader=downloader,
        )
        titles = scripted_utility_smoke(controller, b"\x01\x00" * 8)
    print("\n".join(titles))
    print(f"submits={len(worker.submits)}")
    print(f"account_calls={account.calls}")
    print(f"downloads={downloads['n']}")
    ok = len(worker.submits) == 1 and account.calls == 0 and downloads["n"] == 0
    return 0 if ok else 1


class _LazyAccount:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def verify(self, api_key: str) -> str:
        from voice_clone_mic.ptt.account import ElevenLabsAccount

        return ElevenLabsAccount(self.base_url).verify(api_key)

    def list_voices(self, api_key: str) -> list[dict]:
        from voice_clone_mic.ptt.account import ElevenLabsAccount

        return ElevenLabsAccount(self.base_url).list_voices(api_key)


def _utility_controller(args):
    from voice_clone_mic.config import load_settings
    from voice_clone_mic.ptt.paths import known_whisper_dirs, support_dir
    from voice_clone_mic.ptt.secrets import KeychainSecretStore, MemorySecretStore, system_keychain_backend
    from voice_clone_mic.ptt.utility import BoundSessionWorker, UtilityController, UtilityMemoryWorker

    settings = load_settings(dict(os.environ), Path.cwd())
    home = Path.home()
    support = support_dir(home)
    if args.fake:
        catalog = fake_catalog
        secrets = MemorySecretStore()
        worker = UtilityMemoryWorker()
        billing_required = False
        require_model = False
        opener = None
        closer = None
        capture = lambda: b"\x01\x00" * 160
        faults = None
    else:
        catalog = _live_catalog
        secrets = KeychainSecretStore(system_keychain_backend())
        audio: dict = {}
        worker = BoundSessionWorker(audio)
        billing_required = True
        require_model = True
        opener = lambda: _arm_utility_audio(audio)
        closer = lambda: _close_utility_audio(audio)
        capture = lambda: _pop_utility_audio(audio)
        faults = lambda: _utility_fault(audio)
    controller = UtilityController(
        worker,
        secrets,
        _LazyAccount(settings.api_base),
        support=support,
        known_dirs=known_whisper_dirs(home, Path.cwd()),
        catalog_provider=catalog,
        billing_required=billing_required,
        env_key=settings.api_key,
        closer=closer,
        capture_pcm=capture,
        audio_opener=opener,
        require_model=require_model,
        fault_check=faults,
        tts_model=settings.tts_model,
    )
    if not args.fake:
        audio["controller"] = controller
    return controller


def _live_catalog():
    from voice_clone_mic.audio_host import SoundDeviceHost

    return SoundDeviceHost().list_devices()


def _arm_utility_audio(audio: dict) -> None:
    import hashlib

    from voice_clone_mic.audio_host import CaptureBuffer, SoundDeviceHost
    from voice_clone_mic.errors import validate_api_base
    from voice_clone_mic.ptt.paths import find_whisper_model, known_whisper_dirs
    from voice_clone_mic.ptt.session import PttSession
    from voice_clone_mic.ptt.tts import HttpxTtsClient
    from voice_clone_mic.ptt.whisper_backend import DEFAULT_MODEL, LocalWhisperTranscriber

    controller = audio["controller"]
    key = controller._key()
    voice_id = controller.voice_id
    if not voice_id:
        raise RuntimeError("Choose a voice before recording. No request was sent.")
    found = find_whisper_model(known_whisper_dirs(Path.home(), Path.cwd()))
    if found is None:
        raise LocalModelMissing("Whisper small.en is not prepared. No request was sent.")
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    same = (
        audio.get("host") is not None
        and audio.get("fingerprint") == fingerprint
        and audio.get("voice") == voice_id
        and audio.get("input") == controller.input_query
        and audio.get("output") == controller.output_query
    )
    if same:
        return
    _close_utility_audio(audio)
    devices = _live_catalog()
    chosen_in, chosen_out = select_devices(
        devices,
        controller.input_query or None,
        controller.output_query or None,
        False,
    )
    transcriber = LocalWhisperTranscriber(found.parent, DEFAULT_MODEL)
    tts = HttpxTtsClient(key, validate_api_base(controller.account.base_url), controller.tts_model)
    session = PttSession(transcriber, tts, voice_id, chosen_in, chosen_out)
    host = SoundDeviceHost()
    capture = CaptureBuffer(max_bytes=session.max_samples * 2)
    try:
        host.open_streams(session, chosen_in, chosen_out, capture)
    except Exception:
        host.close()
        raise
    audio["host"] = host
    audio["capture"] = capture
    audio["session"] = session
    audio["fingerprint"] = fingerprint
    audio["voice"] = voice_id
    audio["input"] = controller.input_query
    audio["output"] = controller.output_query


def _pop_utility_audio(audio: dict) -> bytes:
    capture = audio.get("capture")
    if capture is None:
        return b""
    return capture.pop_all()


def _utility_fault(audio: dict) -> str | None:
    host = audio.get("host")
    if host is None:
        return None
    return host.poll_inactive()


def _close_utility_audio(audio: dict) -> None:
    host = audio.pop("host", None)
    if host is not None:
        host.close()
    session = audio.pop("session", None)
    if session is not None:
        session.cancel()
    audio.pop("capture", None)
    audio.pop("fingerprint", None)
