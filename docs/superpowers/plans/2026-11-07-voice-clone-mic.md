# Voice-clone mic implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Python CLI that segments microphone audio, converts each segment with ElevenLabs Voice Changer streaming, and plays only converted audio (or silence) into a user-selected BlackHole or VB-CABLE device.

**Architecture:** Pure, injectable core (segmenter, resampler, PCM assembler, device selection, sequence gate, playback buffer, HTTP client) with a session orchestrator. PortAudio is behind `AudioHost` and is not imported by tests. `demo` and `--mock` never open a socket.

**Tech Stack:** Python 3.11–3.12, pytest, httpx, sounddevice, numpy 1.26, python-dotenv.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-11-07-voice-clone-mic-design.md`. Behavior in this plan that disagrees with the spec is a bug in the plan; the spec wins.
- Python `>=3.11,<3.13`. Package layout `src/voice_clone_mic`.
- Endpoint `POST /v1/voice-changer/{voice_id}/stream` with `output_format=pcm_24000`, multipart `file_format=pcm_s16le_16`, `model_id` default `eleven_multilingual_sts_v2`. Do not send `optimize_streaming_latency`.
- Decode `pcm_24000` as signed 16-bit little-endian mono at 24000 Hz. Refuse to process audio when `sys.byteorder != "little"`.
- No raw-mic write path. No automatic retry. No audio files. No API-key logging.
- Non-mock network requires `--i-accept-api-billing`. Tests, `demo`, and `--mock` do not call ElevenLabs.
- Do not install drivers, read or print secrets, or claim hardware/API validation from mocks.
- Bounds: capture 2.0 s, unsent 8.0 s, in flight 2, hold 12.0 s, playback 3.0 s, segment 4.0 s, request hard cap 5.0 s (`160_000` bytes).
- Frame 20 ms, RMS threshold 500, pre-roll 200 ms, hangover 400 ms, minimum speech 200 ms.
- Five consecutive conversion failures (422, 5xx, timeout, disconnect, 3xx) pause until `unmute`. 401/403 are fatal. 429 pauses immediately. Success resets the counter.
- Output is never implicit. Feedback loop keys refuse to start even with `--allow-any-output`.

---

### Task 1: Package metadata and ignore rules

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/voice_clone_mic/__init__.py`
- Create: `src/voice_clone_mic/__main__.py`
- Test: `tests/test_hygiene.py`

**Interfaces:**
- Produces: installable package `voice-clone-mic`, version `0.1.0`, `requires-python >=3.11,<3.13`.
- Dependencies: `httpx>=0.27,<0.29`, `sounddevice>=0.5.1,<0.6`, `numpy>=1.26.4,<2`, `python-dotenv>=1.0,<2`.
- Dev dependency: `pytest>=8.2,<9`.
- Script entry `voice-clone-mic = voice_clone_mic.cli:main` is added in the CLI task; Task 1's `__main__` calls `cli.main` and can fail import until Task 10. Hygiene tests in this task must not import `cli`.

- [ ] **Step 1: Write the failing hygiene test**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_env_example_has_empty_secrets_only():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ELEVENLABS_API_KEY=" in text
    assert "sk_" not in text
    for line in text.splitlines():
        if line.startswith("ELEVENLABS_API_KEY="):
            assert line.strip() == "ELEVENLABS_API_KEY="

def test_gitignore_covers_secrets_and_audio():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for token in (".env", ".vcm/", "*.wav", "*.pcm", "*.mp3"):
        assert token in text
```

- [ ] **Step 2: Run `pytest tests/test_hygiene.py -q` and confirm failure because the files are missing**

- [ ] **Step 3: Add the files**

`.env.example` contains only comments plus:

```
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
ELEVENLABS_MODEL_ID=eleven_multilingual_sts_v2
ELEVENLABS_API_BASE=https://api.elevenlabs.io
VCM_INPUT_DEVICE=
VCM_OUTPUT_DEVICE=
```

`.gitignore` contains `.env`, `.venv/`, `venv/`, `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `*.egg-info/`, `dist/`, `build/`, `.vcm/`, `*.wav`, `*.pcm`, `*.mp3`, `recordings/`.

