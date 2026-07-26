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

import json
import typing
import unicodedata

import pytest

from risa.internal import wire


@pytest.mark.parametrize("width", [1, 2, 3])
def test_packing_round_trips_across_the_whole_range(width: int) -> None:
    top = wire.largest_value(width)
    for value in (0, 1, top // 2, top - 1, top):
        packed = wire.pack_uint(value, width)
        assert len(packed) == width
        assert wire.unpack_uint(packed) == value


def test_packing_is_ordered_so_digits_read_most_significant_first() -> None:
    assert wire.pack_uint(0, 2) < wire.pack_uint(1, 2) < wire.pack_uint(wire.ALPHABET_SIZE, 2)


def test_packing_rejects_a_value_too_large_for_its_width() -> None:
    with pytest.raises(ValueError, match="does not fit"):
        wire.pack_uint(wire.ALPHABET_SIZE, 1)


def test_packing_rejects_a_negative_value() -> None:
    with pytest.raises(ValueError, match="negative"):
        wire.pack_uint(-1, 2)


@pytest.mark.parametrize("raw", ['"', "\\", " ", "\x00", "é", "ab\x01"])
def test_unpacking_fails_soft_on_anything_not_a_packed_digit(raw: str) -> None:
    assert wire.unpack_uint(raw) is None


@pytest.mark.parametrize("size", [0, 1, 2, 3, 4, 5, 7, 8, 16, 64, 255])
def test_pack_bytesing_round_trips_at_every_length(size: int) -> None:
    data = bytes(range(256))[:size] if size <= 256 else bytes(size)
    assert wire.unpack_bytes(wire.pack_bytes(data)) == data


def test_pack_bytesing_round_trips_bytes_that_would_break_a_text_channel() -> None:
    data = b"\x00\x1f\x80\xff" * 8
    assert wire.unpack_bytes(wire.pack_bytes(data)) == data


@pytest.mark.parametrize("raw", ['"', "\\", "é", " ", "ab c"])
def test_unpack_bytesing_fails_soft_on_foreign_characters(raw: str) -> None:
    assert wire.unpack_bytes(raw) is None


def test_every_written_character_is_printable_ascii() -> None:
    written = wire.pack_bytes(bytes(range(256))) + "".join(wire.pack_uint(n, 1) for n in range(wire.ALPHABET_SIZE))
    assert written.isascii()
    assert all(char.isprintable() for char in written)


def test_one_written_character_is_always_one_byte_however_it_is_counted() -> None:
    # The property the whole chunker rests on: capacity cannot depend on values.
    written = wire.pack_bytes(bytes(range(256))) + "".join(wire.pack_uint(n, 1) for n in range(wire.ALPHABET_SIZE))
    assert len(written.encode()) == len(written)
    assert len(json.dumps(written)) == len(written) + 2


def test_written_characters_survive_a_json_round_trip_unchanged() -> None:
    written = wire.pack_bytes(bytes(range(256)))
    assert json.loads(json.dumps(written)) == written


@pytest.mark.parametrize("form", ["NFC", "NFD", "NFKC", "NFKD"])
def test_written_characters_are_unchanged_by_every_normalisation_form(
    form: typing.Literal["NFC", "NFD", "NFKC", "NFKD"],
) -> None:
    # Discord stores a custom_id verbatim and normalises nothing, so two ids that
    # look alike can differ; risa's own output must at least never be the thing
    # that differs.
    written = wire.pack_bytes(bytes(range(256))) + "".join(wire.pack_uint(n, 1) for n in range(wire.ALPHABET_SIZE))
    assert unicodedata.normalize(form, written) == written


@pytest.mark.parametrize("data", [b"", b"\x00" * 32, b"\xff" * 32, bytes(range(256))])
def test_pack_bytesing_round_trips_the_byte_values_most_likely_to_break_something(data: bytes) -> None:
    assert wire.unpack_bytes(wire.pack_bytes(data)) == data
