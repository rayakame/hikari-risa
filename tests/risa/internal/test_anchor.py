from __future__ import annotations

import msgspec
import pytest

import risa
from risa.internal import anchor
from risa.internal import codec
from risa.internal import wire

SCHEMA_FP = "aB7"


def tail_for(*encoded: str) -> int:
    return codec.FINGERPRINT_LENGTH + len(codec.pack_frames(encoded)) if encoded else 0


def slot_for(*encoded: str) -> int:
    # What a real component leaves for a fragment once its handler args are written.
    return codec.MAX_FRAGMENT_LENGTH - tail_for(*encoded)


def message_anchor(values: list[object], *, seq: int = 0) -> str:
    return anchor.MessageAnchor(SCHEMA_FP, seq, anchor.pack_payload(values)).pack()


def test_the_placement_tags_are_writable_wire_characters() -> None:
    assert anchor.PLACEMENT_MESSAGE in wire.ALPHABET
    assert anchor.PLACEMENT_STORE in wire.ALPHABET
    assert anchor.PLACEMENT_MESSAGE != anchor.PLACEMENT_STORE


def test_the_message_header_is_nine_characters() -> None:
    assert anchor.MESSAGE_HEADER_LENGTH == 9
    assert len(message_anchor([])) == anchor.MESSAGE_HEADER_LENGTH + len(anchor.pack_payload([]))


def test_a_bare_button_carries_more_state_than_a_bound_one() -> None:
    bound = slot_for(codec.IntConverter(int).encode(2))

    assert slot_for() == codec.MAX_FRAGMENT_LENGTH
    assert bound < codec.MAX_FRAGMENT_LENGTH


@pytest.mark.parametrize(
    "values",
    [[], [3, 1, 4], ["Ship it?", {"Red": 3, "Blue": 1}], [1364800923047857855], [True, None, "hÿ"]],
)
def test_a_payload_survives_the_round_trip(values: list[object]) -> None:
    assert anchor.unpack_payload(anchor.pack_payload(values)) == values


def test_a_message_anchor_survives_the_round_trip() -> None:
    raw = message_anchor([3, 1, 4], seq=7)

    parsed = anchor.parse(raw)

    assert isinstance(parsed, anchor.MessageAnchor)
    assert parsed.schema_fp == SCHEMA_FP
    assert parsed.seq == 7
    assert anchor.unpack_payload(parsed.payload) == [3, 1, 4]


def test_a_store_anchor_survives_the_round_trip() -> None:
    minted = anchor.StoreAnchor.mint()

    parsed = anchor.parse(minted.pack())

    assert isinstance(parsed, anchor.StoreAnchor)
    assert parsed.key == minted.key


def test_a_store_anchor_is_short_enough_to_replicate_everywhere() -> None:
    raw = anchor.StoreAnchor.mint().pack()

    assert len(raw) == anchor.STORE_ANCHOR_LENGTH
    assert len(raw) < codec.MAX_FRAGMENT_LENGTH


def test_minted_keys_do_not_repeat() -> None:
    assert len({anchor.StoreAnchor.mint().key for _ in range(100)}) == 100


def test_everything_written_stays_inside_the_wire_alphabet() -> None:
    emitted = message_anchor(['🎈 weird " text \\ here', -(2**63)]) + anchor.StoreAnchor.mint().pack()

    assert all(char in wire.ALPHABET for char in emitted)


@pytest.mark.parametrize(
    "capacities",
    [[89], [84, 84, 84], [89, 84, 76], [10, 10, 10, 10, 10], [0, 89], [5] * 20],
)
def test_carving_and_gathering_are_inverses(capacities: list[int]) -> None:
    raw = message_anchor([3, 1, 4])

    fragments = anchor.carve(raw, capacities, view_name="poll")

    assert len(fragments) == len(capacities)
    assert anchor.gather(dict(enumerate(fragments))) == raw


def test_carving_fills_the_first_slots_and_leaves_the_rest_empty() -> None:
    raw = message_anchor([3, 1, 4])
    assert len(raw) < 84

    fragments = anchor.carve(raw, [84, 84, 84], view_name="poll")

    assert fragments[0] == raw
    assert not fragments[1]
    assert not fragments[2]


def test_carving_spills_into_later_slots_when_it_has_to() -> None:
    raw = message_anchor([0] * 40)
    fragments = anchor.carve(raw, [10, 10, 10, 10, 10, 10, 10, 10], view_name="wide")

    assert len(fragments[0]) == 10
    assert "".join(fragments) == raw