`pyproject.toml` uses setuptools with `package-dir` `"" = "src"` and the pins above. `__init__.py` sets `__version__ = "0.1.0"`.

- [ ] **Step 4: `pytest tests/test_hygiene.py -q` passes**

- [ ] **Step 5: Commit `chore: add package metadata and secret ignore rules`**

### Task 2: PCM assembler

**Files:**
- Create: `src/voice_clone_mic/pcm.py`
- Test: `tests/test_pcm.py`

**Interfaces:**
- Produces: `clip_i16(value: int) -> int`
- Produces: `samples_to_bytes(samples: list[int]) -> bytes` little-endian int16
- Produces: `bytes_to_samples(data: bytes) -> list[int]` requires even length, else `ValueError`
- Produces: `class PcmFrameAssembler` with `feed(data: bytes) -> list[int]` and `flush() -> bool` (`True` when a dangling byte was dropped)

- [ ] **Step 1: Failing tests**

Cover: one odd byte then the rest reassembles `0x0201` as sample `0x0201`; chunks of 1, 2, 3, and 4 bytes; flush drops a trailing odd byte and the next `feed` starts clean; `bytes_to_samples` rejects odd input; `clip_i16` maps `-40000` to `-32768` and `40000` to `32767`.

- [ ] **Step 2: `pytest tests/test_pcm.py -q` fails on import**

- [ ] **Step 3: Implement the assembler with one stored leftover byte. Interpret each completed pair as signed little-endian.**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit `feat: assemble fragmented little-endian PCM frames`**

### Task 3: Streaming resampler

**Files:**
- Create: `src/voice_clone_mic/resample.py`
- Test: `tests/test_resample.py`

**Interfaces:**
- Produces: `class StreamResampler` with `__init__(source_rate: int, target_rate: int)`, `process(samples: list[int]) -> list[int]`, `flush() -> list[int]`, `reset() -> None`
- `process` keeps one-sample lookahead. `flush` emits through the last source sample using the last sample as the missing neighbor, then clears state.
- Step is `source_rate / target_rate` source samples per output sample. Output is `clip_i16(round(linear mix))`.
- Rates below 1 raise `ValueError`.

- [ ] **Step 1: Failing tests**

```python
def test_chunked_matches_oneshot_for_three_rates():
    source = [0, 1000, -1000, 32767, -32768] * 50
    for src, dst in ((24000, 48000), (48000, 16000), (24000, 44100)):
        whole = StreamResampler(src, dst)
        once = whole.process(source) + whole.flush()
        part = StreamResampler(src, dst)
        got = []
        for offset in range(0, len(source), 7):
            got.extend(part.process(source[offset:offset + 7]))
        got.extend(part.flush())
        assert got == once
        assert abs(len(once) - round(len(source) * dst / src)) <= 1
```

Also assert a constant input stays constant, `reset` then a new buffer does not prepend old samples, and rate `0` raises.

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement the phase buffer described above. Drop consumed samples with `int(pos)` and subtract that integer from `pos`.**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit `feat: add stateful streaming resampler`**

### Task 4: Speech segmenter

**Files:**
- Create: `src/voice_clone_mic/segmenter.py`
- Test: `tests/test_segmenter.py`

**Interfaces:**
- Produces: `@dataclass Segment` fields `sequence: int`, `generation: int`, `pcm: bytes`, `duration_ms: int`
- Produces: `class SpeechSegmenter` constructor keyword arguments `sample_rate=16000`, `frame_ms=20`, `threshold=500.0`, `pre_roll_ms=200`, `hangover_ms=400`, `min_speech_ms=200`, `max_segment_ms=4000`, `generation=0`
- Methods: `push(samples: list[int]) -> list[Segment]`, `flush() -> list[Segment]`, `reset() -> None`
- Constructor raises `ValueError` unless every millisecond setting is a positive multiple of `frame_ms`, except `pre_roll_ms` which may be `0`.
- RMS is `sqrt(mean(sample^2))` over the frame. Speech is `rms >= threshold`.
- Forced cuts do not reuse pre-roll. A forced cut with at least one loud frame is emitted, and later pieces of that same phrase are emitted even if they are shorter than the minimum. A phrase that ends without a forced cut is emitted only when its loud frames meet the minimum. `reset` drops an open phrase, clears that exemption, and sets the next sequence to `0`. `flush` uses the same close rule and drops a trailing short frame.

