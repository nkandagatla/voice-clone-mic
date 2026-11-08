import pytest

from voice_clone_mic.resample import StreamResampler


def test_chunked_matches_oneshot_for_three_rates():
    source = [0, 1000, -1000, 32767, -32768] * 50
    for src, dst in ((24000, 48000), (48000, 16000), (24000, 44100)):
        whole = StreamResampler(src, dst)
        once = whole.process(source) + whole.flush()
        part = StreamResampler(src, dst)
        got = []
        for offset in range(0, len(source), 7):
            got.extend(part.process(source[offset : offset + 7]))
        got.extend(part.flush())
        assert got == once
        assert abs(len(once) - round(len(source) * dst / src)) <= 1


def test_constant_signal_stays_constant():
    resampler = StreamResampler(16000, 48000)
    output = resampler.process([1000] * 30) + resampler.flush()
    assert output
    assert set(output) == {1000}


def test_reset_drops_carried_state():
    resampler = StreamResampler(16000, 16000)
    resampler.process([1, 2, 3, 4])
    resampler.reset()
    got = resampler.process([9, 8, 7]) + resampler.flush()
    fresh = StreamResampler(16000, 16000)
    assert got == fresh.process([9, 8, 7]) + fresh.flush()


def test_non_positive_rate_is_rejected():
    with pytest.raises(ValueError):
        StreamResampler(0, 16000)
