import hashlib
import json
import threading

import httpx
import pytest

from voice_clone_mic.cli import main
from voice_clone_mic.devices import fake_catalog
from voice_clone_mic.errors import ConversionError
from voice_clone_mic.ptt.account import ElevenLabsAccount
from voice_clone_mic.ptt.paths import find_whisper_model, known_whisper_dirs
from voice_clone_mic.ptt.secrets import KeychainSecretStore, MemorySecretStore, SecretStoreError
from voice_clone_mic.ptt.settings_window import SETTINGS_BUTTONS, dispatch_settings
from voice_clone_mic.ptt.utility import LazySessionWorker, UtilityController, UtilityMemoryWorker

SECRET = "super-secret-key"


def _controller(tmp_path, **kwargs):
    worker = kwargs.pop("worker", None) or UtilityMemoryWorker()
    secrets = kwargs.pop("secrets", None) or MemorySecretStore()
    catalog = kwargs.pop("catalog", None) or fake_catalog()
    account = kwargs.pop("account", None) or _RefuseAccount()
    closer = kwargs.pop("closer", None) or []
    controller = UtilityController(
        worker,
        secrets,
        account,
        support=tmp_path / "support",
        known_dirs=known_whisper_dirs(tmp_path, tmp_path / "cwd"),
        catalog_provider=lambda: catalog,
        billing_required=kwargs.pop("billing_required", True),
        env_key=kwargs.pop("env_key", None),
        downloader=kwargs.pop("downloader", None),
        model_checker=kwargs.pop("model_checker", None),
        closer=lambda: closer.append("closed"),
    )
    controller._closer_log = closer
    return controller, worker


class _RefuseAccount:
    def verify(self, api_key: str) -> str:
        raise AssertionError("account verify was not requested")

    def list_voices(self, api_key: str) -> list[dict]:
        raise AssertionError("voice list was not requested")


def test_support_directory_is_checked_before_the_known_model_dirs(tmp_path):
    controller, _worker = _controller(tmp_path)
    directories = controller.model_directories()
    assert directories[0] == tmp_path / "support" / "whisper"
    assert directories[1:] == known_whisper_dirs(tmp_path, tmp_path / "cwd")


def test_known_dirs_are_only_the_three_configured_locations(tmp_path):
    found = known_whisper_dirs(tmp_path, tmp_path / "cwd")
    assert found == [
        tmp_path / "Library" / "Application Support" / "VoiceCloneMic" / "whisper",
        tmp_path / "Applications" / "VoiceCloneMicRuntime" / "runtime" / ".vcm" / "whisper",
        tmp_path / "cwd" / ".vcm" / "whisper",
    ]


def test_model_lookup_uses_the_first_matching_known_dir_only(tmp_path):
    digest = hashlib.sha256(b"checkpoint").hexdigest()
    first = tmp_path / "first"
    second = tmp_path / "second"
    outside = tmp_path / "outside"
    for directory in (first, second, outside):
        directory.mkdir()
        (directory / "small.en.pt").write_bytes(b"checkpoint")
    (first / "small.en.pt").write_bytes(b"nope")
    assert find_whisper_model([first, second], expected=digest) == second / "small.en.pt"
    assert find_whisper_model([first], expected=digest) is None


def test_missing_keychain_refuses_to_save_and_does_not_call_a_process(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("subprocess was called")

    monkeypatch.setattr("subprocess.run", explode)
    store = KeychainSecretStore(None)
    with pytest.raises(SecretStoreError) as caught:
        store.set_secret("local.voice-clone-mic", "elevenlabs-api-key", SECRET)
    assert SECRET not in str(caught.value)
    assert store.get_secret("local.voice-clone-mic", "elevenlabs-api-key") is None


def test_keychain_backend_error_does_not_echo_the_key():
    class Boom:
        def add(self, service, account, secret):
            raise OSError(f"locked {secret}")

    store = KeychainSecretStore(Boom())
    with pytest.raises(SecretStoreError) as caught:
        store.set_secret("svc", "acct", SECRET)
    assert SECRET not in str(caught.value)


def test_connect_401_and_timeout_do_not_list_voices_or_speak():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["xi-api-key"] == SECRET
        if request.url.path == "/v1/user":
            return httpx.Response(401, json={"detail": {"message": SECRET}})
        raise AssertionError(request.url.path)

    account = ElevenLabsAccount("https://api.elevenlabs.io", transport=httpx.MockTransport(handler))
    with pytest.raises(ConversionError) as caught:
        account.verify(SECRET)
    assert SECRET not in str(caught.value)
    assert calls == ["/v1/user"]

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    slow = ElevenLabsAccount("https://api.elevenlabs.io", transport=httpx.MockTransport(timeout))
    with pytest.raises(ConversionError) as caught:
        slow.list_voices(SECRET)
    assert SECRET not in str(caught.value)
    assert caught.value.code == "timeout"


def test_voice_list_returns_only_api_names():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/voices"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {"voice_id": "voice-a", "name": "Studio clone"},
                    {"voice_id": "voice-b"},
                    {"name": "missing id"},
                ]
            },
        )

    account = ElevenLabsAccount("https://api.elevenlabs.io", transport=httpx.MockTransport(handler))
    assert account.list_voices(SECRET) == [{"voice_id": "voice-a", "name": "Studio clone"}]


