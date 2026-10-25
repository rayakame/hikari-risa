# Copyright (c) 2025 Rayakame
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import annotations

import msgspec
import pytest

import risa
from risa import codecs
from risa.internal import codec
from risa.internal import wire
from risa.state import anchor

SCHEMA_FP = "aB7"


def payload_of(values: list[object]) -> bytes:
    return msgspec.msgpack.encode(values)


def message_anchor(values: list[object], *, seq: int = 0) -> bytes:
    return anchor.MessageAnchor(SCHEMA_FP, seq, payload_of(values)).pack()


def carve(raw: bytes, capacities: list[int], *, view_name: str = "poll") -> list[str]:
    return list(anchor.carve(raw, capacities, wire_codec=codecs.BASE85, view_name=view_name))


def gather(fragments: list[str]) -> bytes | None:
    return anchor.gather({index: codecs.BASE85.decode(fragment) for index, fragment in enumerate(fragments)})


def test_the_placement_tags_are_single_distinct_bytes() -> None:
    assert len(anchor.PLACEMENT_MESSAGE) == 1
    assert len(anchor.PLACEMENT_STORE) == 1
    assert anchor.PLACEMENT_MESSAGE != anchor.PLACEMENT_STORE


def test_the_message_header_is_nine_bytes() -> None:
    assert anchor.MESSAGE_HEADER_LENGTH == 9
    assert len(message_anchor([])) == anchor.MESSAGE_HEADER_LENGTH + len(payload_of([]))


def test_a_message_anchor_survives_the_round_trip() -> None:
    raw = message_anchor([3, 1, 4], seq=7)

    parsed = anchor.parse(raw)

    assert isinstance(parsed, anchor.MessageAnchor)
    assert parsed.schema_fp == SCHEMA_FP
    assert parsed.seq == 7
    assert parsed.payload == payload_of([3, 1, 4])


def test_the_total_counts_the_whole_anchor() -> None:
    raw = message_anchor([3, 1, 4])

    assert int.from_bytes(raw[1 : 1 + anchor.TOTAL_WIDTH], "big") == len(raw)


def test_a_store_anchor_survives_the_round_trip() -> None:
    minted = anchor.StoreAnchor.mint()

    parsed = anchor.parse(minted.pack())

    assert isinstance(parsed, anchor.StoreAnchor)
    assert parsed.key == minted.key


def test_a_store_anchor_is_short_enough_to_replicate_everywhere() -> None:
    raw = anchor.StoreAnchor.mint().pack()

    assert len(raw) == anchor.STORE_ANCHOR_LENGTH
    assert len(codecs.BASE85.encode(raw)) < codec.MAX_FRAGMENT_LENGTH


def test_a_store_token_is_printable_and_stable() -> None:
    minted = anchor.StoreAnchor.mint()

    assert all(char in wire.ALPHABET for char in minted.token)
    assert minted.token == anchor.StoreAnchor(minted.key).token


def test_minted_keys_do_not_repeat() -> None:
    assert len({anchor.StoreAnchor.mint().key for _ in range(100)}) == 100


@pytest.mark.parametrize(
    "capacities",
    [[88], [84, 84, 84], [88, 84, 76], [10, 10, 10, 10, 10], [0, 88], [5] * 20],
)
def test_carving_and_gathering_are_inverses(capacities: list[int]) -> None:
    raw = message_anchor([3, 1, 4])

    fragments = carve(raw, capacities)

    assert len(fragments) == len(capacities)
    assert gather(fragments) == raw


def test_every_fragment_fits_its_slot() -> None:
    raw = message_anchor([0] * 60)
    capacities = [88, 13, 7, 6, 5, 40]

    fragments = carve(raw, capacities)

    assert all(len(fragment) <= capacity for fragment, capacity in zip(fragments, capacities, strict=True))
    assert gather(fragments) == raw


def test_carving_fills_the_first_slots_and_leaves_the_rest_empty() -> None:
    raw = message_anchor([3, 1, 4])

    fragments = carve(raw, [84, 84, 84])

    assert fragments[0] == codecs.BASE85.encode(raw)
    assert not fragments[1]
    assert not fragments[2]


def test_carving_spills_into_later_slots_when_it_has_to() -> None:
    raw = message_anchor([0] * 40)

    fragments = carve(raw, [10] * 8, view_name="wide")

    assert len(fragments[0]) == 10
    assert gather(fragments) == raw


def test_an_exact_fit_is_not_an_overflow() -> None:
    raw = message_anchor([3, 1, 4])

    fragments = carve(raw, [len(codecs.BASE85.encode(raw))])

    assert gather(fragments) == raw