- [ ] **Step 1: Failing tests using 16 kHz, frame 20 ms, pre-roll 0 or 200, hangover 400, min speech 200, max 200 or 4000 as each test needs**

Cases:

- Silence of any length returns no segments.
- 160 ms of amplitude 5000 (below 200 ms) plus hangover returns no segments.
- 200 ms of leading zeros, 200 ms of amplitude 5000, 400 ms of zeros produces one segment whose samples are exactly that 800 ms.
- Continuous amplitude 5000 for 500 ms with `max_segment_ms=200` and `pre_roll_ms=0` returns segments whose concatenated samples equal the input, with no overlap.
- The same input pushed twice yields equal `pcm` bytes and sequence numbers starting at 0 after `reset`.
- `push` of a partial frame emits nothing; `reset` then `flush` emits nothing.
- `duration_ms` equals `len(samples) * 1000 / sample_rate`.

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement the idle/speaking state machine from the spec. Store frames as copies. Emit little-endian PCM via `samples_to_bytes`.**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit `feat: segment speech with pre-roll and forced cuts`**

### Task 5: Device selection

**Files:**
- Create: `src/voice_clone_mic/devices.py`
- Test: `tests/test_devices.py`

**Interfaces:**
- `@dataclass(frozen=True) DeviceInfo`: `index: int`, `name: str`, `max_input_channels: int`, `max_output_channels: int`, `default_samplerate: float`, `default_input: bool`, `default_output: bool`
- `loop_key(name: str) -> str | None`
- `select_devices(devices: list[DeviceInfo], input_query: str | None, output_query: str | None, allow_any_output: bool) -> tuple[DeviceInfo, DeviceInfo]`
- `plan_stream(device: DeviceInfo, direction: str) -> tuple[int, int]` returns `(sample_rate, channels)`
- `class DeviceSelectionError(ValueError)`
- Fixture helper `fake_catalog() -> list[DeviceInfo]` with Built-in Microphone (index 0, default input, 48000, 1 in), Speakers (index 1, default output, 44100, 2 out), BlackHole 2ch (index 2, 2 in, 2 out, 48000), `CABLE Input (VB-Audio Virtual Cable)` (index 3, 0 in, 8 out, 48000), `CABLE Output (VB-Audio Virtual Cable)` (index 4, 8 in, 0 out, 48000)

Selection rules are the spec rules. `plan_stream` refuses rates outside 8000–192000. Input channels are 1. Output channels are 1 when `max_output_channels == 1`, else 2. Direction `"input"` requires an input channel; `"output"` requires an output channel.

- [ ] **Step 1: Failing tests for every selection case named in the spec, plus `plan_stream` rate rejection and channel choice**

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement name matching and loop keys. BlackHole keys are `bh:` plus the squashed lowercased name. VB keys strip `input`, `output`, `vb-audio`, `virtual`, and `cable`, then prefix `vb:`.**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit `feat: select virtual cable endpoints without feedback`**

### Task 6: Playback buffer

**Files:**
- Create: `src/voice_clone_mic/playback.py`
- Test: `tests/test_playback.py`

**Interfaces:**
- `class PlaybackBuffer` `__init__(self, sample_rate: int, channels: int, max_ms: int = 3000)`
- `write(self, generation: int, mono: list[int]) -> bool`
- `read_mono(self, frames: int) -> list[int]` always returns `frames` samples, padding zeros, and increments `underruns` when any pad was required and `frames > 0`
- `clear_and_bump(self) -> int` returns the new generation
- `queued_ms(self) -> int`
- Attributes: `generation` starts at 1, `underruns`, `dropped_writes`
- A write with the wrong generation returns `False` and does not change queued audio.
- A write that would exceed `max_ms` returns `False`, increments `dropped_writes`, and leaves the previous audio in place.
- `read_mono` and `write` share one `threading.Lock`.

- [ ] **Step 1: Failing tests for silence underrun, stereo caller still reading mono, stale generation, cap, and clear**

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement with `array.array("h")`**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit `feat: bound playback to converted audio or silence`**

### Task 7: Sequence gate

