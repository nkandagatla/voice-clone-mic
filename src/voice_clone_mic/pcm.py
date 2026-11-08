"""Little-endian signed 16-bit PCM helpers."""

from __future__ import annotations


def clip_i16(value: int) -> int:
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return int(value)


def samples_to_bytes(samples: list[int]) -> bytes:
    return b"".join(
        clip_i16(sample).to_bytes(2, "little", signed=True) for sample in samples
    )


def bytes_to_samples(data: bytes) -> list[int]:
    if len(data) % 2 != 0:
        raise ValueError("PCM byte string must contain complete 16-bit frames")
    return [
        int.from_bytes(data[index : index + 2], "little", signed=True)
        for index in range(0, len(data), 2)
    ]


class PcmFrameAssembler:
    """Reassembles a byte stream that may split 16-bit frames."""

    def __init__(self) -> None:
        self._odd: int | None = None

    def feed(self, data: bytes) -> list[int]:
        if not data:
            return []
        samples: list[int] = []
        view = data
        if self._odd is not None:
            samples.append(int.from_bytes(bytes((self._odd, view[0])), "little", signed=True))
            self._odd = None
            view = view[1:]
        pair_count = len(view) // 2
        for index in range(pair_count):
            start = index * 2
            samples.append(int.from_bytes(view[start : start + 2], "little", signed=True))
        if len(view) % 2 == 1:
            self._odd = view[-1]
        return samples

    def flush(self) -> bool:
        dropped = self._odd is not None
        self._odd = None
        return dropped
