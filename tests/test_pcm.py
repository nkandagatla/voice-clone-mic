import pytest

from voice_clone_mic.pcm import PcmFrameAssembler, bytes_to_samples, clip_i16, samples_to_bytes


def test_odd_byte_is_completed_by_the_next_chunk():
    assembler = PcmFrameAssembler()
    assert assembler.feed(b"\x01") == []
    assert assembler.feed(b"\x02") == [0x0201]


def test_fragmented_chunks_keep_sample_alignment():
    raw = samples_to_bytes([1, -2, 300, -400])
    assembler = PcmFrameAssembler()
    got = []
    chunks = (raw[:1], raw[1:3], raw[3:6], raw[6:])
    assert sum(len(chunk) for chunk in chunks) == len(raw)
    for chunk in chunks:
        got.extend(assembler.feed(chunk))
    assert got == [1, -2, 300, -400]
    assert assembler.flush() is False


def test_flush_drops_dangling_byte_and_resets():
    assembler = PcmFrameAssembler()
    assembler.feed(b"\x01\x02\x03")
    assert assembler.flush() is True
    assert assembler.feed(b"\x04\x05") == [0x0504]


def test_bytes_to_samples_rejects_odd_length():
    with pytest.raises(ValueError):
        bytes_to_samples(b"\x01")


def test_clip_i16_saturates():
    assert clip_i16(-40000) == -32768
    assert clip_i16(40000) == 32767
    assert clip_i16(5) == 5
