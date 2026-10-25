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

import typing

import msgspec

__all__ = (
    "Envelope",
    "pack_entry",
    "unpack_entry",
)


class Envelope(msgspec.Struct, frozen=True):
    v: int
    n: str
    f: str
    d: bytes


_ENCODER: typing.Final[msgspec.msgpack.Encoder] = msgspec.msgpack.Encoder()
_DECODER: typing.Final[msgspec.msgpack.Decoder[Envelope]] = msgspec.msgpack.Decoder(Envelope)


def pack_entry(envelope: Envelope) -> bytes:
    return _ENCODER.encode(envelope)


def unpack_entry(raw: bytes) -> Envelope | None:
    try:
        return _DECODER.decode(raw)
    except msgspec.DecodeError:
        return None
