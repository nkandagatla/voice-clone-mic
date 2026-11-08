# Mac utility implementation plan

> **For agentic workers:** Implement on `cursor/voice-clone-mic-mvp-c335`. Do not merge. Do not run the menu bar, install packages on the user's Mac, or call ElevenLabs.

**Goal:** Add a settings-backed utility controller and an unsigned Mac launcher for converted-voice enable, record, and local Whisper TTS.

**Architecture:** `UtilityController` owns enablement, billing acceptance, account data, device choice, and generation. Cocoa and rumps are imported only when `ptt utility` is launched without `--smoke`. The existing `MenuController` and `ptt menu --smoke` stay as they are.

**Tech Stack:** Python 3.11/3.12. Menu extra remains `rumps==0.4.0` and `pyobjc-framework-Cocoa==13.0`, plus `pyobjc-framework-Security==13.0` for the keychain. Those Cocoa wheels are universal2 and list macOS 10.9 or 10.13 as the minimum, which includes Ventura 13.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-11-08-mac-utility-design.md`.
- Preserve the current menu smoke titles and the other existing tests.
- No plaintext key file, no key in subprocess argv, no transcript log.
- `GET /v1/user` and `GET /v1/voices` only from a connect or refresh action.
- Output enable does not record or call TTS. Disable cancels and does not replay.
- Known Whisper directories only, in the spec order.

---

### Task 1: Paths, secrets, and account client

**Files:**
- Create: `src/voice_clone_mic/ptt/paths.py`
- Create: `src/voice_clone_mic/ptt/secrets.py`
- Create: `src/voice_clone_mic/ptt/account.py`
- Test: `tests/test_ptt_utility.py`

**Interfaces:**
- `known_whisper_dirs(home, cwd) -> list[Path]`
- `find_whisper_model(directories) -> Path | None`
- `SecretStoreError`
- `MemorySecretStore.set_secret/get_secret/delete_secret`
- `KeychainSecretStore(backend)` raises `SecretStoreError` when `backend` is None and does not call a process
- `ElevenLabsAccount.verify(api_key) -> str` and `list_voices(api_key) -> list[dict]`

- [x] Tests for directory order, hash match, keychain refusal, 401 redaction, voice list contents, and timeout
- [x] Implement
- [x] `pytest tests/test_ptt_utility.py -q`

### Task 2: Utility controller

**Files:**
- Create: `src/voice_clone_mic/ptt/utility.py`
- Test: `tests/test_ptt_utility.py`

**Interfaces:**
- `UtilityController.enable_output/disable_output/accept_billing/start_recording/stop_recording`
- `connect(typed_key)`, `disconnect`, `refresh_voices`, `select_voice`, `select_devices`
- `on_phase(generation, phase)`, `on_done(generation, result)`, `prepare_model()`
- `scripted_utility_smoke(controller, pcm) -> list[str]`

- [x] Tests for disabled start, billing gate, one submit, disable ignoring a late callback, settings change cancelling work, and settings.json omitting the key
- [x] Implement
- [x] `pytest tests/test_ptt_utility.py -q`

### Task 3: Command, window binding, and bundle

**Files:**
- Create: `src/voice_clone_mic/ptt/settings_window.py`
- Modify: `src/voice_clone_mic/ptt/cli.py`
- Modify: `src/voice_clone_mic/cli.py`
- Modify: `src/voice_clone_mic/ptt/mac_app.py`
- Modify: `tests/test_ptt_menu.py`
- Modify: `README.md`

**Interfaces:**
- `voice-clone-mic ptt utility --smoke` prints the scripted phases and does not import `rumps`
- `SETTINGS_BUTTONS` maps Connect, Refresh voices, Enable output, Disable output, Record, Stop, and Prepare model to controller methods
- The bundle executable runs `ptt utility`, cds to the known runtime when present, and contains no API key

- [x] Tests for smoke output, missing Cocoa, and the bundle
- [x] Implement
- [x] `pytest -q`

### Task 4: Review

- [x] Confirm `ptt menu --smoke --fake` still prints the previous mute titles
- [x] Confirm no test opens a socket or imports whisper
- [x] Do not claim the settings window was shown

## Self-review

The existing `MenuController` phases and `ptt menu --smoke` titles stay. The utility is a second controller, so the 94 tests keep their assertions except the bundle launcher, which must exec `ptt utility` because that is the approved double-click behavior. The bundle test is updated to that command and still checks that the script is unsigned, has no model download, and has no API key.

Locked interfaces:

- `known_whisper_dirs(home, cwd)` returns Application Support `whisper`, `~/Applications/VoiceCloneMicRuntime/runtime/.vcm/whisper`, then `cwd/.vcm/whisper`.
- `find_whisper_model(directories, name="small.en")` returns the first `small.en.pt` whose SHA256 equals the published hash. No other directories are visited.
- `KeychainSecretStore(None).set_secret` raises `SecretStoreError` and the message does not contain the secret. The store never calls `subprocess`.
- `ElevenLabsAccount.verify` sends `GET /v1/user`. `list_voices` sends `GET /v1/voices` and returns only `voice_id` and `name` from the body. A missing name is skipped.
- `UtilityController` starts with `output_enabled False`. `enable_output` does not call the worker. `accept_billing` is required before `start_recording` when `billing_required` is true. `disable_output`, device loss, sleep, and quit increment generation, cancel the worker, and call the closer. A late `on_done` is ignored. `settings.json` may store the device queries and voice id, never the API key.
- `SETTINGS_BUTTONS` is the only mapping the window uses. `open_settings_window` imports AppKit inside the function.
- `ptt utility --smoke` is headless. `ptt utility --fake` returns exit 2 when Cocoa is missing and does not construct an HTTP client.
