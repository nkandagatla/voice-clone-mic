from voice_clone_mic.ptt.capture import BoundedCapture
from voice_clone_mic.ptt.gate import assess_transcript


def _segment(text="hello", no_speech=0.1, logprob=-0.2, compression=1.2):
    return {
        "text": text,
        "no_speech_prob": no_speech,
        "avg_logprob": logprob,
        "compression_ratio": compression,
    }


def test_quiet_audio_is_not_spoken():
    decision = assess_transcript("hello", [_segment()], rms=10)
    assert decision.speak is False
    assert decision.reason == "silence"


def test_empty_and_no_speech_and_low_confidence_are_not_spoken():
    assert assess_transcript("   ", [_segment("   ")], rms=2000).reason == "empty"
    assert assess_transcript("Thanks", [_segment(no_speech=0.8)], rms=2000).reason == "no_speech"
    assert assess_transcript("Thanks", [_segment(logprob=-1.4)], rms=2000).reason == "low_confidence"
    assert assess_transcript("Thanks", [_segment(compression=3.0)], rms=2000).reason == "low_confidence"
    assert assess_transcript("Thanks", [], rms=2000).reason == "empty"


def test_clear_speech_segment_is_accepted():
    decision = assess_transcript("  hello there  ", [_segment("hello there")], rms=2000)
    assert decision.speak is True
    assert decision.text == "hello there"
    assert decision.reason == "ok"


def test_capture_drops_samples_past_the_cap():
    capture = BoundedCapture(max_samples=4)
    capture.extend([1, 2, 3])
    capture.extend([4, 5, 6])
    assert capture.snapshot() == [1, 2, 3, 4]
    assert capture.dropped == 2
    capture.clear()
    assert capture.snapshot() == []
