# Voice-clone mic MVP design

Date: 2026-11-07
Status: approved for implementation by the user's existing architecture decision. This document records that decision, the verified transport, and the contracts the CLI must meet. It is not a claim that a physical microphone, BlackHole, VB-CABLE, or the live ElevenLabs API was exercised while writing this spec.

## Problem

A person speaking into the laptop's physical microphone needs that speech converted to their own ElevenLabs cloned voice and presented as a virtual microphone that Teams, Zoom, OBS, or a recorder can select.

The user accepts noticeable delay. The product is a phrase-buffered voice changer, not a zero-latency bidirectional speech-to-speech session, and not a speech-to-text then text-to-speech substitute.

Targets:

- macOS Ventura 13.7.8 on Intel Core i5 with Intel Iris Plus graphics. This is a compatibility target. Nothing in this repository may require Apple Silicon.
- Windows on a conventional x64 laptop.

The app does not install drivers, create an OS virtual audio device, change system settings, train or upload a voice, or ship a kernel extension. The user installs BlackHole on macOS or VB-CABLE on Windows themselves. This app only writes converted audio to the playback side of that already-installed cable.

## Verified transport

Checked against the official page `https://elevenlabs.io/docs/api-reference/speech-to-speech/stream` on 2026-11-07.

- Method and path: `POST /v1/voice-changer/{voice_id}/stream`
- Default host: `https://api.elevenlabs.io`
- Header: `xi-api-key`
- Body: `multipart/form-data` with the audio file field `audio`, form field `file_format=pcm_s16le_16`, and form field `model_id=eleven_multilingual_sts_v2`
- Query: `output_format=pcm_24000`
- Input contract for `pcm_s16le_16`: 16-bit PCM, 16 kHz, mono, little-endian
- `optimize_streaming_latency` is documented on that page as deprecated. This client must not send it.
- The older speech-to-speech path and any STT-then-TTS path are out of scope.
- `enable_logging` is left unset, so the API default (`true`) applies. That default is not enterprise zero-retention. This app must not describe itself as zero-retention.
- Voice listing, used only by an explicit command, is `GET /v1/voices` with the same API key header. The command reads `voice_id` and `name` only.

`pcm_24000` is a headerless PCM response. This MVP decodes it as signed 16-bit little-endian mono at 24 kHz, the same PCM layout ElevenLabs documents for the `pcm_s16le_16` input and the layout its PCM outputs use in the official SDKs. Startup refuses to run if `sys.byteorder != "little"` so that layout cannot be misread on an unexpected architecture.

The client follows no redirects. A 3xx response is a failed segment, not a second request.

## Approaches considered

1. Bidirectional low-latency streaming duplex. Rejected. The user accepted phrase delay, and the approved architecture is speech-segmented requests with a streamed PCM response.
2. STT, then TTS in the cloned voice. Rejected. It changes timing and delivery and is a different product.
3. Speech-segmented capture, one Voice Changer stream per segment, ordered playback into a user-installed virtual cable. Selected.

## Pipeline

```
physical mic callback
  -> bounded capture buffer (device rate, drop oldest on overflow)
  -> async pump (never called from the audio callback)
  -> streaming resample to 16 kHz mono int16
  -> speech segmenter
  -> duration-bounded unsent queue
  -> at most two in-flight POSTs, each segment submitted once
  -> streamed PCM chunks
  -> odd-byte assembler (per segment)
  -> streaming resampler 24 kHz -> output device rate (per segment)
  -> sequence gate
  -> bounded playback buffer
  -> output callback writes converted samples or silence
```

The PortAudio callback only copies bytes into or out of a bounded buffer under a short lock. It does not call asyncio, allocate without a bound, log audio, or touch the network.

The asyncio loop is the only place that segments audio, starts HTTP tasks, and pushes converted samples. A control command arriving from a stdin thread is handed to the loop with `call_soon_threadsafe`.

## Segmentation

Defaults, all multiples of a 20 ms frame at 16 kHz:

| Parameter | Value | Behavior |
| --- | --- | --- |
| RMS threshold | 500 | Frame is speech at or above this int16 RMS |
| Pre-roll | 200 ms | Leading context attached when speech starts. Not reattached after a forced cut |
| Minimum speech | 200 ms | Shorter bursts are discarded and not requested |
| Hangover | 400 ms | Trailing silence included, then the segment closes |
| Maximum segment | 4000 ms | Forced cut on the next frame boundary. No overlap with the next segment |

A trailing partial frame at flush is dropped. Flush emits an open segment only when it is allowed to close. Mute, stop, and device fault discard the open segment instead of flushing it.

