# Push-to-talk Whisper TTS implementation plan

> **For agentic workers:** Implement this plan on the existing `cursor/voice-clone-mic-mvp-c335` branch. Do not merge. Do not replace speech-to-speech or RVC commands.

**Goal:** Add an optional `ptt` CLI that records one bounded utterance, transcribes it locally, and plays one ElevenLabs TTS stream into an explicit virtual cable.

**Architecture:** Fake transcriber and mock TTS are the default test path. Official `openai-whisper` is an optional extra imported only by `prepare-model` and a live `run`. Device selection, resampling, PCM assembly, and playback are the existing modules.

**Tech Stack:** Existing Python 3.11/3.12 package. Optional extra pins `openai-whisper==20250625`, `torch==2.2.2`, `numba==0.61.2`, `llvmlite==0.44.0`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-11-08-ptt-whisper-tts-design.md`.
- No live ElevenLabs call, no model download, and no microphone open in tests.
- TTS request is `POST /v1/text-to-speech/{voice_id}/stream?output_format=pcm_24000` with JSON `text` and `model_id`. Do not send `optimize_streaming_latency`.
- Default TTS model `eleven_flash_v2_5`. STS model env stays `ELEVENLABS_MODEL_ID`.
- One Whisper call and at most one TTS call per accepted utterance.
- Quiet, empty, no-speech, and low-confidence transcripts do not call TTS.
- Generation bump discards late STT and TTS results.
- Existing tests stay green.

---

### Task 1: Gate, capture cap, and TTS client

**Files:**
- Create: `src/voice_clone_mic/ptt/gate.py`
- Create: `src/voice_clone_mic/ptt/capture.py`
- Create: `src/voice_clone_mic/ptt/tts.py`
- Test: `tests/test_ptt_gate.py`
- Test: `tests/test_ptt_tts.py`

**Interfaces:**
- `assess_transcript(text, segments, rms) -> Decision` with `speak: bool` and `reason`
- `BoundedCapture.extend` drops samples past `max_samples`
- `HttpxTtsClient.stream(voice_id, text, cancel)` yields bytes
- `MockTtsClient` records texts and yields odd-sized PCM derived from the text

- [ ] Failing tests for silence, no-speech, low logprob, high compression, empty text, capture cap, TTS URL/body, and redacted 401
- [ ] Implement
- [ ] `pytest tests/test_ptt_gate.py tests/test_ptt_tts.py -q`

### Task 2: Utterance session

**Files:**
- Create: `src/voice_clone_mic/ptt/session.py`
- Test: `tests/test_ptt_session.py`

**Interfaces:**
- `PttSession.submit(device_pcm) -> UtteranceResult`
- `cancel()` bumps playback generation and clears audio
- Transcriber is any object with `transcribe(samples_16k) -> Transcript`
- Quiet input does not call the transcriber
- A transcriber that returns after `cancel()` does not call TTS
- 401 sets `fatal` and a later submit does not call TTS

- [ ] Failing session tests
- [ ] Implement using `StreamResampler`, `PcmFrameAssembler`, and `PlaybackBuffer`
- [ ] `pytest tests/test_ptt_session.py -q`

### Task 3: CLI, config, and model prep guard

**Files:**
- Create: `src/voice_clone_mic/ptt/whisper_backend.py`
- Create: `src/voice_clone_mic/ptt/cli.py`
- Modify: `src/voice_clone_mic/cli.py`
- Modify: `src/voice_clone_mic/config.py`
- Modify: `.env.example`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Test: `tests/test_ptt_cli.py`

**Interfaces:**
- `voice-clone-mic ptt demo` offline, exit 0, `passthrough=false`
- `ptt run` without `--i-accept-api-billing` sends nothing
- `ptt run --mock --fake --once --output BlackHole` exit 0
- `ptt prepare-model` does not import `whisper` until invoked, and without the extra installed it exits 2 without downloading
- Importing `voice_clone_mic.ptt.session` does not import `whisper`

- [ ] Failing CLI tests
- [ ] Implement
- [ ] `pytest -q` including the previous 69 tests

### Task 4: Review

- [ ] Re-read the spec against `ptt/session.py` and `ptt/tts.py`
- [ ] Fix findings
- [ ] Do not claim a Ventura run, a model download, or a latency number