def test_enable_does_not_record_and_billing_blocks_live_start(tmp_path):
    controller, worker = _controller(tmp_path)
    controller.select_devices("Built-in Microphone", "BlackHole")
    assert controller.status().output_enabled is False
    assert controller.enable_output().phase == "ready"
    assert worker.submits == []
    assert controller.start_recording().phase == "error"
    assert "No request was sent" in controller.status().last_error
    assert worker.submits == []
    controller.accept_billing()
    assert controller.start_recording().phase == "recording"
    assert worker.submits == []


def test_disable_cancels_and_reenable_does_not_replay(tmp_path):
    controller, worker = _controller(tmp_path, billing_required=False)
    controller.select_devices("Built-in", "BlackHole")
    controller.enable_output()
    controller.start_recording()
    assert controller.stop_recording(b"\x01\x00").phase == "transcribing"
    worker.advance("generating")
    worker.advance("playing")
    assert controller.status().phase == "playing"
    assert controller.disable_output().phase == "disabled"
    assert controller._closer_log == ["closed"]
    worker.finish("ok")
    assert controller.status().phase == "disabled"
    assert len(worker.submits) == 1
    assert controller.enable_output().phase == "ready"
    assert len(worker.submits) == 1


def test_settings_change_cancels_the_old_generation(tmp_path):
    controller, worker = _controller(tmp_path, billing_required=False)
    controller.select_devices("Built-in", "BlackHole")
    controller.enable_output()
    controller.start_recording()
    controller.stop_recording(b"\x02\x00")
    controller.select_voice("typed-voice", manual=True)
    assert controller.status().voice_label == "Manual ID"
    assert controller.status().phase == "ready"
    assert worker.cancels >= 1
    worker.advance("playing")
    worker.finish("ok")
    assert controller.status().phase == "ready"
    assert len(worker.submits) == 1
    saved = (tmp_path / "support" / "settings.json").read_text(encoding="utf-8")
    assert "typed-voice" in saved
    assert "api_key" not in saved


def test_connect_stores_in_keychain_not_the_settings_file(tmp_path):
    class Account:
        def verify(self, api_key: str) -> str:
            assert api_key == SECRET
            return "starter"

        def list_voices(self, api_key: str) -> list[dict]:
            assert api_key == SECRET
            return [{"voice_id": "voice-a", "name": "Studio clone"}]

    secrets = MemorySecretStore()
    controller, _worker = _controller(tmp_path, secrets=secrets, account=Account())
    assert controller.connect(SECRET).connected is True
    assert secrets.get_secret("local.voice-clone-mic", "elevenlabs-api-key") == SECRET
    controller.refresh_voices()
    assert controller.status().voices == ({"voice_id": "voice-a", "name": "Studio clone"},)
    controller.select_voice("voice-a")
    assert controller.status().voice_label == "Studio clone"
    text = (tmp_path / "support" / "settings.json").read_text(encoding="utf-8")
    assert SECRET not in text
    assert SECRET not in controller.status().detail
    with pytest.raises(ValueError):
        controller.select_voice("not-returned")


def test_keychain_failure_does_not_connect_or_write_plaintext(tmp_path):
    class Broken:
        def set_secret(self, service, account, secret):
            raise SecretStoreError("Secure keychain is unavailable. The key was not saved.")

        def get_secret(self, service, account):
            return None

        def delete_secret(self, service, account):
            raise SecretStoreError("Secure keychain is unavailable. The key was not saved.")

    controller, _worker = _controller(tmp_path, secrets=Broken())
    with pytest.raises(SecretStoreError) as caught:
        controller.connect(SECRET)
    assert SECRET not in str(caught.value)
    assert controller.status().connected is False
    assert not (tmp_path / "support" / "settings.json").exists()


def test_env_key_is_used_in_memory_and_not_copied(tmp_path):
    class Account:
        def verify(self, api_key: str) -> str:
            assert api_key == SECRET
            return "connected"

        def list_voices(self, api_key: str) -> list[dict]:
            raise AssertionError("voices were not requested")

    secrets = MemorySecretStore()
    controller, _worker = _controller(tmp_path, secrets=secrets, account=Account(), env_key=SECRET)
    controller.connect(None)
    assert secrets.values == {}
    assert controller.status().connected is True
    assert SECRET not in controller.status().detail


def test_missing_blackhole_disables_recording_without_a_speaker_default(tmp_path):
    catalog = [device for device in fake_catalog() if "BlackHole" not in device.name and "CABLE" not in device.name]
    controller, worker = _controller(tmp_path, catalog=catalog, billing_required=False)
    assert controller.status().phase == "needs_setup"
    assert "BlackHole is not installed" in controller.status().detail
    assert "does not install" in controller.status().detail
    assert controller.enable_output().phase == "needs_setup"
    assert controller.start_recording().phase == "needs_setup"
    assert worker.submits == []
    with pytest.raises(Exception):
        controller.select_devices("Built-in Microphone", "Speakers")
    assert controller.status().output_enabled is False


