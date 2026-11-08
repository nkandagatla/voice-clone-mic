"""Settings and converted-voice switch. Enabling output does not record or call ElevenLabs."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from voice_clone_mic.devices import DeviceInfo, DeviceSelectionError, loop_key, select_devices
from voice_clone_mic.ptt.paths import find_whisper_model
from voice_clone_mic.ptt.secrets import ACCOUNT, SERVICE, SecretStoreError
from voice_clone_mic.ptt.whisper_backend import LocalModelMissing

SCOPE_TEXT = (
    "The switch silences only this app's converted voice on the selected virtual cable. "
    "It does not mute the Mac microphone, Zoom, or Teams. "
    "The meeting must select that cable as its microphone."
)


@dataclass(frozen=True)
class UtilityView:
    phase: str
    title: str
    detail: str
    output_enabled: bool
    billing_accepted: bool
    can_record: bool
    whisper_ready: bool
    whisper_path: str
    model_progress: str
    voice_id: str
    voice_label: str
    voice_manual: bool
    voices: tuple[dict[str, str], ...]
    input_name: str
    output_name: str
    connected: bool
    account_label: str
    last_error: str
    muted: bool


class UtilityController:
    def __init__(
        self,
        worker: object,
        secrets: object,
        account: object,
        support,
        known_dirs: list,
        catalog_provider,
        billing_required: bool = True,
        env_key: str | None = None,
        downloader=None,
        model_checker=None,
        closer=None,
        capture_pcm=None,
        audio_opener=None,
        require_model: bool = False,
        fault_check=None,
        tts_model: str = "eleven_flash_v2_5",
    ) -> None:
        self.worker = worker
        self.secrets = secrets
        self.account = account
        self.support = support
        self.known_dirs = list(known_dirs)
        self.catalog_provider = catalog_provider
        self.billing_required = billing_required
        self.billing_accepted = False
        self.env_key = env_key or None
        self.downloader = downloader
        self.model_checker = model_checker
        self.closer = closer
        self.capture_pcm = capture_pcm
        self.audio_opener = audio_opener
        self.require_model = require_model
        self.fault_check = fault_check
        self.tts_model = tts_model or "eleven_flash_v2_5"
        self.output_enabled = False
        self.phase = "disabled"
        self.connected = False
        self.account_label = "disconnected"
        self.voices: list[dict[str, str]] = []
        self.voice_id = ""
        self.voice_label = ""
        self.voice_manual = False
        self.input_query = ""
        self.output_query = ""
        self.input_name = ""
        self.output_name = ""
        self.last_error = ""
        self.model_progress = ""
        self.whisper_ready = False
        self.whisper_path = ""
        self.generation = 0
        self.key_in_memory: str | None = None
        self._redact_secret: str | None = None
        self.setup_problem = ""
        self._prepare_token: object | None = None
        self._prepare_thread: threading.Thread | None = None
        self._load_settings()
        self.refresh_whisper()
        self._verify_saved_devices()

    def status(self) -> UtilityView:
        titles = {
            "disabled": "Output: Disabled",
            "ready": "Output: Ready",
            "recording": "Recording",
            "transcribing": "Transcribing",
            "generating": "Generating",
            "playing": "Playing",
            "error": "Error",
            "needs_setup": "Needs setup",
            "device_lost": "Cable lost",
            "quit": "Output: Quit",
        }
        if self.billing_accepted:
            billing = "Speech credits: accepted for this session."
        else:
            billing = "Speech credits: not accepted this session. Speaking uses ElevenLabs credits."
        detail = f"{SCOPE_TEXT} {billing}"
        if self.setup_problem:
            detail = f"{self.setup_problem} {detail}"
        if self.phase == "device_lost":
            detail = f"Virtual cable unavailable. Output stays silent. {detail}"
        if self.last_error and self.phase == "error":
            detail = f"{self.last_error} {detail}"
        return UtilityView(
            phase=self.phase,
            title=titles[self.phase],
            detail=detail,
            output_enabled=self.output_enabled,
            billing_accepted=self.billing_accepted,
            can_record=self.phase == "ready" and self.output_enabled and not self.setup_problem,
            whisper_ready=self.whisper_ready,
            whisper_path=self.whisper_path,
            model_progress=self.model_progress,
            voice_id=self.voice_id,
            voice_label=self.voice_label,
            voice_manual=self.voice_manual,
            voices=tuple(self.voices),
            input_name=self.input_name,
            output_name=self.output_name,
            connected=self.connected,
            account_label=self.account_label,
            last_error=self.last_error,
            muted=not self.output_enabled,
        )

    def enable_output(self) -> UtilityView:
        if self.phase in {"quit", "device_lost"}:
            return self.status()
        if self.setup_problem or not self.output_name or not self.input_name:
            self.output_enabled = False
            self.phase = "needs_setup"
            return self.status()
        self.output_enabled = True
        self.phase = "ready"
        return self.status()

    def disable_output(self) -> UtilityView:
        if self.phase == "quit":
            return self.status()
        self.output_enabled = False
        self.generation += 1
        self.phase = "disabled"
        self.worker.cancel()
        self._close()
        return self.status()

    def toggle_output(self) -> UtilityView:
        if self.output_enabled:
            return self.disable_output()
        return self.enable_output()

    def accept_billing(self) -> UtilityView:
        self.billing_accepted = True
        if self.phase == "error" and self.output_enabled:
            self.phase = "ready"
            self.last_error = ""
        return self.status()

    def start_recording(self) -> UtilityView:
        if self.phase in {"needs_setup", "device_lost", "quit"} or not self.output_enabled:
            return self.status()
        if self.phase != "ready":
            return self.status()
        if self.billing_required and not self.billing_accepted:
            self.phase = "error"
            self.last_error = "Accept speech credits for this session before recording. No request was sent."
            return self.status()
        if self.require_model and not self.whisper_ready:
            self.phase = "error"
            self.last_error = "Whisper small.en is not prepared. No request was sent."
            return self.status()
        if self.audio_opener is not None:
            try:
                self.audio_opener()
            except Exception as exc:
                self.last_error = self._safe(exc)
                self.phase = "error"
                self._close()
                return self.status()
        self.phase = "recording"
        return self.status()

    def stop_recording(self, pcm: bytes | None = None) -> UtilityView:
        if self.phase != "recording":
            return self.status()
        if not self.output_enabled:
            self.generation += 1
            self.phase = "disabled"
            self.worker.cancel()
            self._close()
            return self.status()
        if pcm is None:
            pcm = self.capture_pcm() if self.capture_pcm is not None else b""
        self.generation += 1
        generation = self.generation
        self.phase = "transcribing"
        self.worker.start(pcm, generation, self.on_phase, self.on_done)
        return self.status()

    def on_phase(self, generation: int, phase: str) -> None:
        if generation != self.generation or not self.output_enabled:
            return
        if self.phase in {"quit", "disabled", "device_lost"}:
            return
        if phase in {"transcribing", "generating", "playing"}:
            self.phase = phase

    def on_done(self, generation: int, result: object) -> None:
        if generation != self.generation or not self.output_enabled:
            return
        if self.phase in {"quit", "disabled", "device_lost"}:
            return
        if isinstance(result, BaseException):
            self.last_error = self._safe(result)
            self.output_enabled = False
            self.phase = "error"
            self._close()
            return
        self.phase = "ready"
        self.last_error = ""

    def connect(self, typed_key: str | None) -> UtilityView:
        key = self._store_or_read_key(typed_key)
        self._redact_secret = key
        self._cancel_busy()
        self.account_label = self.account.verify(key)
        self.key_in_memory = key
        self.connected = True
        self.last_error = ""
        return self.status()

    def disconnect(self) -> UtilityView:
        self._cancel_busy()
        try:
            self.secrets.delete_secret(SERVICE, ACCOUNT)
        except SecretStoreError as exc:
            self.last_error = self._safe(exc)
        self.key_in_memory = None
        self.connected = False
        self.account_label = "disconnected"
        self.voices = []
        return self.status()

    def refresh_voices(self) -> UtilityView:
        self.voices = list(self.account.list_voices(self._key()))
        return self.status()

    def select_voice(self, voice_id: str, manual: bool = False) -> UtilityView:
        if manual:
            cleaned = str(voice_id).strip()
            if not cleaned:
                raise ValueError("A manual voice ID is required.")
            chosen_id = cleaned
            label = "Manual ID"
        else:
            match = next((item for item in self.voices if item["voice_id"] == voice_id), None)
            if match is None:
                raise ValueError("That voice was not returned by ElevenLabs.")
            chosen_id = match["voice_id"]
            label = match["name"]
        self._cancel_busy()
        self.voice_id = chosen_id
        self.voice_label = label
        self.voice_manual = manual
        self._save_settings()
        return self.status()

    def select_devices(self, input_query: str | None, output_query: str | None) -> UtilityView:
        catalog = list(self.catalog_provider())
        chosen_in, chosen_out = select_devices(catalog, input_query, output_query, False)
        self._cancel_busy()
        self.input_query = input_query or ""
        self.output_query = output_query or ""
        self.input_name = chosen_in.name
        self.output_name = chosen_out.name
        self.setup_problem = ""
        if self.output_enabled and self.phase not in {"quit", "device_lost"}:
            self.phase = "ready"
        elif self.phase not in {"quit", "device_lost"}:
            self.phase = "disabled"
        self._save_settings()
        return self.status()

    def refresh_devices(self) -> UtilityView:
        try:
            catalog = list(self.catalog_provider())
        except Exception as exc:
            self.last_error = self._safe(exc)
            return self.note_device_lost()
        if self.output_name and not any(device.name == self.output_name for device in catalog):
            return self.note_device_lost()
        problem = missing_cable_message(catalog)
        if problem and not self.output_name:
            self.setup_problem = problem
            self.output_enabled = False
            self.phase = "needs_setup"
        return self.status()

    def note_device_lost(self) -> UtilityView:
        self.output_enabled = False
        self.generation += 1
        self.input_name = ""
        self.output_name = ""
        self.phase = "device_lost"
        self.last_error = "Virtual cable unavailable. Output stays silent."
        self.worker.cancel()
        self._close()
        return self.status()

    def note_sleep(self) -> UtilityView:
        if self.phase == "quit":
            return self.status()
        self.output_enabled = False
        self.generation += 1
        self.phase = "disabled"
        self.worker.cancel()
        self._close()
        return self.status()

    def quit(self) -> UtilityView:
        self.output_enabled = False
        self.generation += 1
        self.phase = "quit"
        self.worker.cancel()
        self._close()
        return self.status()

    def prepare_model(self, background: bool = True) -> UtilityView:
        token = object()
        self._prepare_token = token
        self.model_progress = "starting"
        view = self.status()

        def run() -> None:
            try:
                def progress(message: str) -> None:
                    if self._prepare_token is not token:
                        raise LocalModelMissing("Model download cancelled.")
                    self.model_progress = message

                target = self.support / "whisper"
                if self.downloader is None:
                    from voice_clone_mic.ptt.whisper_backend import prepare_official_model

                    prepare_official_model(target)
                else:
                    self.downloader(target, progress)
                if self._prepare_token is not token:
                    self.model_progress = "cancelled"
                    return
                self.refresh_whisper()
                self.model_progress = "ready" if self.whisper_ready else "error"
            except Exception as exc:
                if self._prepare_token is not token or "cancelled" in str(exc).lower():
                    self.model_progress = "cancelled"
                    return
                self.model_progress = "error"
                self.last_error = self._safe(exc)

        if background:
            self._prepare_thread = threading.Thread(target=run, name="whisper-prepare", daemon=True)
            self._prepare_thread.start()
        else:
            run()
        return view

    def cancel_prepare(self) -> UtilityView:
        self._prepare_token = object()
        self.model_progress = "cancelled"
        return self.status()

    def model_directories(self) -> list:
        ordered = []
        for directory in [self.support / "whisper", *self.known_dirs]:
            if directory not in ordered:
                ordered.append(directory)
        return ordered

    def refresh_whisper(self) -> None:
        if self.model_checker is not None:
            found = self.model_checker()
        else:
            found = find_whisper_model(self.model_directories())
        self.whisper_ready = found is not None
        self.whisper_path = str(found) if found else ""

    def should_open_settings(self) -> bool:
        return bool(self.setup_problem) or not self.output_name or (self.billing_required and not self.connected)

    def poll(self) -> None:
        if self.fault_check is not None and self.phase not in {"quit", "device_lost", "needs_setup"}:
            try:
                fault = self.fault_check()
            except Exception as exc:
                self.last_error = self._safe(exc)
                self.note_device_lost()
                return
            if fault:
                self.note_device_lost()
                return
        if self.phase in {"quit", "needs_setup"} or not self.output_name:
            return
        try:
            catalog = list(self.catalog_provider())
        except Exception as exc:
            self.last_error = self._safe(exc)
            self.note_device_lost()
            return
        if not any(device.name == self.output_name for device in catalog):
            self.note_device_lost()

    def _store_or_read_key(self, typed_key: str | None) -> str:
        key = (typed_key or "").strip()
        if key:
            self.secrets.set_secret(SERVICE, ACCOUNT, key)
            return key
        stored = self.secrets.get_secret(SERVICE, ACCOUNT)
        if stored:
            return stored
        if self.env_key:
            return self.env_key
        raise SecretStoreError("No API key is available. Nothing was contacted.")

    def _key(self) -> str:
        if self.key_in_memory:
            return self.key_in_memory
        stored = self.secrets.get_secret(SERVICE, ACCOUNT)
        if stored:
            return stored
        if self.env_key:
            return self.env_key
        raise SecretStoreError("No API key is available. Nothing was contacted.")

    def _cancel_busy(self) -> None:
        busy = self.phase in {"recording", "transcribing", "generating", "playing"}
        self.generation += 1
        self.worker.cancel()
        if busy:
            self._close()
            self.phase = "ready" if self.output_enabled else "disabled"

    def _close(self) -> None:
        if self.closer is not None:
            self.closer()

    def _verify_saved_devices(self) -> None:
        try:
            catalog = list(self.catalog_provider())
        except Exception as exc:
            self.phase = "needs_setup"
            self.setup_problem = self._safe(exc)
            return
        problem = missing_cable_message(catalog)
        if self.input_query or self.output_query:
            try:
                chosen_in, chosen_out = select_devices(
                    catalog,
                    self.input_query or None,
                    self.output_query or None,
                    False,
                )
            except DeviceSelectionError as exc:
                self.input_name = ""
                self.output_name = ""
                self.phase = "needs_setup"
                self.setup_problem = problem or str(exc)
                return
            self.input_name = chosen_in.name
            self.output_name = chosen_out.name
            self.setup_problem = ""
            self.phase = "disabled"
            return
        self.phase = "needs_setup"
        self.setup_problem = problem or (
            "Choose a physical microphone and a virtual cable. This app does not choose speakers for you."
        )

    def _load_settings(self) -> None:
        path = self.support / "settings.json"
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        self.input_query = _text(payload.get("input_device"))
        self.output_query = _text(payload.get("output_device"))
        self.voice_id = _text(payload.get("voice_id"))
        self.voice_label = _text(payload.get("voice_label"))
        self.voice_manual = bool(payload.get("voice_manual"))

    def _save_settings(self) -> None:
        self.support.mkdir(parents=True, exist_ok=True)
        payload = {
            "input_device": self.input_query,
            "output_device": self.output_query,
            "voice_id": self.voice_id,
            "voice_label": self.voice_label,
            "voice_manual": self.voice_manual,
        }
        text = json.dumps(payload, indent=2)
        secret = self.key_in_memory or self.env_key
        if secret and secret in text:
            raise SecretStoreError("Refusing to write the API key into settings.")
        (self.support / "settings.json").write_text(text + "\n", encoding="utf-8")

    def _safe(self, exc: object) -> str:
        text = str(exc)
        for secret in (self.key_in_memory, self.env_key, self._redact_secret):
            if secret:
                text = text.replace(secret, "[redacted]")
        return " ".join(text.split())[:180]


class UtilityMemoryWorker:
    def __init__(self) -> None:
        self.submits: list[tuple[int, bytes]] = []
        self.cancels = 0
        self._on_phase = None
        self._on_done = None
        self._generation: int | None = None

    def start(self, pcm: bytes, generation: int, on_phase, on_done) -> None:
        self.submits.append((generation, pcm))
        self._generation = generation
        self._on_phase = on_phase
        self._on_done = on_done

    def advance(self, phase: str) -> None:
        if self._on_phase is not None and self._generation is not None:
            self._on_phase(self._generation, phase)

    def finish(self, result: object = None) -> None:
        if self._on_done is not None and self._generation is not None:
            self._on_done(self._generation, result)

    def cancel(self) -> None:
        self.cancels += 1


class LazySessionWorker:
    """Builds the speech session on the first stop, not when output is enabled."""

    def __init__(self, factory) -> None:
        self.factory = factory
        self._worker = None

    def start(self, pcm: bytes, generation: int, on_phase, on_done) -> None:
        if self._worker is None:
            self._worker = self.factory()
        self._worker.start(pcm, generation, on_phase, on_done)

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()


class BoundSessionWorker:
    """Uses the session armed by start_recording, and builds it only after that."""

    def __init__(self, audio: dict) -> None:
        self.audio = audio
        self._worker: PhaseSessionWorker | None = None
        self._session = None

    def start(self, pcm: bytes, generation: int, on_phase, on_done) -> None:
        session = self.audio.get("session")
        if session is None:
            on_done(generation, RuntimeError("Recording was not armed. No request was sent."))
            return
        if self._worker is None or self._session is not session:
            self._session = session
            self._worker = PhaseSessionWorker(session)
        self._worker.start(pcm, generation, on_phase, on_done)

    def cancel(self) -> None:
        session = self.audio.get("session")
        if session is not None:
            session.cancel()


class PhaseSessionWorker:
    """Runs one utterance off the UI thread and reports transcribe, generate, and play."""

    def __init__(self, session: object) -> None:
        self.session = session

    def start(self, pcm: bytes, generation: int, on_phase, on_done) -> None:
        import asyncio

        def run() -> None:
            def report(phase: str) -> None:
                on_phase(generation, phase)

            try:
                result = asyncio.run(self.session.submit(pcm, on_phase=report))
            except Exception as exc:
                on_done(generation, exc)
                return
            on_done(generation, result)

        threading.Thread(target=run, name="utility-utterance", daemon=True).start()

    def cancel(self) -> None:
        self.session.cancel()


def scripted_utility_smoke(controller: UtilityController, utterance: bytes) -> list[str]:
    titles = [controller.status().title]
    controller.select_devices("Built-in Microphone", "BlackHole 2ch")
    titles.append(controller.enable_output().title)
    titles.append(controller.start_recording().title)
    controller.accept_billing()
    titles.append(controller.start_recording().title)
    titles.append(controller.stop_recording(utterance).title)
    controller.worker.advance("generating")
    titles.append(controller.status().title)
    controller.worker.advance("playing")
    titles.append(controller.status().title)
    titles.append(controller.disable_output().title)
    controller.worker.finish("ok")
    titles.append(controller.enable_output().title)
    titles.append(controller.quit().title)
    return titles


def missing_cable_message(devices: list[DeviceInfo]) -> str | None:
    virtual = [device for device in devices if device.max_output_channels > 0 and loop_key(device.name)]
    if virtual:
        return None
    return (
        "BlackHole is not installed. This app does not install it. "
        "Live recording stays disabled until a virtual cable output is available. "
        "The meeting must select that cable. This app does not mute Zoom or Teams."
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
