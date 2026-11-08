import pytest

from voice_clone_mic.devices import (
    DeviceInfo,
    DeviceSelectionError,
    fake_catalog,
    loop_key,
    plan_stream,
    select_devices,
)


def test_loop_keys_pair_cable_endpoints_and_split_blackhole_sizes():
    assert loop_key("CABLE Input (VB-Audio Virtual Cable)") == loop_key(
        "CABLE Output (VB-Audio Virtual Cable)"
    )
    assert loop_key("BlackHole 2ch") != loop_key("BlackHole 16ch")
    assert loop_key("Built-in Microphone") is None


def test_default_physical_input_and_explicit_blackhole_output():
    chosen_in, chosen_out = select_devices(fake_catalog(), None, "BlackHole", False)
    assert chosen_in.name == "Built-in Microphone"
    assert chosen_out.name == "BlackHole 2ch"


def test_output_is_required_and_speakers_are_rejected():
    with pytest.raises(DeviceSelectionError):
        select_devices(fake_catalog(), None, None, False)
    with pytest.raises(DeviceSelectionError):
        select_devices(fake_catalog(), None, "Speakers", False)


def test_allow_any_output_still_rejects_feedback():
    _chosen_in, chosen_out = select_devices(fake_catalog(), None, "Speakers", True)
    assert chosen_out.index == 1
    with pytest.raises(DeviceSelectionError):
        select_devices(fake_catalog(), "BlackHole", "BlackHole", True)


def test_vb_cable_capture_side_cannot_be_the_app_output_or_the_same_cable():
    with pytest.raises(DeviceSelectionError):
        select_devices(fake_catalog(), None, "CABLE Output", False)
    with pytest.raises(DeviceSelectionError):
        select_devices(fake_catalog(), "CABLE Output", "CABLE Input", False)


def test_ambiguous_output_is_rejected():
    with pytest.raises(DeviceSelectionError, match="Multiple"):
        select_devices(fake_catalog(), None, "CABLE", False)


def test_default_virtual_input_is_refused():
    devices = [
        DeviceInfo(0, "Built-in Microphone", 1, 0, 48000, False, False),
        DeviceInfo(1, "Speakers", 0, 2, 44100, False, True),
        DeviceInfo(2, "BlackHole 2ch", 2, 2, 48000, True, False),
        DeviceInfo(3, "CABLE Input (VB-Audio Virtual Cable)", 0, 8, 48000, False, False),
        DeviceInfo(4, "CABLE Output (VB-Audio Virtual Cable)", 8, 0, 48000, False, False),
    ]
    with pytest.raises(DeviceSelectionError):
        select_devices(devices, None, "CABLE Input", False)


def test_plan_stream_uses_reported_rate_and_channel_cap():
    mic, blackhole = select_devices(fake_catalog(), None, "BlackHole", False)
    assert plan_stream(mic, "input") == (48000, 1)
    assert plan_stream(blackhole, "output") == (48000, 2)
    cable_in = fake_catalog()[3]
    assert plan_stream(cable_in, "output") == (48000, 2)
    bad = DeviceInfo(9, "Odd rate", 1, 1, 1000, False, False)
    with pytest.raises(DeviceSelectionError):
        plan_stream(bad, "input")