def test_device_loss_and_sleep_close_streams(tmp_path):
    controller, worker = _controller(tmp_path, billing_required=False)
    controller.select_devices("Built-in", "BlackHole")
    controller.enable_output()
    controller.start_recording()
    controller.note_device_lost()
    assert controller.status().phase == "device_lost"
    assert controller.status().output_enabled is False
    assert controller._closer_log
    assert worker.cancels >= 1
    controller.note_sleep()
    assert controller.status().phase == "disabled"
    assert controller.quit().phase == "quit"


def test_prepare_runs_off_the_caller_and_cancel_drops_the_result(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def downloader(root, progress):
        assert threading.current_thread() is not threading.main_thread()
        progress("downloading")
        started.set()
        if not release.wait(2):
            raise AssertionError("cancel did not release the download")
        progress("finished")

    controller, _worker = _controller(tmp_path, downloader=downloader)
    assert controller.prepare_model().model_progress == "starting"
    assert started.wait(2)
    assert controller.status().model_progress == "downloading"
    controller.cancel_prepare()
    release.set()
    controller._prepare_thread.join(2)
    assert controller.status().model_progress == "cancelled"
    assert controller.status().whisper_ready is False


def test_saved_devices_reload_without_the_key(tmp_path):
    controller, _worker = _controller(tmp_path, billing_required=False)
    controller.select_devices("Built-in Microphone", "BlackHole 2ch")
    other, _worker = _controller(tmp_path, billing_required=False)
    assert other.status().output_name == "BlackHole 2ch"
    assert other.status().input_name == "Built-in Microphone"
    assert other.status().output_enabled is False
    blob = json.loads((tmp_path / "support" / "settings.json").read_text(encoding="utf-8"))
    assert "api_key" not in blob


def test_stale_saved_output_is_not_kept(tmp_path):
    controller, _worker = _controller(tmp_path)
    controller.select_devices("Built-in", "BlackHole")
    empty = [device for device in fake_catalog() if "BlackHole" not in device.name and "CABLE" not in device.name]
    reloaded, _worker = _controller(tmp_path, catalog=empty)
    assert reloaded.status().output_name == ""
    assert reloaded.status().phase == "needs_setup"


def test_settings_buttons_call_the_same_controller(tmp_path):
    controller, worker = _controller(tmp_path, billing_required=False)
    dispatch_settings(controller, "Refresh devices")
    dispatch_settings(controller, "Enable output")
    assert controller.status().phase == "needs_setup"
    controller.select_devices("Built-in", "BlackHole")
    assert dispatch_settings(controller, "Enable output").phase == "ready"
    assert dispatch_settings(controller, "Record").phase == "recording"
    assert dispatch_settings(controller, "Stop", pcm=b"\x01\x00").phase == "transcribing"
    assert len(worker.submits) == 1
    assert dispatch_settings(controller, "Disable output").output_enabled is False
    assert SETTINGS_BUTTONS["Prepare model"] == "prepare_model"
    assert SETTINGS_BUTTONS["Accept speech credits"] == "accept_billing"


def test_lazy_worker_is_not_built_until_a_recording_stops(tmp_path):
    calls: list[str] = []

    class Made:
        def start(self, pcm, generation, on_phase, on_done):
            calls.append("start")
            on_phase(generation, "transcribing")
            on_done(generation, None)

        def cancel(self):
            calls.append("cancel")

    worker = LazySessionWorker(lambda: calls.append("factory") or Made())
    controller, _worker = _controller(tmp_path, worker=worker, billing_required=False)
    controller.select_devices("Built-in", "BlackHole")
    controller.enable_output()
    controller.accept_billing()
    assert calls == []
    controller.start_recording()
    assert calls == []
    controller.stop_recording(b"\x01\x00")
    assert calls[0] == "factory"
    assert controller.status().phase == "ready"


def test_utility_smoke_does_not_import_cocoa_or_download(capsys):
    import sys

    assert main(["ptt", "utility", "--smoke"]) == 0
    out = capsys.readouterr().out
    assert "Output: Disabled" in out
    assert "Output: Ready" in out
    assert "Transcribing" in out
    assert "Generating" in out
    assert "Playing" in out
    assert "Output: Quit" in out
    assert "submits=1" in out
    assert "downloads=0" in out
    assert "account_calls=0" in out
    assert "rumps" not in sys.modules
    assert "AppKit" not in sys.modules
    assert "whisper" not in sys.modules


def test_utility_without_cocoa_does_not_call_the_api(capsys, monkeypatch):
    import importlib.util

    if importlib.util.find_spec("rumps") is not None:
        pytest.skip("rumps is installed; this environment must not launch the utility")

    def boom(*_args, **_kwargs):
        raise AssertionError("network client constructed")

    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(httpx, "AsyncClient", boom)
    assert main(["ptt", "utility", "--fake"]) == 2
    assert "Cocoa was not started" in capsys.readouterr().err
