# Mac utility: settings and converted-voice switch

Date: 2026-11-08
Status: approved. Build only. Do not install onto the Intel Mac that is already running commit `b357771`, and do not call ElevenLabs or open a microphone from this change.

## Product

One unsigned Mac utility opens a settings window when it is not configured, and a menu bar for daily use. Routine setup after the `.app` is installed does not require Terminal. Speech-to-speech, RVC, and the existing `ptt` commands stay as they are.

The output switch starts disabled. Enabling it does not record and does not call ElevenLabs. Record, then stop, still produces one local Whisper transcript and at most one TTS stream. Disabling the output silences this app's virtual-cable playback and cancels capture, transcription, and any TTS request. It does not install or remove a virtual device, and it does not mute the Mac microphone, Zoom, or Teams. The meeting must select the cable itself.

Enabling the output again does not replay audio from the disabled session.

## Settings

The window is native Cocoa, opened from the menu's Settings item and automatically when output, voice, or a live account is missing. Buttons call the same Python controller the menu uses.

- Masked API key field. Connect and Disconnect are explicit.
- Connect verifies `GET /v1/user`. Refresh voices calls `GET /v1/voices` and lists only returned `voice_id` and `name` pairs. Neither call synthesizes speech.
- A manual voice ID field is labeled manual. It is not presented as a name returned by the API.
- Physical input and virtual-cable output use the existing device rules. Speakers and a loopback input are refused. Refresh re-reads the device list. The saved names are checked again on launch and when a device disappears.
- Whisper `small.en` shows ready or missing. Prepare downloads only after the button press, on a worker thread, with hash validation. Cancel ignores a late completion.
- Live recording requires a once-per-session acceptance that speaking uses ElevenLabs credits. Account and voice reads do not require that acceptance. Launch arguments are not the only way to grant it.

## Credentials

A new key is stored only through an OS keychain backend. The implementation uses Security.framework via PyObjC when that framework imports. If it does not, saving fails and nothing is written to a plaintext file. The key is not placed in subprocess arguments or logs. An existing `.env` key can be used in memory when the user connects without typing a replacement. It is not copied into the keychain or into the support file unless the user typed it and the keychain accepted it.

Non-secret choices live in `~/Library/Application Support/VoiceCloneMic/settings.json`. The API key is not a field there.

## Model paths

The app looks only at these directories, in order, and does not scan the disk:

1. `~/Library/Application Support/VoiceCloneMic/whisper`
2. `~/Applications/VoiceCloneMicRuntime/runtime/.vcm/whisper` (the already installed runtime)
3. `./.vcm/whisper` relative to the process working directory

The installed file `.../runtime/.vcm/whisper/small.en.pt` remains in place. A new download is written to Application Support. The app uses the first directory whose `small.en.pt` matches the published SHA256.

## Phases

`disabled`, `ready`, `recording`, `transcribing`, `generating`, `playing`, `error`, plus `needs_setup`, `device_lost`, and `quit`. The menu title shows that phase. Workers check the generation before they change phase or audio. Settings changes during work increment the generation and cancel first.

## Packaging

The unsigned bundle's executable launches `python -m voice_clone_mic ptt utility` with no secret arguments. It `cd`s to `~/Applications/VoiceCloneMicRuntime/runtime` when that directory exists, and exports `VOICE_CLONE_MIC_SUPPORT` to Application Support. It does not download a model, enable output, accept billing, or register a login item. It is not signed or notarized, and it is not a self-contained app: it uses the external virtualenv whose Python path was written into the launcher.

## Tests

Controller, keychain failure, account mocks, device changes, billing, and cancellation are unit tests. `ptt menu --smoke` stays headless. `ptt utility --smoke` is the new headless settings-and-switch script. Neither opens Cocoa or the network. This environment cannot show the Cocoa window, so the GUI is not claimed as visually tested.