The minimum speech length rejects isolated blips: a phrase that ends by hangover or flush without ever reaching the forced-cut length is discarded when its loud frames are shorter than the minimum. A forced cut is emitted if it contains any loud frame. The phrase stays open after that cut, including when the cut frame itself is under the RMS threshold, until hangover has elapsed. Later audio is not given a new pre-roll, and a short tail is still emitted. Returning to idle clears the exemption, so the next blip is judged on its own.

Continuous speech is cut into back-to-back segments whose samples concatenate to the speech that was segmented, with no duplicated frames. That bound is what keeps a single request from growing toward the API's multi-minute maximum. The request guard still refuses any payload over 5.0 seconds (`16000 * 2 * 5` bytes) or with an odd length, and it does not send a replacement.

## Backpressure

Bounds are durations, not object counts.

| Buffer | Bound | Overflow behavior |
| --- | --- | --- |
| Capture | 2.0 s of device-rate audio | Drop oldest captured audio. Nothing from this buffer is written to the output |
| Unsent segments | 8.0 s | Drop the oldest unsent segment and mark its sequence failed so later sequences are not stuck. A segment that is already in flight is not cancelled by overflow and is not submitted again |
| In flight | 2 requests | Further segments stay unsent until a slot frees |
| Out-of-order hold | 12.0 s of not-yet-playable converted audio | Drop the newest held sequence, mark it failed, and play nothing for it when its turn arrives |
| Playback | 6.0 s | Keep the prefix that fits and drop the tail. The callback never blocks |

6.0 s is one 4.0 s forced-cut segment plus headroom after resampling to the device rate. A 3.0 s cap would reject an entire max-length phrase when it arrives in one chunk.

Those stages are independent, so retained audio cannot grow past the sum: 2.0 s of capture plus 8.0 s unsent plus at most two 4.0 s in-flight requests plus 12.0 s held converted audio plus 6.0 s of playback. Overflow at every stage discards; it never blocks the callback and never writes the discarded microphone audio to the output.

A conversion failure is a 422, 5xx, timeout, redirect, or disconnect. A successful segment resets the consecutive-failure counter. User mute, stop, overflow drops, and fatal 401/403 do not increment it. The fifth consecutive conversion failure pauses new submissions until `unmute`.

There is no automatic retry. A segment that was handed to the client counts as submitted even if the socket fails afterward. Timeout, HTTP failure, and cancellation do not start a second POST for the same sequence. This is the rule that avoids a duplicate billed conversion.

## Ordering and playback

Each segment has a sequence number and a generation. The sequence gate releases audio for sequence N as soon as chunks arrive, but only while N is the oldest unfinished sequence. Newer sequences stay held, including when their HTTP response finishes first.

When N fails, times out, or is cancelled, the gate closes N without queueing converted audio. A late chunk for N is ignored. N+1 then becomes eligible. Real time spent waiting was already silence from the output callback's underrun fill. The gate does not add a second silence pad.

Underruns write integer zero samples of the negotiated channel count. They do not write microphone audio.

Mute and stop share one operation: under the playback lock, increment the generation, clear the playback buffer, and reset the sequence gate; then cancel in-flight tasks and clear the unsent queue, segmenter, and capture resampler. A task that still holds a chunk must pass the new generation check before `playback.write`. Old audio cannot become audible after the call returns to the event loop.

Generation also resets sequence numbers to zero. Stale tasks carry the old generation and cannot enter the new gate.

## Device selection

`devices` lists host devices. It does not open a stream.

`run` never chooses an output by default.

- Input, when omitted, is the host's default input only when that device's loop key is empty. If the default input looks like BlackHole or a VB-CABLE endpoint, selection fails and asks for an explicit physical microphone.
- Output is required (`--output` or `VCM_OUTPUT_DEVICE`). A numeric query is a device index. Any other query is a case-insensitive substring and must match exactly one device.
- The matched output must have at least one output channel and must look like a virtual cable, unless `--allow-any-output` is present. That flag does not disable the feedback check.
- Virtual-cable heuristic: name contains `blackhole`, or it is a VB-Audio / VB-CABLE style endpoint (`vb-audio`, `vb-cable`, or a `cable input` / `cable output` endpoint).
- Loop key: the full lowercased BlackHole device name, so 2ch and 16ch are different devices. For VB-CABLE, the name with `input`/`output` removed, so `CABLE Input (VB-Audio Virtual Cable)` and `CABLE Output (VB-Audio Virtual Cable)` are one cable. Equal loop keys, or the same device index, refuse to start.
- An output endpoint with zero playback channels is refused with a message that names the playback side. On VB-CABLE the app writes `CABLE Input`. Meetings select `CABLE Output`. On BlackHole the app writes the BlackHole device and meetings select that device's input side. The app does not also capture it.