def test_an_exact_fit_is_not_an_overflow() -> None:
    raw = message_anchor([3, 1, 4])

    fragments = anchor.carve(raw, [len(raw)], view_name="poll")

    assert fragments == [raw]


def test_state_that_does_not_fit_names_the_numbers_and_the_way_out() -> None:
    raw = message_anchor(["x" * 200])

    with pytest.raises(risa.StateOverflowError) as exc_info:
        anchor.carve(raw, [84, 84, 84], view_name="poll")

    assert exc_info.value.view_name == "poll"
    assert exc_info.value.needed == len(raw)
    assert exc_info.value.available == 252
    assert exc_info.value.slots == 3
    assert "InStore" in str(exc_info.value)


def test_a_slot_never_carries_more_than_one_length_digit_can_frame() -> None:
    raw = message_anchor(["x" * 90])

    fragments = anchor.carve(raw, [1000, 1000], view_name="greedy")

    assert len(fragments[0]) == codec.MAX_FRAGMENT_LENGTH


def test_slots_past_the_addressable_limit_carry_nothing() -> None:
    capacities = [89] * (codec.MAX_FRAGMENTS + 3)
    raw = message_anchor([3, 1, 4])

    fragments = anchor.carve(raw, capacities, view_name="huge")

    assert len(fragments) == len(capacities)
    assert not any(fragments[codec.MAX_FRAGMENTS :])


def test_gathering_needs_the_first_fragment() -> None:
    assert anchor.gather({1: "bc", 2: "de"}) is None
    assert anchor.gather({}) is None


def test_gathering_stops_at_a_gap() -> None:
    assert anchor.gather({0: "ab", 1: "cd", 3: "ef"}) == "abcd"


def test_a_message_missing_its_tail_is_caught_by_the_declared_total() -> None:
    raw = message_anchor([0] * 40)
    fragments = anchor.carve(raw, [30, 30, 30], view_name="wide")

    torn = anchor.gather({0: fragments[0], 1: fragments[1]})

    assert torn is not None
    assert torn != raw
    assert anchor.parse(torn) is None


@pytest.mark.parametrize("raw", ["", "x", "?0EaB7000", "zzzz"])
def test_an_unknown_tag_fails_soft(raw: str) -> None:
    assert anchor.parse(raw) is None


def test_a_truncated_message_anchor_fails_soft() -> None:
    assert anchor.parse(message_anchor([3, 1, 4])[:5]) is None


def test_a_corrupted_total_fails_soft() -> None:
    raw = message_anchor([3, 1, 4])

    assert anchor.parse(raw[:1] + '"' + raw[2:]) is None


def test_a_corrupted_sequence_counter_fails_soft() -> None:
    raw = message_anchor([3, 1, 4])
    start = 1 + anchor.TOTAL_WIDTH + anchor.SCHEMA_FP_LENGTH

    assert anchor.parse(raw[:start] + '"' + raw[start + 1 :]) is None


@pytest.mark.parametrize("key", ["", "short", "x" * 40])
def test_a_store_anchor_of_the_wrong_length_fails_soft(key: str) -> None:
    assert anchor.parse(anchor.PLACEMENT_STORE + key) is None


def test_a_store_key_that_does_not_decode_fails_soft() -> None:
    forged = anchor.PLACEMENT_STORE + '"' * (anchor.STORE_ANCHOR_LENGTH - 1)

    assert anchor.parse(forged) is None


@pytest.mark.parametrize("payload", ['"', "not base85 at all!"])
def test_a_garbled_payload_is_unreadable(payload: str) -> None:
    assert anchor.unpack_payload(payload) is None


@pytest.mark.parametrize("value", [{"counts": [3, 1, 4]}, 7, "just a string"])
def test_a_payload_that_is_not_a_positional_array_is_refused(value: object) -> None:
    assert anchor.unpack_payload(wire.pack_bytes(msgspec.msgpack.encode(value))) is None


def test_durable_ints_are_bounded_by_msgpack_not_by_the_wire() -> None:
    # The wire args codec takes arbitrary-precision ints; msgpack stops at 64 bits, so a
    # durable field holding a larger one fails at render rather than silently truncating.
    assert anchor.unpack_payload(anchor.pack_payload([2**64 - 1])) == [2**64 - 1]
    with pytest.raises(OverflowError):
        anchor.pack_payload([2**64])


def test_a_schema_fingerprint_of_the_wrong_width_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        anchor.MessageAnchor("ab", 0, "").pack()


def test_a_sequence_counter_wider_than_its_field_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        anchor.MessageAnchor(SCHEMA_FP, wire.largest_value(anchor.SEQ_WIDTH) + 1, "").pack()
