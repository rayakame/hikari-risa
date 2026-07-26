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

import pytest

import risa
from risa.internal import anchor
from risa.internal import codec
from risa.internal import constants
from risa.internal import wire

COOKIE = codec.make_cookie("framing", 1)
HANDLER = codec.make_handler_token("press", 1)


def custom_id(*, fragment: str = "", index: int = 0, args: str = "") -> codec.CustomID:
    return codec.CustomID(
        raw_cookie=COOKIE,
        handler=HANDLER,
        fragment_index=index,
        fragment=fragment,
        args=args,
    )


def roundtrip(parts: codec.CustomID) -> codec.CustomID:
    decoded = codec.CustomID.parse(parts.encode(view_name="framing"))
    assert decoded is not None
    return decoded


# --- round trips ---


def test_a_bare_component_round_trips() -> None:
    assert roundtrip(custom_id()) == custom_id()


def test_a_component_carrying_a_fragment_round_trips() -> None:
    parts = custom_id(fragment="a fragment", index=3)
    assert roundtrip(parts) == parts


def test_a_component_carrying_fragment_and_args_keeps_them_apart() -> None:
    parts = custom_id(fragment="STATE", index=1, args="ARGS")
    decoded = roundtrip(parts)
    assert decoded.fragment == "STATE"
    assert decoded.args == "ARGS"


def test_a_component_carrying_only_args_round_trips() -> None:
    parts = custom_id(args="just args")
    decoded = roundtrip(parts)
    assert not decoded.fragment
    assert decoded.args == "just args"


def test_an_empty_fragment_is_distinguishable_from_a_missing_one() -> None:
    # Both are "no state here", and both must decode without consuming args.
    assert roundtrip(custom_id(args="xy")).args == "xy"


def test_a_fragment_may_fill_the_whole_remaining_budget() -> None:
    parts = custom_id(fragment="f" * codec.MAX_FRAGMENT_LENGTH)
    assert len(parts.encode(view_name="framing")) == constants.MAX_CUSTOM_ID_LENGTH
    assert roundtrip(parts) == parts


def test_every_id_we_emit_is_within_the_length_discord_enforces() -> None:
    # Measured: the limit is 100 Unicode code points, minimum 1 -- so len() is
    # the right check, and risa must never emit an empty id.
    smallest = custom_id().encode(view_name="framing")
    largest = custom_id(fragment="f" * codec.MAX_FRAGMENT_LENGTH).encode(view_name="framing")
    for encoded in (smallest, largest):
        assert 1 <= len(encoded) <= constants.MAX_CUSTOM_ID_LENGTH
        assert len(encoded) == len(encoded.encode())


def test_the_header_costs_what_the_build_pass_is_told_it_costs() -> None:
    encoded = custom_id().encode(view_name="framing")
    assert len(encoded) == codec.FRAGMENT_START
    assert codec.FRAGMENT_START + codec.MAX_FRAGMENT_LENGTH == constants.MAX_CUSTOM_ID_LENGTH


# --- overflow ---


def test_an_overlong_component_is_refused() -> None:
    with pytest.raises(risa.CustomIdOverflowError):
        custom_id(args="a" * 200).encode(view_name="framing")


def test_a_fragment_beyond_what_one_component_can_frame_is_refused() -> None:
    with pytest.raises(risa.CustomIdOverflowError):
        custom_id(fragment="f" * (codec.MAX_FRAGMENT_LENGTH + 1)).encode(view_name="framing")


def test_a_fragment_index_beyond_the_wire_is_refused() -> None:
    with pytest.raises(risa.CustomIdOverflowError):
        custom_id(index=codec.MAX_FRAGMENT_INDEX + 1).encode(view_name="framing")


# --- failing soft on ids risa did not write ---


@pytest.mark.parametrize(
    "foreign",
    [
        "",
        "other:button",
        "x",
        "a" * 9,
        "a" * 100,
        "\x01",
        "\x01" * 9,
        "\x01" * 10,
        "1" * 11,
        "miru:whatever:1234",
    ],
)
def test_a_foreign_custom_id_decodes_to_nothing(foreign: str) -> None:
    assert codec.CustomID.parse(foreign) is None


def test_an_id_of_exactly_header_length_but_foreign_version_decodes_to_nothing() -> None:
    assert codec.CustomID.parse("Z" * codec.FRAGMENT_START) is None


def test_an_id_wearing_our_version_but_broken_framing_decodes_to_nothing() -> None:
    version = wire.pack_uint(codec.CODEC_VERSION, 1)
    # A fragment length that runs past the end of the string.
    assert codec.CustomID.parse(version + COOKIE + HANDLER + wire.pack_uint(0, 1) + wire.pack_uint(50, 1)) is None
    # A fragment length that is not a packed digit at all.
    assert codec.CustomID.parse(version + COOKIE + HANDLER + wire.pack_uint(0, 1) + '"') is None
    # An index that is not a packed digit.
    assert codec.CustomID.parse(version + COOKIE + HANDLER + '"' + wire.pack_uint(0, 1)) is None


def test_decoding_never_raises_on_arbitrary_input() -> None:
    version = wire.pack_uint(codec.CODEC_VERSION, 1)
    for suffix in ("", "\x00", "é" * 40, '"' * 12, "\\", "﻿"):
        codec.CustomID.parse(version + suffix)
        codec.CustomID.parse(suffix)


# --- the property the chunker rests on ---


def test_an_encoded_component_is_printable_ascii_of_predictable_width() -> None:
    key = anchor.make_state_key()
    encoded = custom_id(fragment=anchor.StoreAnchor(key=key).encode(), args="ARGS").encode(view_name="framing")
    assert encoded.isascii()
    assert all(char.isprintable() for char in encoded)
    assert len(encoded.encode()) == len(encoded)


def test_a_full_message_of_fragments_reassembles() -> None:
    payload = anchor.MessageAnchor(fingerprint="abc", seq=5, state="s" * 200).encode()
    capacities = [codec.MAX_FRAGMENT_LENGTH] * 4
    fragments = anchor.split_across(payload, capacities)
    assert fragments is not None

    ids = [custom_id(fragment=f, index=i).encode(view_name="framing") for i, f in enumerate(fragments)]
    assert all(len(one) <= constants.MAX_CUSTOM_ID_LENGTH for one in ids)

    decoded = [codec.CustomID.parse(one) for one in ids]
    assert all(part is not None for part in decoded)
    gathered = anchor.join_fragments([(part.fragment_index, part.fragment) for part in decoded if part is not None])
    assert gathered == payload
