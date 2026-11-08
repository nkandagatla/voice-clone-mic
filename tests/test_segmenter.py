from voice_clone_mic.pcm import bytes_to_samples
from voice_clone_mic.segmenter import SpeechSegmenter


def _audio(segmenter, loud_frames, silence_frames, amplitude=5000):
    frame = segmenter.frame_len
    return [0] * (silence_frames * frame) + [amplitude] * (loud_frames * frame)


def test_silence_emits_nothing():
    segmenter = SpeechSegmenter()
    assert segmenter.push([0] * 16000) == []
    assert segmenter.flush() == []


def test_short_burst_is_dropped():
    segmenter = SpeechSegmenter(pre_roll_ms=0)
    frame = segmenter.frame_len
    audio = [5000] * (8 * frame) + [0] * (20 * frame)
    assert segmenter.push(audio) == []
    assert segmenter.flush() == []


def test_preroll_and_hangover_are_included():
    segmenter = SpeechSegmenter(pre_roll_ms=200, hangover_ms=400, min_speech_ms=200)
    frame = segmenter.frame_len
    audio = [0] * (10 * frame) + [5000] * (10 * frame) + [0] * (20 * frame)
    parts = segmenter.push(audio) + segmenter.flush()
    assert len(parts) == 1
    assert bytes_to_samples(parts[0].pcm) == audio
    assert parts[0].duration_ms == 800
    assert parts[0].sequence == 0


def test_forced_cuts_concatenate_without_overlap_or_gap():
    segmenter = SpeechSegmenter(pre_roll_ms=0, max_segment_ms=200, min_speech_ms=200)
    samples = [5000] * (25 * segmenter.frame_len)
    parts = segmenter.push(samples) + segmenter.flush()
    got = []
    for part in parts:
        got.extend(bytes_to_samples(part.pcm))
    assert got == samples
    assert [part.sequence for part in parts] == [0, 1, 2]
    assert len(parts[-1].pcm) < len(parts[0].pcm)


def test_same_input_is_byte_identical_and_reset_restarts_sequence():
    def run():
        segmenter = SpeechSegmenter(pre_roll_ms=200, hangover_ms=400, min_speech_ms=200)
        frame = segmenter.frame_len
        audio = [0] * (10 * frame) + [5000] * (10 * frame) + [0] * (20 * frame)
        return segmenter.push(audio) + segmenter.flush()

    first = run()
    second = run()
    assert [part.pcm for part in first] == [part.pcm for part in second]

    segmenter = SpeechSegmenter(pre_roll_ms=0, min_speech_ms=200)
    frame = segmenter.frame_len
    segmenter.push([5000] * (10 * frame))
    segmenter.reset()
    assert segmenter.flush() == []
    closed = segmenter.push([5000] * (10 * frame) + [0] * (20 * frame))
    assert [part.sequence for part in closed] == [0]


def test_quiet_forced_cut_keeps_a_short_tail_without_new_preroll():
    segmenter = SpeechSegmenter(pre_roll_ms=0, max_segment_ms=1000, min_speech_ms=200, hangover_ms=400)
    frame = segmenter.frame_len
    loud_tail = [5000] * (5 * frame)
    audio = [5000] * (49 * frame) + [0] * frame + loud_tail + [0] * (20 * frame)
    parts = segmenter.push(audio) + segmenter.flush()
    got = []
    for part in parts:
        got.extend(bytes_to_samples(part.pcm))
    assert got == audio
    assert len(parts) == 2
    assert loud_tail == bytes_to_samples(parts[1].pcm)[: len(loud_tail)]


def test_partial_frame_is_not_emitted_early():
    segmenter = SpeechSegmenter(pre_roll_ms=0)
    assert segmenter.push([0] * (segmenter.frame_len - 1)) == []
    assert segmenter.push([0]) == []
