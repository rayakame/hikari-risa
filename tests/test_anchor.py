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

from risa.internal import anchor
from risa.state import schema


class Rank(msgspec.Struct):
    name: str
    score: int


def field(name: str, annotation: object, *, has_default: bool = True) -> schema.DurableField:
    return schema.DurableField(name=name, annotation=annotation, has_default=has_default)


POLL = [field("question", str, has_default=False), field("votes", dict[str, int])]


# --- keys and tags ---


def test_a_state_key_has_the_width_the_store_dialect_slices() -> None:
    assert len(anchor.make_state_key()) == anchor.STATE_KEY_LENGTH


def test_state_keys_do_not_repeat() -> None:
    assert len({anchor.make_state_key() for _ in range(1000)}) == 1000


def test_a_store_anchor_round_trips() -> None:
    key = anchor.make_state_key()
    parsed = anchor.parse(anchor.StoreAnchor(key=key).encode())
    assert isinstance(parsed, anchor.StoreAnchor)
    assert parsed.key == key


def test_a_message_anchor_round_trips() -> None:
    encoded = anchor.MessageAnchor(fingerprint="abc", seq=7, state="payload").encode()
    parsed = anchor.parse(encoded)
    assert isinstance(parsed, anchor.MessageAnchor)
    assert parsed.fingerprint == "abc"
    assert parsed.seq == 7
    assert parsed.state == "payload"


def test_a_message_anchor_declares_its_own_length() -> None:
    encoded = anchor.MessageAnchor(fingerprint="abc", seq=1, state="x" * 40).encode()
    assert anchor.parse(encoded[:-1]) is None
    assert anchor.parse(encoded + "x") is None


def test_the_placement_tag_tells_the_dialects_apart() -> None:
    assert anchor.StoreAnchor(key=anchor.make_state_key()).encode()[0] == anchor.STORE_TAG
    assert anchor.MessageAnchor(fingerprint="abc", seq=0, state="").encode()[0] == anchor.MESSAGE_TAG


@pytest.mark.parametrize("raw", ["", "x", "z" * 20, "m", "s", "sshort"])
def test_parsing_fails_soft_on_a_damaged_anchor(raw: str) -> None:
    assert anchor.parse(raw) is None


def test_a_message_anchor_read_as_a_store_anchor_is_refused() -> None:
    encoded = anchor.MessageAnchor(fingerprint="abc", seq=0, state="x" * anchor.STATE_KEY_LENGTH).encode()
    assert not isinstance(anchor.parse(encoded), anchor.StoreAnchor)


# --- chunking ---


def test_carving_lays_the_anchor_out_in_order() -> None:
    fragments = anchor.split_across("abcdefghij", [4, 4, 4])
    assert fragments == ["abcd", "efgh", "ij"]
    assert anchor.join_fragments(enumerate(fragments or [])) == "abcdefghij"


def test_carving_leaves_later_components_empty_when_the_anchor_is_short() -> None:
    assert anchor.split_across("ab", [4, 4, 4]) == ["ab", "", ""]


def test_carving_fills_an_exact_fit() -> None:
    assert anchor.split_across("abcdefgh", [4, 4]) == ["abcd", "efgh"]


def test_carving_refuses_an_anchor_that_does_not_fit() -> None:
    assert anchor.split_across("abcdefghi", [4, 4]) is None


def test_carving_refuses_any_anchor_when_there_is_nowhere_to_put_it() -> None:
    assert anchor.split_across("a", []) is None


def test_an_empty_anchor_needs_no_capacity_at_all() -> None:
    assert anchor.split_across("", []) == []
    assert anchor.split_across("", [4, 4]) == ["", ""]


def test_carving_skips_components_with_no_room() -> None:
    assert anchor.split_across("abcd", [0, 4]) == ["", "abcd"]


def test_gathering_reorders_fragments_by_index() -> None:
    assert anchor.join_fragments([(2, "ij"), (0, "abcd"), (1, "efgh")]) == "abcdefghij"


def test_gathering_accepts_a_replicated_fragment() -> None:
    # Every component of a store-backed view carries the same anchor at index 0.
    key = anchor.StoreAnchor(key=anchor.make_state_key()).encode()
    assert anchor.join_fragments([(0, key), (0, key), (0, key)]) == key


def test_gathering_refuses_disagreeing_fragments_at_one_index() -> None:
    assert anchor.join_fragments([(0, "abcd"), (0, "wxyz")]) is None


def test_gathering_refuses_a_gap() -> None:
    assert anchor.join_fragments([(0, "abcd"), (2, "ij")]) is None


def test_gathering_refuses_an_empty_message() -> None:
    assert anchor.join_fragments([]) is None


def test_a_message_missing_its_last_component_is_caught_by_the_declared_length() -> None:
    encoded = anchor.MessageAnchor(fingerprint="abc", seq=3, state="x" * 30).encode()
    fragments = anchor.split_across(encoded, [15, 15, 15])
    assert fragments is not None
    assert all(fragments), "the anchor must reach every component for this to be a truncation"

    reassembled = anchor.join_fragments(list(enumerate(fragments))[:-1])
    # The surviving fragments still form a contiguous run from zero, so nothing
    # about their framing looks wrong: only the length the anchor declares
    # about itself catches that the tail is missing.
    assert reassembled is not None
    assert anchor.parse(reassembled) is None


def test_dropping_a_component_that_carried_nothing_loses_nothing() -> None:
    encoded = anchor.MessageAnchor(fingerprint="abc", seq=3, state="x" * 10).encode()
    fragments = anchor.split_across(encoded, [20, 20])
    assert fragments is not None
    assert fragments == [encoded, ""]

    assert anchor.join_fragments(list(enumerate(fragments))[:-1]) == encoded


