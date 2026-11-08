# voice-clone-mic

Phrase-buffered voice changer for a laptop microphone. It cuts speech into short segments, sends each segment to the ElevenLabs Voice Changer stream, and writes the converted audio to a virtual cable you already installed. Teams, Zoom, OBS, or a recorder then select that cable as their microphone.

This is not a zero-latency or bidirectional speech session. A phrase waits for 400 ms of silence, or for a 4 second forced cut, before conversion starts. Bursts shorter than 200 ms are not sent. Network time is added on top. The status line `observed_last_segment_ms` is only the clock from "segment ready" to the first converted sample for that segment. It is not a guaranteed mouth-to-ear latency, and this repository does not publish one.

The app does not install BlackHole or VB-CABLE, and it does not create an operating-system virtual device. You install the cable. This process only plays audio into it.

## Cable direction

```
physical microphone
  -> this app
  -> playback side of the cable
  -> capture side of the cable
  -> the meeting app's microphone selector
```

- macOS: install [BlackHole](https://github.com/ExistentialAudio/BlackHole) yourself. Point `--output` at the BlackHole device, for example `BlackHole 2ch`. In Teams, Zoom, or OBS, choose that same BlackHole device as the microphone. Do not also select BlackHole as this app's input.
- Windows: install [VB-CABLE](https://vb-audio.com/Cable/) yourself. Point `--output` at the playback endpoint, usually `CABLE Input`. In the meeting app, choose the capture endpoint, usually `CABLE Output`.

`--output` is required. The app will not fall back to the speakers or the default output. If the chosen input and output look like the same cable, startup stops so the cable cannot feed itself.

## Compatibility

The intended macOS machine is Ventura 13.7.8 on an Intel Core i5 with Intel Iris Plus graphics. Python is pinned to 3.11 and 3.12, and numpy is pinned to the 1.26 line, because those releases publish Intel macOS wheels. Nothing here requires Apple Silicon.

That is an intent, not a result from this change. The automated tests do not open a microphone, BlackHole, VB-CABLE, or the ElevenLabs API. GitHub-hosted `macos-latest` is not an Intel Ventura machine. A green CI run would still not be Intel hardware validation, and this change has not observed CI.

## Install

Windows and macOS both use the same steps from a terminal. On Windows, use `py -3.12` if `python` is not Python 3.11 or 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
voice-clone-mic --help
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead of `source`.

The macOS and Windows wheels of `sounddevice` include PortAudio, so a normal Intel or Windows install does not need a compiler or a kernel driver. This repository does not install BlackHole, VB-CABLE, or PortAudio itself. Linux is not a target: `sounddevice` there expects a system PortAudio library, and the Ubuntu test job never imports it.

## Configuration

```bash
cp .env.example .env
```

Leave `ELEVENLABS_API_KEY=` empty until you have decided to spend API quota. `.env` is gitignored. The example file has blanks, not a key. A key inside `.vcm/config.json` is ignored; only the voice id is read from that file.

```bash
voice-clone-mic use-voice YOUR_VOICE_ID
voice-clone-mic config
```

`config` prints `api_key=set` or `api_key=missing`. It does not print the key.

## Safe commands

These do not contact ElevenLabs and do not need a key:

```bash
voice-clone-mic devices --fake
voice-clone-mic demo
voice-clone-mic run --mock --fake --once --output BlackHole
```

`demo` plays a synthetic phrase through the mock converter into a fake BlackHole buffer and exits. `--mock` XORs the PCM so the software tests can prove the output is not a copy of the microphone. It is not a preview of your cloned voice.

`voice-clone-mic devices` without `--fake` queries the local PortAudio devices. It does not open a stream.

While a live `run` is active, type `status`, `mute`, `unmute`, `stop`, `help`, or `quit`.

## Live API, later

Voice Changer is billed by audio duration. ElevenLabs documents the current stream as `POST /v1/voice-changer/{voice_id}/stream`. This client sends 16 kHz mono PCM (`pcm_s16le_16`) and asks for `pcm_24000`. The recommended model is `eleven_multilingual_sts_v2`. `optimize_streaming_latency` is deprecated and is not sent.

Confirm the current price in the ElevenLabs dashboard before the first live run. A published skill for this API describes billing as 1,000 characters per minute of audio; treat that as a hint to check, not as your invoice. `demo`, `--mock`, and the test suite do not call the API.

A live run also requires the billing flag, a key, a voice id, and an explicit virtual-cable output:

```bash
voice-clone-mic voices --i-accept-api-billing
voice-clone-mic run --output "BlackHole 2ch" --i-accept-api-billing
```

On Windows the output name is the playback side, for example `--output "CABLE Input"`.

There is no automatic retry. A timed-out or failed segment is not sent again, so a lost response is not billed twice by this process. The server may already have counted a request that this process never finished reading.

`enable_logging` is left at the API default (`true`). That default is not enterprise zero-retention. Do not read the defaults as a promise that ElevenLabs discards the audio.

## Privacy

On a live run, microphone audio leaves the laptop and is uploaded to ElevenLabs. Logs record sequence numbers, durations, and byte counts. They do not record the key or the PCM. The app does not write wav, pcm, or mp3 recordings.

Mute, stop, and a device fault cancel in-flight requests and clear playback. Only converted audio or silence is written to the cable. Underruns are silence. Raw microphone audio is not a fallback.

## Optional push-to-talk

`run` is still speech-to-speech. `ptt` is a separate mode: one bounded recording, local Whisper on CPU, then one ElevenLabs text-to-speech stream into the same kind of virtual cable. Prosody is regenerated. Delay is transcription time plus the TTS request. No end-to-end latency number is claimed.

The default model name is OpenAI Whisper `small.en`. Official `openai-whisper` is the backend because current PyTorch macOS wheels are Apple Silicon only, and `faster-whisper` depends on `onnxruntime`, which has no Intel macOS wheel for Python 3.12. The optional extra pins `torch==2.2.2`, `numba==0.61.2`, and `llvmlite==0.44.0`, which publish Intel (`macosx_10_9` / `macosx_10_14` x86_64) and Windows amd64 wheels for Python 3.11 and 3.12. Those tags can run on Ventura 13. They were not installed or executed on that machine. Install this extra in its own virtualenv, not the RVC environment.

```bash
python -m voice_clone_mic ptt demo
python -m voice_clone_mic ptt run --mock --fake --once --output BlackHole
```

Both stay on the machine and do not call ElevenLabs. A real model download is explicit:

```bash
python -m pip install -e ".[ptt]"
python -m voice_clone_mic ptt prepare-model
```

`prepare-model` is the only command that downloads weights. Later transcription refuses to start unless `.vcm/whisper/small.en.pt` matches the published SHA256. Live TTS still needs `--i-accept-api-billing`, `ELEVENLABS_TTS_MODEL` (default `eleven_flash_v2_5`), and a voice id. The speech-to-speech model variable is unchanged.

Design notes: `docs/superpowers/specs/2026-11-08-ptt-whisper-tts-design.md`.

## Optional RVC training checkout

The ElevenLabs commands above are unchanged. A separate, optional checker can verify an external Retrieval-based Voice Conversion checkout at commit `81eed5e8f68b6bed1789f682fe78cdd324495afc` and patch only its WebUI bind to `127.0.0.1` with sharing and the browser left off. It does not train, record, or download weights. Upstream `webui.py` at that commit listens on `0.0.0.0` and has no host flag, so an unpatched launch is refused.

```bash
python -m voice_clone_mic.rvc_tools check --root /path/to/rvc
python -m voice_clone_mic.rvc_tools audit
```

Details, the checkpoint-load audit, and the Windows commands are in `docs/rvc-localhost-training.md`. `state=patched` means the reviewed source patch is present. It does not mean the training UI was started or that a model loaded.

## Tests

```bash
python -m pip install -e ".[dev]"
pytest -q
```

The suite uses fake capture, fake playback, and an HTTP mock. It does not need a microphone, a virtual cable, a network connection, or an API key.

## Limits

- A PortAudio overflow or underflow does not switch the cable over to the raw microphone. Excess capture is dropped. A stopped stream or a callback exception mutes output and clears playback.
- Linear resampling, not a band-limited sample-rate converter.
- Virtual-cable detection is a name check for BlackHole and VB-CABLE.
- Continuous speech is forced into 4 second requests. Short noises under 200 ms are dropped.
- Intel Ventura 13.7.8 and a real meeting app have not been exercised here.
- Passing the mock tests does not mean the live API accepted your voice or that a meeting can hear the cable.