Sample-rate negotiation uses the device's reported default rate. Rates outside 8 kHz–192 kHz are refused. Input is requested as one channel when the device has any input channel; if the host still delivers more than one, only channel 0 is kept (channels are not summed). Output uses one channel when that is all the device has, otherwise two, and the mono converted signal is duplicated. A failure to open the selected device stops startup. The app does not fall back to another device or to the speakers.

## Fail-closed output

The playback buffer has one writer API, and it accepts converted mono samples plus a generation. There is no raw-microphone write path.

Startup validates configuration before opening the output stream. Missing voice id, missing API key, a non-HTTPS base URL, a base host that is not `elevenlabs.io` or a subdomain of it, a bad device selection, or a big-endian interpreter aborts with a non-zero status and does not open the output.

Fatal authentication (`401`, `403`) mutes conversions permanently for the process, cancels pending work, and clears playback. `unmute` cannot clear that latch. `429` and five consecutive non-fatal conversion failures pause conversions until an explicit `unmute`. A paused or muted session still outputs silence. `422`, `5xx`, timeout, redirect, and disconnect fail that segment only, with no retry, until the consecutive-failure pause trips.

Device disconnect or a stream error uses the same clear-and-mute path. Captured audio from the disconnected input is not flushed to the output.

## CLI

Console script: `voice-clone-mic`. Module: `python -m voice_clone_mic`.

| Command | Network | Behavior |
| --- | --- | --- |
| `--help` | no | Usage, cable direction, and the phrase-buffering delay statement |
| `devices` | no | List inputs and outputs. `--fake` lists the built-in fixture devices so tests need no hardware |
| `config` | no | Print whether the API key is `set` or `missing`. Never print the key |
| `use-voice VOICE_ID` | no | Store only a voice id matching `^[A-Za-z0-9_-]{8,64}$` in `.vcm/config.json` |
| `voices --i-accept-api-billing` | yes, explicit | GET `/v1/voices` and print id and name. Without the flag, send nothing |
| `demo` | no | Fake mic, fake BlackHole output, mock converter, then exit |
| `run ...` | only without `--mock` | Live or fake devices |

`run` without `--mock` also requires `--i-accept-api-billing`. Without it, no socket is opened. `--mock` uses the local converter and does not read the API key into the client.

Voice id precedence: `--voice-id`, then `ELEVENLABS_VOICE_ID`, then `.vcm/config.json`. The API key comes only from `ELEVENLABS_API_KEY` after a local `.env` load. A key found inside `.vcm/config.json` is ignored and reported as ignored. `.env` does not override variables already present in the environment.

While `run` is active, stdin accepts `status`, `mute`, `unmute`, `stop`, `help`, and `quit`. `demo` and `run --fake --once` are non-interactive so the test suite can execute them.

`status` prints state, mock flag, generation, device names, segment counters, capture/unsent/in-flight/held/playback durations in milliseconds, underruns, the observed last-segment milliseconds, and `passthrough=false`. Observed segment time is the clock from segment ready to the first converted sample written. It is a measurement of that segment, not a guaranteed mouth-to-ear latency.

Exit codes: `0` success, `2` usage or configuration, `1` runtime failure after a command started.

## Mock converter

The mock converter is not a model of ElevenLabs quality. It byte-wise XORs `0xFF` into the 16 kHz PCM and returns those bytes, split into odd-sized chunks, as the fake 24 kHz stream. Tests compute the same XOR plus the public assembler and resampler and require playback to match that reference. Playback must also differ from the microphone audio resampled straight to the output rate. That is the software proof of "no raw passthrough." It is not a proof that the live API or a virtual cable works.

## Privacy and secrets

- Microphone audio leaves the laptop only when a non-mock `run` has passed the billing flag and the device checks. `demo`, tests, and `--mock` do not open a connection.
- Logs may include sequence numbers, durations, byte counts, and sanitized status text. They must not include the API key, raw PCM, or response audio.
- The app does not write `.wav`, `.pcm`, or `.mp3` recordings. There is no recording flag in this MVP.
- `.env`, `.vcm/`, and audio suffixes are gitignored. `.env.example` contains empty placeholders only.
- Error text is sanitized: the key, if known, is replaced; non-UTF-8 bodies are omitted; remaining detail is capped at 180 characters.

