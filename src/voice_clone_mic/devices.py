"""Virtual-cable selection that refuses speaker fallback and cable feedback."""

from __future__ import annotations

import re
from dataclasses import dataclass


class DeviceSelectionError(ValueError):
    """The requested devices cannot be used safely."""


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    default_input: bool
    default_output: bool


def fake_catalog() -> list[DeviceInfo]:
    return [
        DeviceInfo(0, "Built-in Microphone", 1, 0, 48000, True, False),
        DeviceInfo(1, "Speakers", 0, 2, 44100, False, True),
        DeviceInfo(2, "BlackHole 2ch", 2, 2, 48000, False, False),
        DeviceInfo(3, "CABLE Input (VB-Audio Virtual Cable)", 0, 8, 48000, False, False),
        DeviceInfo(4, "CABLE Output (VB-Audio Virtual Cable)", 8, 0, 48000, False, False),
    ]


def loop_key(name: str) -> str | None:
    squashed = " ".join(name.lower().split())
    if "blackhole" in squashed:
        return "bh:" + squashed
    if (
        "vb-audio" in squashed
        or "vb-cable" in squashed
        or re.search(r"\bcable (input|output)\b", squashed)
    ):
        core = squashed
        for token in ("input", "output", "vb-audio", "virtual", "cable"):
            core = core.replace(token, " ")
        return "vb:" + " ".join(core.split())
    return None


def select_devices(
    devices: list[DeviceInfo],
    input_query: str | None,
    output_query: str | None,
    allow_any_output: bool,
) -> tuple[DeviceInfo, DeviceInfo]:
    if output_query is None:
        raise DeviceSelectionError(
            "An output virtual cable is required. This app does not choose speakers for you."
        )
    if input_query is None:
        defaults = [device for device in devices if device.default_input]
        if len(defaults) != 1:
            raise DeviceSelectionError("No single default input device is available. Pass --input.")
        chosen_in = defaults[0]
        if loop_key(chosen_in.name) is not None:
            raise DeviceSelectionError(
                "The default input looks like a virtual cable. Pass --input with a physical microphone."
            )
    else:
        chosen_in = _match(devices, input_query, "input")
    chosen_out = _match(devices, output_query, "output")
    if chosen_in.max_input_channels < 1:
        raise DeviceSelectionError(f"{chosen_in.name} has no input channels.")
    if chosen_out.max_output_channels < 1:
        raise DeviceSelectionError(
            f"{chosen_out.name} has no playback channels. "
            "On VB-CABLE select the playback side, usually CABLE Input."
        )
    if not allow_any_output and loop_key(chosen_out.name) is None:
        raise DeviceSelectionError(
            f"{chosen_out.name} does not look like BlackHole or VB-CABLE. "
            "Pass a virtual cable, or --allow-any-output to override the name check."
        )
    in_key = loop_key(chosen_in.name)
    out_key = loop_key(chosen_out.name)
    if chosen_in.index == chosen_out.index or (in_key is not None and in_key == out_key):
        raise DeviceSelectionError(
            "Input and output look like the same virtual cable. "
            "Capturing that cable would feed it back into itself."
        )
    return chosen_in, chosen_out


def plan_stream(device: DeviceInfo, direction: str) -> tuple[int, int]:
    rate = int(device.default_samplerate)
    if rate < 8000 or rate > 192000:
        raise DeviceSelectionError(
            f"{device.name} reported sample rate {device.default_samplerate}, which this app will not use."
        )
    if direction == "input":
        if device.max_input_channels < 1:
            raise DeviceSelectionError(f"{device.name} has no input channels.")
        return rate, 1
    if direction == "output":
        if device.max_output_channels < 1:
            raise DeviceSelectionError(f"{device.name} has no playback channels.")
        channels = 1 if device.max_output_channels == 1 else 2
        return rate, channels
    raise DeviceSelectionError(f"Unknown stream direction {direction!r}.")


def _match(devices: list[DeviceInfo], query: str, direction: str) -> DeviceInfo:
    if query.isdigit():
        hits = [device for device in devices if device.index == int(query)]
    else:
        needle = query.lower()
        hits = [device for device in devices if needle in device.name.lower()]
    if not hits:
        raise DeviceSelectionError(f"No {direction} device matches {query!r}.")
    if len(hits) > 1:
        names = ", ".join(device.name for device in hits)
        raise DeviceSelectionError(f"Multiple {direction} devices match {query!r}: {names}.")
    return hits[0]
