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

from risa.state import serde


def test_an_envelope_survives_the_round_trip() -> None:
    envelope = serde.Envelope(v=1, n="poll", f="G%t", d=b"payload")

    assert serde.unpack_entry(serde.pack_entry(envelope)) == envelope


@pytest.mark.parametrize("data", [b"", b"\x00", b"\xff\xfe binary \x00 soup"])
def test_the_payload_comes_back_byte_identical(data: bytes) -> None:
    envelope = serde.Envelope(v=3, n="board", f="ttH", d=data)

    unpacked = serde.unpack_entry(serde.pack_entry(envelope))

    assert unpacked is not None
    assert unpacked.d == data


@pytest.mark.parametrize("raw", [b"", b"\x00\x01 not msgpack", b"\xc1"])
def test_garbage_decodes_to_nothing(raw: bytes) -> None:
    assert serde.unpack_entry(raw) is None


def test_a_truncated_record_decodes_to_nothing() -> None:
    raw = serde.pack_entry(serde.Envelope(v=1, n="poll", f="G%t", d=b"payload"))

    assert serde.unpack_entry(raw[:-3]) is None


def test_a_record_missing_a_field_decodes_to_nothing() -> None:
    raw = msgspec.msgpack.encode({"v": 1, "n": "poll", "d": b"p"})

    assert serde.unpack_entry(raw) is None


def test_a_record_with_a_wrong_typed_field_decodes_to_nothing() -> None:
    raw = msgspec.msgpack.encode({"v": "1", "n": "poll", "f": "G%t", "d": b"p"})

    assert serde.unpack_entry(raw) is None


def test_a_record_from_a_newer_risa_still_decodes() -> None:
    raw = msgspec.msgpack.encode({"v": 1, "n": "poll", "f": "G%t", "d": b"p", "future": 9})

    assert serde.unpack_entry(raw) == serde.Envelope(v=1, n="poll", f="G%t", d=b"p")


def test_an_envelope_cannot_be_edited() -> None:
    envelope = serde.Envelope(v=1, n="poll", f="G%t", d=b"p")

    with pytest.raises(AttributeError):
        envelope.v = 2  # type: ignore[misc]