def test_a_split_anchor_survives_a_full_round_trip() -> None:
    state = schema.StateSchema(POLL)
    encoded = anchor.MessageAnchor(
        fingerprint=state.fingerprint,
        seq=12,
        state=state.pack(["Ship it?", {"yes": 3}]),
    ).encode()
    fragments = anchor.split_across(encoded, [30] * 5)
    assert fragments is not None

    parsed = anchor.parse(anchor.join_fragments(enumerate(fragments)) or "")
    assert isinstance(parsed, anchor.MessageAnchor)
    assert parsed.seq == 12
    assert state.unpack(parsed.fingerprint, parsed.state) == ("Ship it?", {"yes": 3})


# --- structural type ids ---


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (int, float),
        (int, str),
        (list[int], list[str]),
        (dict[str, int], dict[str, str]),
        (list[int], set[int]),
        (int | None, int),
    ],
)
def test_different_shapes_get_different_type_ids(left: object, right: object) -> None:
    assert schema.structural_type_id(left) != schema.structural_type_id(right)


def test_a_nested_change_is_as_visible_as_a_top_level_one() -> None:
    class Renamed(msgspec.Struct):
        label: str
        score: int

    class Retyped(msgspec.Struct):
        name: str
        score: str

    assert schema.structural_type_id(list[Rank]) != schema.structural_type_id(list[Renamed])
    assert schema.structural_type_id(list[Rank]) != schema.structural_type_id(list[Retyped])


def test_a_type_id_is_stable_across_equal_annotations() -> None:
    assert schema.structural_type_id(dict[str, list[int]]) == schema.structural_type_id(dict[str, list[int]])


# --- schema evolution ---


def test_state_round_trips_through_its_own_schema() -> None:
    state = schema.StateSchema(POLL)
    packed = state.pack(["Ship it?", {"yes": 12, "no": 5}])
    assert state.unpack(state.fingerprint, packed) == ("Ship it?", {"yes": 12, "no": 5})


def test_appending_a_defaulted_field_keeps_old_state_readable() -> None:
    old = schema.StateSchema(POLL)
    packed = old.pack(["Ship it?", {"yes": 1}])
    grown = schema.StateSchema([*POLL, field("closed", bool)])
    assert grown.unpack(old.fingerprint, packed) == ("Ship it?", {"yes": 1})


def test_appending_a_required_field_retires_old_state() -> None:
    old = schema.StateSchema(POLL)
    packed = old.pack(["Ship it?", {"yes": 1}])
    grown = schema.StateSchema([*POLL, field("owner", int, has_default=False)])
    assert grown.unpack(old.fingerprint, packed) is None


def test_removing_a_field_retires_old_state() -> None:
    old = schema.StateSchema([*POLL, field("closed", bool)])
    packed = old.pack(["Ship it?", {"yes": 1}, False])
    assert schema.StateSchema(POLL).unpack(old.fingerprint, packed) is None


def test_renaming_a_field_retires_old_state() -> None:
    old = schema.StateSchema(POLL)
    packed = old.pack(["Ship it?", {"yes": 1}])
    renamed = schema.StateSchema([field("prompt", str, has_default=False), POLL[1]])
    assert renamed.unpack(old.fingerprint, packed) is None


def test_retyping_a_field_retires_old_state() -> None:
    old = schema.StateSchema(POLL)
    packed = old.pack(["Ship it?", {"yes": 1}])
    retyped = schema.StateSchema([POLL[0], field("votes", list[int])])
    assert retyped.unpack(old.fingerprint, packed) is None


def test_swapping_two_same_typed_fields_retires_old_state() -> None:
    # The flare failure this library exists to prevent: identical shapes,
    # different meanings. Only the field names distinguish them.
    first = schema.StateSchema([field("title", str), field("body", str)])
    second = schema.StateSchema([field("body", str), field("title", str)])
    packed = first.pack(["a title", "a body"])
    assert first.fingerprint != second.fingerprint
    assert second.unpack(first.fingerprint, packed) is None


def test_state_from_an_unrelated_view_is_refused() -> None:
    state = schema.StateSchema(POLL)
    assert state.unpack("???", state.pack(["Ship it?", {}])) is None


def test_corrupted_state_is_refused_rather_than_decoded() -> None:
    state = schema.StateSchema(POLL)
    packed = state.pack(["Ship it?", {"yes": 1}])
    assert state.unpack(state.fingerprint, packed[:-2]) is None
    assert state.unpack(state.fingerprint, "not pack_bytesed at all") is None


def test_a_view_with_no_durable_fields_round_trips_an_empty_shape() -> None:
    state = schema.StateSchema([])
    assert state.unpack(state.fingerprint, state.pack([])) == ()
    assert state.unpack(state.fingerprint, "leftovers") is None


def test_nested_structures_survive_packing() -> None:
    state = schema.StateSchema([field("rows", list[Rank]), field("page", int)])
    rows = [Rank(name="ada", score=9), Rank(name="grace", score=7)]
    unpacked = state.unpack(state.fingerprint, state.pack([rows, 3]))
    assert unpacked == (rows, 3)


def test_packed_state_is_printable_ascii() -> None:
    state = schema.StateSchema(POLL)
    packed = state.pack(["ünïcödé ☃", {"a": -(2**40)}])
    assert packed.isascii()
    assert len(packed.encode()) == len(packed)
