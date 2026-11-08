import sys
import threading

import pytest

from voice_clone_mic.cli import main
from voice_clone_mic.devices import DeviceInfo
from voice_clone_mic.pcm import samples_to_bytes
from voice_clone_mic.ptt.mac_app import write_unsigned_menu_app
from voice_clone_mic.ptt.menu_controller import MenuController, MemoryWorker
from voice_clone_mic.ptt.session import PttSession, Transcript
from voice_clone_mic.ptt.tts import MockTtsClient

MIC = DeviceInfo(0, "Built-in Microphone", 1, 0, 16000, True, False)
CABLE = DeviceInfo(2, "BlackHole 2ch", 2, 2, 16000, False, False)


def _loud() -> bytes:
    return samples_to_bytes([8000] * 16000)


def test_starts_muted_and_unmute_does_not_send_audio():
    worker = MemoryWorker()
    controller = MenuController(worker, "BlackHole 2ch")
    assert controller.status().phase == "muted"
    assert controller.status().can_record is False
    assert "does not mute the Mac microphone" in controller.status().detail
    assert controller.toggle_mute().phase == "ready"
    assert worker.submits == []
    assert controller.start_recording().phase == "recording"
    assert controller.toggle_mute().phase == "muted"
    assert worker.submits == []
    assert worker.cancels == 1
    assert controller.toggle_mute().phase == "ready"
    assert worker.submits == []


def test_stop_sends_once_and_a_late_callback_after_mute_is_ignored():
    worker = MemoryWorker()
    controller = MenuController(worker, "BlackHole 2ch")
    controller.toggle_mute()
    controller.start_recording()
    assert controller.stop_recording(b"\x01\x02").phase == "converting"
    assert len(worker.submits) == 1
    controller.toggle_mute()
    worker.finish()
    assert controller.status().phase == "muted"
    assert len(worker.submits) == 1


def test_device_loss_and_quit_stay_silent():
    worker = MemoryWorker()
    controller = MenuController(worker, "BlackHole 2ch")
    controller.toggle_mute()
    controller.start_recording()
    lost = controller.on_device_lost()
    assert lost.phase == "device_lost"
    assert "silent" in lost.detail
    assert controller.start_recording().phase == "device_lost"
    assert controller.quit().phase == "quit"
    assert worker.cancels >= 1


def test_missing_output_blocks_recording():
    controller = MenuController(MemoryWorker(), "", "Choose a virtual cable output before recording.")
    assert controller.status().phase == "needs_setup"
    assert controller.start_recording().phase == "needs_setup"
    assert controller.toggle_mute().phase == "needs_setup"


def test_menu_smoke_commands_do_not_import_cocoa(capsys):
    assert main(["ptt", "menu", "--smoke", "--fake"]) == 0
    fake = capsys.readouterr().out
    assert "Converted voice: Muted" in fake
    assert "Converted voice: Ready" in fake
    assert "Converted voice: Converting" in fake
    assert "Converted voice: Quit" in fake
    assert "does not mute the Mac microphone" in fake
    assert "submits=1" in fake
    assert "rumps" not in sys.modules
    assert main(["ptt", "menu", "--smoke"]) == 0
    missing = capsys.readouterr().out
    assert "Needs setup" in missing
    assert "record_blocked=true" in missing
    assert "rumps" not in sys.modules


def test_menu_without_cocoa_does_not_start_or_call_the_api(capsys, monkeypatch):
    import importlib.util

    if importlib.util.find_spec("rumps") is not None:
        pytest.skip("rumps is installed; this environment must not launch the menu")
    import httpx

    def boom(*_args, **_kwargs):
        raise AssertionError("network client constructed")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    assert main(["ptt", "menu", "--fake"]) == 2
    assert "Cocoa was not started" in capsys.readouterr().err


def test_unsigned_app_bundle_is_not_signed(tmp_path):
    app = write_unsigned_menu_app(tmp_path, "/usr/bin/python3")
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    launch = (app / "Contents" / "MacOS" / "launch").read_text(encoding="utf-8")
    assert "LSUIElement" in plist
    assert "LSMinimumSystemVersion" in plist
    assert "13.0" in plist
    assert "codesign" not in launch
    assert "ptt utility" in launch
    assert "ptt menu" not in launch
    assert "prepare-model" not in launch
    assert "ELEVENLABS" not in launch
    assert "i-accept-api-billing" not in launch
    assert "VoiceCloneMicRuntime/runtime" in launch
    assert "Application Support/VoiceCloneMic" in launch


def test_mute_during_slow_transcription_does_not_speak():
    class Slow:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def transcribe(self, samples):
            del samples
            self.started.set()
            self.release.wait(timeout=3)
            return Transcript(
                "hello",
                [{"text": "hello", "no_speech_prob": 0.1, "avg_logprob": -0.2, "compression_ratio": 1.0}],
            )

    stt = Slow()
    tts = MockTtsClient()
    session = PttSession(stt, tts, "voice", MIC, CABLE)

    class SessionWorker:
        def __init__(self) -> None:
            self.thread: threading.Thread | None = None

        def start(self, pcm: bytes, generation: int, on_done) -> None:
            def run() -> None:
                import asyncio

                result = asyncio.run(session.submit(pcm))
                on_done(generation, result)

            self.thread = threading.Thread(target=run)
            self.thread.start()

        def cancel(self) -> None:
            session.cancel()

        def join(self) -> None:
            if self.thread is not None:
                self.thread.join(timeout=3)

    worker = SessionWorker()
    controller = MenuController(worker, "BlackHole 2ch")
    controller.toggle_mute()
    controller.start_recording()
    controller.stop_recording(_loud())
    assert stt.started.wait(timeout=2)
    controller.toggle_mute()
    stt.release.set()
    worker.join()
    assert tts.texts == []
    assert session.playback.snapshot() == []
    assert controller.status().phase == "muted"
