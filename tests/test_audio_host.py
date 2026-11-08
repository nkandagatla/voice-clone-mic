from voice_clone_mic.audio_host import (
    CaptureBuffer,
    copy_input_channel,
    duplicate_channels,
    write_output_block,
)
from voice_clone_mic.pcm import bytes_to_samples


def test_capture_backlog_is_reported_from_the_session_buffer():
    from voice_clone_mic.devices import DeviceInfo
    from voice_clone_mic.mock_client import MockVoiceClient
    from voice_clone_mic.session import Session

    session = Session(
        "mock-voice",
        MockVoiceClient(),
        DeviceInfo(0, "Built-in Microphone", 1, 0, 16000, True, False),
        DeviceInfo(2, "BlackHole 2ch", 2, 2, 16000, False, False),
    )
    session.capture = CaptureBuffer(max_bytes=16000)
    session.capture.append(b"\x00\x01" * 160)
    assert session.status()["capture_backlog_ms"] == 10


def test_capture_buffer_drops_oldest_bytes_and_keeps_alignment():
    capture = CaptureBuffer(max_bytes=4)
    capture.append(b"\x01\x02\x03\x04\x05\x06")
    assert capture.pop_all() == b"\x03\x04\x05\x06"
    assert capture.dropped_bytes == 2


def test_channel_copy_keeps_first_channel_and_output_duplicates_mono():
    assert duplicate_channels([3, 4], 2) == [[3, 4], [3, 4]]
    copied = bytes_to_samples(copy_input_channel([[1, 9], [2, 8], [-1, 0]]))
    assert copied == [1, 2, -1]
    block = [[0, 0], [0, 0]]
    write_output_block(block, [3, 4])
    assert block == [[3, 3], [4, 4]]
