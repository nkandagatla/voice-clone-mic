from voice_clone_mic.playback import PlaybackBuffer


def test_underrun_writes_silence():
    playback = PlaybackBuffer(48000, 2)
    assert playback.read_mono(4) == [0, 0, 0, 0]
    assert playback.underruns == 1


def test_stale_generation_is_rejected_and_clear_empties_audio():
    playback = PlaybackBuffer(48000, 1)
    generation = playback.generation
    assert playback.write(generation, [1, 2, 3]) is True
    refreshed = playback.clear_and_bump()
    assert playback.write(generation, [9, 9]) is False
    assert playback.read_mono(2) == [0, 0]
    assert playback.write(refreshed, [4, 5]) is True
    assert playback.read_mono(2) == [4, 5]


def test_duration_cap_drops_the_new_chunk_only():
    playback = PlaybackBuffer(1000, 1, max_ms=10)
    assert playback.write(playback.generation, [1] * 10) is True
    assert playback.write(playback.generation, [2]) is False
    assert playback.dropped_writes == 1
    assert playback.read_mono(10) == [1] * 10
    assert playback.queued_ms() == 0
