# Push-to-talk local Whisper and ElevenLabs TTS

Date: 2026-11-08
Status: approved by the user's "lets go" after the local-STT recommendation. Credits were mentioned as existing. That is not permission to make a live or billable call from this change. This mode is optional and does not replace speech-to-speech or the RVC tools.

## Problem

Hold a physical microphone, transcribe the utterance on the laptop, speak that text in the user's existing ElevenLabs cloned voice, and play the result into a virtual cable. The speech-to-speech mode stays available. This mode is a different product: speech-to-text, then text-to-speech. Prosody is regenerated. Delay is local transcription time plus the TTS request. No numeric end-to-end latency is promised, and ElevenLabs flash marketing numbers are not an end-to-end claim.

Targets:

- macOS Ventura 13.7.8, Intel Core i5, Iris Plus, x86_64. No macOS 14 requirement and no Apple Silicon requirement.
- Windows 11, AMD Ryzen AI 7 350, Radeon 860M. No CUDA requirement on either machine.

## Backend choice

PyPI was checked on 2026-11-08.

- Current `torch` 2.15 publishes macOS wheels only as `macosx_14_0_arm64`. It does not provide an Intel wheel.
- `ctranslate2` 4.8.5 does publish `macosx_11_0_x86_64` wheels for CPython 3.11 and 3.12, and `win_amd64` for 3.12. `faster-whisper` 1.2.1 depends on `onnxruntime`. Current `onnxruntime` 1.31 has no Intel macOS wheel. The last Intel macOS `onnxruntime` wheels found, 1.16.3, stop at CPython 3.11. That blocks `faster-whisper` on the project's Python 3.12 Intel target.
- Official `openai-whisper` 20250625 is pure Python. Its CPU stack has Intel wheels: `torch` 2.2.2 (`macosx_10_9_x86_64` and `win_amd64`, CPython 3.11 and 3.12), `numba` 0.61.2 (`macosx_10_14_x86_64` and `win_amd64`), `llvmlite` 0.44.0 (same), and `tiktoken` 0.14.0 (macOS x86_64 and Windows). `macosx_10_9` and `macosx_10_14` wheels can run on Ventura 13. They do not require macOS 14.

`openai-whisper` 20250625 loads that checkpoint with `torch.load(..., weights_only=True)` when torch is at least 1.13. The pinned torch 2.2.2 meets that. The optional extra therefore pins that official stack and runs Whisper on CPU. `faster-whisper` is not the backend. The extra is not installed by importing the app, by the default `pip install -e .`, or into the RVC virtualenv. Those pins were read from PyPI filenames. They were not installed or executed on Intel Ventura or on the Windows machine in this change.

The default model name is `small.en`. The file is OpenAI's published checkpoint `small.en.pt`, SHA256 `f953ad0fd29cacd07d5a9eda5624af0f6bcf2258be67c92b79389873d91e0872`, from the URL embedded in `openai-whisper` 20250625. `prepare-model` is the only command that may download it. Transcription refuses to call `whisper.load_model` unless that file is already present and the hash matches, because a mismatched file makes upstream re-download. No customer audio is sent to OpenAI. Local Whisper is not an ElevenLabs credit charge.

## TTS transport

Checked `https://elevenlabs.io/docs/api-reference/text-to-speech/stream` on 2026-11-08.

- `POST /v1/text-to-speech/{voice_id}/stream`
- JSON body with `text` and `model_id`
- Query `output_format=pcm_24000`
- Header `xi-api-key`
- `optimize_streaming_latency` is deprecated and is not sent
- `enable_logging` is left unset, so the API default remains true. This is not zero-retention
- `language_code=en` is sent only when the configured model id does not contain `multilingual_v2`, because the docs say that parameter is not supported for those models
- Redirects are not followed

`pcm_24000` is decoded as signed 16-bit little-endian mono at 24 kHz, then resampled to the selected output device rate. Odd response bytes are buffered until a full sample exists. A trailing odd byte at end of stream is dropped.

The speech-to-speech model remains `ELEVENLABS_MODEL_ID` (default `eleven_multilingual_sts_v2`). Push-to-talk uses `ELEVENLABS_TTS_MODEL` (default `eleven_flash_v2_5`) and `ELEVENLABS_TTS_VOICE_ID` when set, otherwise `ELEVENLABS_VOICE_ID`. The flash default is a configuration choice for a smaller TTS model. It is not a measured latency promise.

## Flow

```
Enter starts capture of the selected physical mic
Enter stops it, or the duration cap stops it
-> resample device rate to 16 kHz mono
-> if the utterance is quiet, stop with silence
-> one local Whisper call on the finalized buffer
-> if the transcript is empty, no-speech, or low-confidence, stop with silence
-> optional on-screen preview, edit, or cancel
-> one TTS stream
-> resample 24 kHz to the virtual-cable rate
-> playback of converted audio, or silence
```

There is no streaming partial transcript and therefore no second TTS call for the same utterance. Push-to-talk does not install a global hotkey. The first control is Enter to start and Enter to stop. `ptt demo` and `ptt run --mock --fake --once` do not read the keyboard.

Capture is bounded to 20 seconds by default. The callback, when a live device is used, only appends samples under that cap. The fake and mock commands do not open a device or a socket.

Output selection reuses the existing virtual-cable rules: explicit output, BlackHole or VB-CABLE heuristic, no speaker fallback, and the same-cable feedback refusal. `--allow-any-output` still refuses feedback.

## Fail closed

Quiet audio is not transcribed and not spoken. After transcription, speech is skipped when any of these hold:

- stripped text is empty
- there is no segment
- every segment has `no_speech_prob` greater than or equal to 0.6
- any used segment has `avg_logprob` below -1.0
- any used segment has `compression_ratio` above 2.4

Those thresholds are gates against silence hallucinations such as a short polite phrase. They are not a claim that Whisper's text is correct.

STT exceptions speak nothing and do not retry that utterance. HTTP 401 and 403 mute further TTS for the process. 429 pauses further TTS until a new process. 422, 5xx, timeout, and disconnect fail that utterance only, with no automatic retry. Playback is cleared when a TTS attempt fails so a partial response does not remain queued.

Cancel, mute, and stop increment the playback generation and clear queued audio. A transcription that finishes afterward cannot call TTS. A TTS chunk that arrives afterward cannot write audio. The live output callback is unchanged: it reads the playback buffer, which contains only accepted TTS samples or zeros.

Text sent to TTS is capped at 500 characters. Longer text is refused for that utterance and is not truncated into a billed request.

## Privacy

Raw samples and the transcript stay in process memory. They are not written to disk and are not logged. The terminal may print a preview because the user is reading it. Live mode sends the accepted transcript to ElevenLabs only after `--i-accept-api-billing`. Demo and `--mock` do not construct an HTTP client. The API key is never printed.

## What this change does not claim

- No Intel Ventura or Windows recording was run.
- No Whisper model file was downloaded in this change.
- No transcript accuracy or audio latency number is measured.
- The training UI and speech-to-speech mode are separate.
