from voice_clone_mic.ordering import SequenceGate


def _view(items):
    return [(item.sequence, item.samples, item.failed) for item in items]


def test_later_segment_waits_for_the_older_one():
    gate = SequenceGate(max_hold_samples=1000)
    gate.reset(1)
    assert gate.push(1, 1, [3, 4], True) == []
    assert _view(gate.push(1, 0, [1, 2], True)) == [
        (0, [1, 2], False),
        (1, [3, 4], False),
    ]


def test_failed_sequence_discards_a_late_chunk_and_advances():
    gate = SequenceGate(max_hold_samples=1000)
    gate.reset(1)
    failed = gate.fail(1, 0)
    assert failed[0].sequence == 0 and failed[0].failed and failed[0].samples == []
    assert gate.push(1, 0, [9], True) == []
    assert _view(gate.push(1, 1, [5], True)) == [(1, [5], False)]


def test_hold_limit_drops_the_newest_sequence_only():
    gate = SequenceGate(max_hold_samples=4)
    gate.reset(1)
    released = []
    released += gate.push(1, 0, [1], False)
    released += gate.push(1, 1, [2, 2], True)
    released += gate.push(1, 2, [9, 9, 9, 9, 9], True)
    released += gate.push(1, 0, [], True)
    assert _view(released) == [
        (0, [1], False),
        (1, [2, 2], False),
        (2, [], True),
    ]


def test_reset_rejects_the_previous_generation():
    gate = SequenceGate(max_hold_samples=100)
    gate.reset(1)
    gate.reset(2)
    assert gate.push(1, 0, [1, 1, 1], True) == []