def test_everything_carved_with_the_default_codec_stays_inside_the_wire_alphabet() -> None:
    raw = message_anchor(['🎈 weird " text \\ here', -(2**63)])

    assert all(char in wire.ALPHABET for fragment in carve(raw, [40, 40, 40]) for char in fragment)


def test_state_that_does_not_fit_names_the_numbers_and_the_way_out() -> None:
    raw = message_anchor(["x" * 300])

    with pytest.raises(risa.StateOverflowError) as exc_info:
        carve(raw, [84, 84, 84])

    overflow = exc_info.value
    assert overflow.view_name == "poll"
    assert overflow.needed == len(raw)
    assert overflow.available == 3 * codecs.BASE85.max_bytes(84)
    assert overflow.slots == 3
    assert overflow.codec_name == "base85"
    assert "InStore" in str(overflow)
    assert "base85" in str(overflow)


def test_a_slot_never_carries_more_than_one_length_digit_can_frame() -> None:
    raw = message_anchor(["x" * 100])

    fragments = carve(raw, [1000, 1000], view_name="greedy")

    assert len(fragments[0]) <= codec.MAX_FRAGMENT_LENGTH
    assert codecs.BASE85.decode(fragments[0]) == raw[: codecs.BASE85.max_bytes(codec.MAX_FRAGMENT_LENGTH)]


def test_slots_past_the_addressable_limit_carry_nothing() -> None:
    capacities = [88] * (codec.MAX_FRAGMENTS + 3)

    fragments = carve(message_anchor([3, 1, 4]), capacities, view_name="huge")

    assert len(fragments) == len(capacities)
    assert not any(fragments[codec.MAX_FRAGMENTS :])


def test_a_denser_codec_carries_more_per_slot() -> None:
    raw = message_anchor(["x" * 150])

    with pytest.raises(risa.StateOverflowError):
        anchor.carve(raw, [88, 88], wire_codec=codecs.BASE85, view_name="poll")
    fragments = anchor.carve(raw, [88, 88], wire_codec=codecs.BASE32768, view_name="poll")

    assert all(len(fragment) <= 88 for fragment in fragments)
    assert anchor.gather({i: codecs.BASE32768.decode(f) for i, f in enumerate(fragments)}) == raw


def test_gathering_needs_the_first_fragment() -> None:
    assert anchor.gather({1: b"bc", 2: b"de"}) is None
    assert anchor.gather({}) is None


def test_gathering_stops_at_a_gap() -> None:
    assert anchor.gather({0: b"ab", 1: b"cd", 3: b"ef"}) == b"abcd"


def test_a_message_missing_its_tail_is_caught_by_the_declared_total() -> None:
    raw = message_anchor([0] * 40)
    fragments = carve(raw, [30, 30, 30], view_name="wide")

    torn = gather(fragments[:2])

    assert torn is not None
    assert torn != raw
    assert anchor.parse(torn) is None


@pytest.mark.parametrize("raw", [b"", b"x", b"?\x00\x0eaB7\x00\x00\x00", b"zzzz"])
def test_an_unknown_tag_fails_soft(raw: bytes) -> None:
    assert anchor.parse(raw) is None


def test_a_truncated_message_anchor_fails_soft() -> None:
    assert anchor.parse(message_anchor([3, 1, 4])[:5]) is None


def test_a_corrupted_total_fails_soft() -> None:
    raw = message_anchor([3, 1, 4])

    assert anchor.parse(raw[:1] + b"\xff" + raw[2:]) is None


def test_a_non_ascii_schema_fingerprint_fails_soft() -> None:
    raw = message_anchor([3, 1, 4])
    start = 1 + anchor.TOTAL_WIDTH

    assert anchor.parse(raw[:start] + b"\xff" + raw[start + 1 :]) is None


@pytest.mark.parametrize("key", [b"", b"short", b"x" * 40])
def test_a_store_anchor_of_the_wrong_length_fails_soft(key: bytes) -> None:
    assert anchor.parse(anchor.PLACEMENT_STORE + key) is None


def test_a_schema_fingerprint_of_the_wrong_width_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        anchor.MessageAnchor("ab", 0, b"").pack()


def test_a_sequence_counter_wider_than_its_field_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        anchor.MessageAnchor(SCHEMA_FP, anchor.MAX_SEQ + 1, b"").pack()


def test_a_payload_the_total_cannot_describe_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        anchor.MessageAnchor(SCHEMA_FP, 0, b"x" * anchor.MAX_TOTAL).pack()


def test_a_store_key_of_the_wrong_width_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="store key"):
        anchor.StoreAnchor(b"short").pack()