**Files:**
- Create: `src/voice_clone_mic/ordering.py`
- Test: `tests/test_ordering.py`

**Interfaces:**
- `@dataclass Released`: `sequence: int`, `samples: list[int]`, `failed: bool`
- `class SequenceGate` `__init__(self, max_hold_samples: int)`
- `reset(self, generation: int) -> None` sets `next_seq = 0` and drops buffers
- `push(self, generation: int, sequence: int, samples: list[int], finished: bool) -> list[Released]`
- `fail(self, generation: int, sequence: int) -> list[Released]`
- Head sequence releases samples immediately. Later sequences are held until they become head. `fail` discards buffered samples for that sequence. A push after close or with the wrong generation returns `[]`.
- When held samples exceed `max_hold_samples`, mark the highest sequence above `next_seq` failed and discard its samples. If only the head exists, do not buffer it here; the playback cap handles the head.

- [ ] **Step 1: Failing tests**

- Sequence 1 finishing before sequence 0 stays unreleased; after sequence 0 finishes, playback order is 0 then 1.
- `fail` on sequence 0 then a late `push` returns nothing, and a finished sequence 1 is released next.
- Hold limit: while sequence 0 is unfinished, a large sequence 2 is dropped and a small sequence 1 is kept. After sequence 0 finishes, the releases are sequence 0's samples, sequence 1's samples, then a failed empty sequence 2.
- `reset` ignores a later push with the old generation.

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement the gate. Keep the lock inside the session, not the gate. The gate itself is single-threaded.**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit `feat: keep converted audio in segment order`**

### Task 8: Errors and HTTP client

**Files:**
- Create: `src/voice_clone_mic/errors.py`
- Create: `src/voice_clone_mic/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- `class ConversionError(Exception)` with `code: str`, `fatal: bool`, `pause: bool`
- `sanitize(text: str, secret: str | None) -> str`
- `error_from_status(status: int, body: bytes, secret: str | None) -> ConversionError`
- `validate_api_base(base: str) -> str` returns the base without a trailing slash or raises `ConversionError` code `config`
- `validate_request_pcm(pcm: bytes) -> None` rejects empty, odd, or `> 160_000` bytes with code `rejected` and does not set fatal
- `class HttpxVoiceClient` `__init__(self, api_key: str, base_url: str, model_id: str, transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 20.0)`
- `async stream(self, voice_id: str, pcm: bytes, cancel: asyncio.Event) -> AsyncIterator[bytes]`

`stream` posts multipart `audio` filename `segment.pcm`, content type `application/octet-stream`, fields `file_format=pcm_s16le_16` and `model_id`, query `output_format=pcm_24000`, header `xi-api-key`, `Accept: audio/pcm`, `follow_redirects=False`. It yields response bytes. Between chunks it checks `cancel` and raises `asyncio.CancelledError`. Non-200 responses read at most 2048 body bytes and raise `error_from_status`. Transport exceptions become `ConversionError` code `disconnected`.

`sanitize` replaces the secret, strips `xi-api-key` assignments, and caps at 180 characters. Non-UTF-8 bodies contribute no detail. JSON `detail` that is a list of objects uses their `msg` values joined by `; `.

- [ ] **Step 1: Failing tests using `httpx.MockTransport`**

Assert the URL path, query, absence of `optimize_streaming_latency` in the URL and body, presence of `pcm_s16le_16` and the model id, odd chunks reassembled by the test's reader, 401 fatal message without the key, 403 fatal, 422 not fatal, 429 `pause=True`, 500 not fatal, 302 not followed, and `validate_api_base("http://example.com")` plus `https://evil.elevenlabs.io.attacker.test` rejected while `https://api.eu.residency.elevenlabs.io/` is accepted.

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement the client. Do not log the request or response body.**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit `feat: call the voice-changer stream without deprecated params`**

### Task 9: Session orchestration