Sanitized status mapping:

| Condition | Result |
| --- | --- |
| 401, 403 | Fatal mute. Message names the status. No retry. Does not use the consecutive-failure counter |
| 422 | This segment only. Message says the voice, model, or PCM format was rejected. Counts toward the five-failure pause |
| 429 | Pause until unmute. No retry of the segment. Does not use the consecutive-failure counter |
| 5xx | This segment only. Message names a server error. Counts toward the five-failure pause |
| Timeout | This segment only. Message says it was not retried. Counts toward the five-failure pause |
| Disconnect or other transport error | This segment only. Message says the connection failed and it was not retried. Counts toward the five-failure pause |
| 3xx | This segment only. Message says an unexpected redirect was not followed. Counts toward the five-failure pause |

## Configuration

`.env.example` keys: `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL_ID` (`eleven_multilingual_sts_v2`), `ELEVENLABS_API_BASE` (`https://api.elevenlabs.io`), `VCM_INPUT_DEVICE`, `VCM_OUTPUT_DEVICE`.

Model id defaults to `eleven_multilingual_sts_v2` when unset. The base URL default is `https://api.elevenlabs.io`.

## Packaging and runtime

- Language: Python 3.11 or 3.12. `requires-python` is `>=3.11,<3.13` so the dependency pins stay on releases that publish Intel macOS wheels.
- Runtime dependencies: `httpx`, `sounddevice`, `numpy`, `python-dotenv`.
- `numpy` is pinned to the 1.26 line because those wheels include `macosx_10_9_x86_64`.
- `sounddevice` is imported only when a live device list or live stream is requested. `demo`, unit tests, and `--fake` do not import it.
- Tests are `pytest` and use fake capture, fake playback, and `httpx.MockTransport`. They do not need a microphone, a virtual cable, a network connection, or an API key.
- GitHub Actions runs the same suite on `ubuntu-latest`, `windows-latest`, and `macos-latest` for Python 3.11 and 3.12. `macos-latest` hosted runners are not Intel Ventura machines. A green run there is not Intel hardware validation.

## Testing contracts

The suite must cover, without hardware or network:

- Segmenter: silence emits nothing; short bursts are dropped; pre-roll and hangover are included; forced cuts concatenate without overlap or gaps; flush and reset behave as specified; repeated input is byte-identical.
- PCM assembler: odd leading bytes, fragmented chunks, and a dropped trailing byte on flush.
- Resampler: chunked input plus `flush` equals one-shot plus `flush` for 24 kHz→48 kHz, 48 kHz→16 kHz, and 24 kHz→44.1 kHz; state does not leak across `reset`.
- Device selection: default physical input, refusal of default virtual input, required virtual output, ambiguous names, speaker rejection, VB-CABLE playback-side versus capture-side, BlackHole self-capture rejection, and `--allow-any-output` still rejecting feedback.
- Ordering: out-of-order completion, timeout close, late chunk drop, hold overflow, generation isolation.
- Playback: underrun silence, duration cap, stale generation rejected, clear empties the buffer.
- Pipeline: mock audio matches the XOR reference and differs from raw resampled mic audio; each segment is posted once; overflow drops unsent audio; mute/stop cancel a blocked conversion and discard its late audio; 401 stays muted across unmute; 429 pauses; consecutive failures pause.
- HTTP client against a mock transport: path, query, multipart fields, absence of `optimize_streaming_latency`, no redirect following, and sanitized 401/403/422/429/5xx bodies that contained the key.
- CLI: `--help`, `demo` exit 0 with `passthrough=false` and `mock=true`, config redaction, `run` without the billing flag sends nothing, `.env.example` has no secret, gitignore covers `.env` and `.vcm/`.
- Import check: importing the CLI module does not import `sounddevice`.

## Honest limitations

- Delay is the sum of hangover or forced-cut buffering, queueing, upload, conversion, download, resampling, and device period. No numeric latency target is claimed.
- The resampler is linear interpolation with carried phase. It is deterministic and stateful. It is not a production-grade band-limited converter.
- Virtual-cable detection is a name heuristic.
- Intel Ventura 13.7.8 is the intended macOS floor. This design does not record a test on that machine.
- Mock tests do not show that ElevenLabs accepted a voice id, that BlackHole or VB-CABLE is installed, or that a meeting app can hear the cable.

## Out of scope

Kernel drivers, installing BlackHole or VB-CABLE, creating the OS device, Voice.ai, voice training or upload, zero-retention enterprise mode, measured latency promises, STT/TTS, persistent recordings, and a graphical mixer.
