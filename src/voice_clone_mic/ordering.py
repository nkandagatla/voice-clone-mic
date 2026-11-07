"""Release converted audio only in segment order."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Released:
    sequence: int
    samples: list[int]
    failed: bool
    finished: bool = False


@dataclass
class _Slot:
    samples: list[int] = field(default_factory=list)
    finished: bool = False
    failed: bool = False


class SequenceGate:
    def __init__(self, max_hold_samples: int) -> None:
        if max_hold_samples < 1:
            raise ValueError("hold limit must be positive")
        self.max_hold_samples = max_hold_samples
        self.generation = 0
        self.next_seq = 0
        self.slots: dict[int, _Slot] = {}

    def reset(self, generation: int) -> None:
        self.generation = generation
        self.next_seq = 0
        self.slots.clear()

    def held_samples(self) -> int:
        return sum(
            len(slot.samples)
            for sequence, slot in self.slots.items()
            if sequence != self.next_seq and not slot.failed
        )

    def push(
        self,
        generation: int,
        sequence: int,
        samples: list[int],
        finished: bool,
    ) -> list[Released]:
        if generation != self.generation or sequence < self.next_seq:
            return []
        slot = self.slots.get(sequence)
        if slot is not None and (slot.failed or slot.finished):
            return []
        if slot is None:
            slot = _Slot()
            self.slots[sequence] = slot
        if samples:
            slot.samples.extend(samples)
        if finished:
            slot.finished = True
        self._enforce_limit()
        return self._drain()

    def fail(self, generation: int, sequence: int) -> list[Released]:
        if generation != self.generation or sequence < self.next_seq:
            return []
        slot = self.slots.get(sequence)
        if slot is not None and slot.failed:
            return []
        if slot is None:
            slot = _Slot()
            self.slots[sequence] = slot
        slot.failed = True
        slot.finished = True
        slot.samples.clear()
        return self._drain()

    def _enforce_limit(self) -> None:
        while self.held_samples() > self.max_hold_samples:
            victims = [
                sequence
                for sequence, slot in self.slots.items()
                if sequence != self.next_seq and not slot.failed
            ]
            if not victims:
                return
            victim = max(victims)
            slot = self.slots[victim]
            slot.samples.clear()
            slot.failed = True
            slot.finished = True

    def _drain(self) -> list[Released]:
        released: list[Released] = []
        while True:
            slot = self.slots.get(self.next_seq)
            if slot is None:
                break
            if slot.failed:
                released.append(Released(self.next_seq, [], True, True))
                self._advance()
                continue
            if slot.samples:
                finished = slot.finished
                released.append(Released(self.next_seq, slot.samples, False, finished))
                slot.samples = []
                if finished:
                    self._advance()
                    continue
                break
            if slot.finished:
                self._advance()
                continue
            break
        return released

    def _advance(self) -> None:
        self.slots.pop(self.next_seq, None)
        self.next_seq += 1