**Files:**
- Create: `src/voice_clone_mic/mock_client.py`
- Create: `src/voice_clone_mic/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- `class MockVoiceClient` `async stream(self, voice_id: str, pcm: bytes, cancel: asyncio.Event)` XOR each input byte with `0xFF` and yield chunks of 1 byte, then 3 bytes, then the rest. Record `payloads: list[bytes]`. Optional `delay_s: list[float]` sleeps before each call's yield, indexed by call count.
- `class Session` constructor takes `voice_id: str`, `client: object`, `input_device: DeviceInfo`, `output_device: DeviceInfo`, `clock: Callable[[], float]`, optional segmenter and timeout. It builds capture and playback resample streams from `plan_stream`.
- `async feed(self, device_pcm: bytes) -> None` resamples to 16 kHz, segments, queues.
- `async wait_idle(self) -> None`
- `async mute(self) -> None`, `async unmute(self) -> str`, `async stop(self) -> None`
- `status(self) -> dict[str, object]`
- `on_device_fault(self, message: str) -> None`
- Playback reference rate is the output plan rate. Mock bytes are treated as 24 kHz PCM after assembly.

Session rules:

- Assign sequence numbers from the segmenter. Dropped unsent segments call `gate.fail` immediately.
- Submit a segment at most once. Keep at most two conversion tasks.
- Each task owns a `PcmFrameAssembler` and a `StreamResampler(24000, output_rate)`.
- First converted sample for a sequence sets `observed_last_segment_ms` from `clock() - ready`.
- `ready` is stored when the segment is created, using `clock`.
- Mute, stop, and device fault bump the playback generation, clear playback, reset the gate to that generation, cancel tasks, clear unsent audio, and `segmenter.reset()` plus capture-resampler `reset()`. They do not flush the open phrase into the client.
- `unmute` returns `"staying muted: authentication failed"` when fatal, otherwise clears user mute and the 429/consecutive pause.
- A `ConversionError` with `fatal` sets the latch. `pause` pauses. Other conversion errors increment the consecutive counter.
- `status()["passthrough"]` is always `False`.
- `status()["mock"]` is true when the client is a `MockVoiceClient`.

- [ ] **Step 1: Failing tests**

1. Constant-loud 48 kHz phrase through `feed` + `wait_idle` matches a reference computed with `StreamResampler`, the segmenter, XOR, `PcmFrameAssembler`, and the 24 kHz→48 kHz resampler. The played mono audio is not the raw 48 kHz input.
2. Two forced-cut segments with `delay_s=[0.05, 0.0]` play in sequence order. `len(client.payloads)` equals the number of emitted segments.
3. A client that blocks on `asyncio.Event` is muted; after the event is set, `read_mono` over the played buffer is still zeros and a direct `write` of the old generation returns `False`.
4. Client raising fatal 401 prevents a second `feed` from appending another payload; `unmute` does not clear it.
5. Five `ConversionError("500", ...)` pauses the sixth submission.
6. `429` pauses on the first one.
7. Capture longer than the unsent budget drops payloads so `len(payloads)` is below the number of produced segments, and `status()["dropped_segments"] > 0`. Use a tiny unsent budget via a constructor argument `max_unsent_ms`.
8. `on_device_fault` clears a seeded playback write.

Add constructor arguments `max_unsent_ms: int = 8000`, `max_in_flight: int = 2`, `request_timeout_s: float = 20.0`, `failure_pause_after: int = 5` so tests can shrink them. The defaults stay the spec values.

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement session and mock client. Conversion tasks check generation before every playback write. Timeout uses `asyncio.timeout` and then `gate.fail` for that sequence if it is still open.**

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit `feat: orchestrate segmented conversion without passthrough`**

### Task 10: CLI, fake host, and live host shell

**Files:**
- Create: `src/voice_clone_mic/config.py`
- Create: `src/voice_clone_mic/audio_host.py`
- Create: `src/voice_clone_mic/cli.py`
- Modify: `src/voice_clone_mic/__main__.py`
- Modify: `pyproject.toml` (add the `voice-clone-mic` script entry)
- Test: `tests/test_cli.py`
- Test: `tests/test_config.py`

**Interfaces:**
- `load_settings(environ: dict[str, str], cwd: Path, dotenv_file: Path | None) -> Settings`
- `Settings` fields: `api_key: str | None`, `voice_id: str | None`, `model_id: str`, `api_base: str`, `input_device: str | None`, `output_device: str | None`, `ignored_file_key: bool`
- `save_voice_id(cwd: Path, voice_id: str) -> None` writes `.vcm/config.json` with only `{"voice_id": ...}`
- `AudioHost` protocol: `list_devices()`, `open_input(...)`, `open_output(...)`, `close()`
- `FakeHost.catalog()` returns `fake_catalog()`
- `SoundDeviceHost.list_devices()` imports sounddevice inside the method
- `main(argv: list[str] | None = None) -> int`

CLI behavior matches the spec command table. `demo` builds a 48 kHz phrase (200 ms silence, 300 ms amplitude 8000, 500 ms silence), runs a `Session` into a `PlaybackBuffer`, prints status lines including `mock=true` and `passthrough=false`, and returns 0 only if some non-zero converted sample was written. `run` without `--mock` and without `--i-accept-api-billing` returns 2 and does not construct `HttpxVoiceClient`. `voices` without the flag returns 2 and does not construct it either. `config` prints `api_key=set` or `api_key=missing` and never the key value. `run --mock --fake --once` returns 0 without sounddevice.

`SoundDeviceHost` requests int16 streams, copies input channel 0 into a bounded 2.0 s buffer, and the output callback calls `read_mono` then duplicates channels. Stream errors call `session.on_device_fault`. This host is not used by pytest.

- [ ] **Step 1: Failing CLI and config tests**

Use `main([...])`. Monkeypatch `httpx.AsyncClient` to raise `AssertionError` if constructed during `demo`, `run --mock --fake --once`, and `voices` without the billing flag. Assert help text contains `BlackHole`, `VB-CABLE`, and `phrase`. Assert `use-voice` rejects `../x` and accepts `JBFqnCBsd6RMkjVDRZzb`. Assert a config file containing `api_key` sets `ignored_file_key` and does not become `Settings.api_key`.

- [ ] **Step 2: Confirm failure**

- [ ] **Step 3: Implement config, CLI, fake host, and the sounddevice host. Load `.env` with `python-dotenv` only when the file exists, without overriding existing environ values.**

- [ ] **Step 4: `pytest tests/test_cli.py tests/test_config.py -q` passes. `import sounddevice` is absent from `sys.modules` after importing `voice_clone_mic.cli` in a clean check (subprocess).**

- [ ] **Step 5: Commit `feat: add voice-clone-mic CLI and offline demo`**

### Task 11: README and CI

**Files:**
- Modify: `README.md`
- Create: `.github/workflows/tests.yml`

**Interfaces:**
- Workflow name `tests`, on push and pull_request, matrix `os: [ubuntu-latest, windows-latest, macos-latest]` and `python-version: ["3.11", "3.12"]`, steps checkout, setup-python, `python -m pip install -e ".[dev]"`, `pytest -q`.
- README states phrase buffering plus network delay, Intel Ventura intent versus no run on that machine in this change, cable direction, install for macOS and Windows, `.env` without a real secret, `demo` / `--mock` commands, the billing flag, privacy (audio leaves the laptop only on a live run; default API logging is not zero-retention), and the exact local test command `pytest -q`.

- [ ] **Step 1: Add both files**

- [ ] **Step 2: `pytest -q` from a clean environment passes locally**

- [ ] **Step 3: Commit `docs: describe install, privacy, and unverified hardware`**

### Task 12: Review

- [ ] Re-read the spec against `session.py`, `client.py`, and `cli.py`.
- [ ] Run `pytest -q` and record the output.
- [ ] Dispatch an independent review of the branch diff. Fix critical and important findings. Re-run `pytest -q`.
- [ ] Do not write that GitHub Actions passed unless a completed run was observed.

## Self-review notes

Spec coverage is mapped as follows: transport Task 8; segmentation Task 4; resampler and PCM Tasks 2–3; bounds and ordering Tasks 6–7 and 9; device rules Task 5; CLI, secrets, and mock Task 10; CI and honest README Task 11. No `TBD` or deferred error handling remains in the tasks above. Signatures used by later tasks (`Segment`, `StreamResampler`, `PcmFrameAssembler`, `select_devices`, `PlaybackBuffer`, `SequenceGate`, `ConversionError`, `HttpxVoiceClient.stream`, `MockVoiceClient`, `Session`) are defined in the task that produces them. This session executes the plan inline because the user already asked for the implemented PR, not a choice between execution modes.
