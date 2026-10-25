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

import secrets
import typing

import msgspec

from risa import errors
from risa.internal import codec
from risa.internal import wire

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import codecs

__all__ = (
    "KEY_BYTES",
    "MAX_SEQ",
    "MAX_TOTAL",
    "MESSAGE_HEADER_LENGTH",
    "PLACEMENT_MESSAGE",
    "PLACEMENT_STORE",
    "SCHEMA_FP_LENGTH",
    "SEQ_WIDTH",
    "STORE_ANCHOR_LENGTH",
    "TOTAL_WIDTH",
    "MessageAnchor",
    "StoreAnchor",
    "carve",
    "gather",
    "parse",
)


PLACEMENT_MESSAGE: typing.Final[bytes] = b"m"
PLACEMENT_STORE: typing.Final[bytes] = b"s"

TOTAL_WIDTH: typing.Final[int] = 2
SCHEMA_FP_LENGTH: typing.Final[int] = 3
SEQ_WIDTH: typing.Final[int] = 3
MESSAGE_HEADER_LENGTH: typing.Final[int] = 1 + TOTAL_WIDTH + SCHEMA_FP_LENGTH + SEQ_WIDTH  # 9
MAX_TOTAL: typing.Final[int] = 256**TOTAL_WIDTH - 1
MAX_SEQ: typing.Final[int] = 256**SEQ_WIDTH - 1

KEY_BYTES: typing.Final[int] = 12  # 96 bits, §7.6
STORE_ANCHOR_LENGTH: typing.Final[int] = 1 + KEY_BYTES


class MessageAnchor(msgspec.Struct, frozen=True):
    schema_fp: str
    seq: int
    payload: bytes

    @classmethod
    def parse(cls, raw: bytes) -> typing.Self | None:
        if len(raw) < MESSAGE_HEADER_LENGTH:
            return None
        if int.from_bytes(raw[1 : 1 + TOTAL_WIDTH], "big") != len(raw):
            return None
        fp_end = 1 + TOTAL_WIDTH + SCHEMA_FP_LENGTH
        try:
            schema_fp = raw[1 + TOTAL_WIDTH : fp_end].decode("ascii")
        except UnicodeDecodeError:
            return None
        seq = int.from_bytes(raw[fp_end : fp_end + SEQ_WIDTH], "big")
        return cls(schema_fp=schema_fp, seq=seq, payload=raw[MESSAGE_HEADER_LENGTH:])

    def pack(self) -> bytes:
        if len(self.schema_fp) != SCHEMA_FP_LENGTH or not self.schema_fp.isascii():
            msg = f"schema fingerprint must be exactly {SCHEMA_FP_LENGTH} ASCII characters, got {self.schema_fp!r}"
            raise ValueError(msg)
        if not 0 <= self.seq <= MAX_SEQ:
            msg = f"sequence counter {self.seq} does not fit in {SEQ_WIDTH} byte(s)"
            raise ValueError(msg)
        total = MESSAGE_HEADER_LENGTH + len(self.payload)
        if total > MAX_TOTAL:
            msg = f"an anchor of {total} bytes does not fit in {TOTAL_WIDTH} length byte(s)"
            raise ValueError(msg)
        return b"".join(
            (
                PLACEMENT_MESSAGE,
                total.to_bytes(TOTAL_WIDTH, "big"),
                self.schema_fp.encode("ascii"),
                self.seq.to_bytes(SEQ_WIDTH, "big"),
                self.payload,
            )
        )


class StoreAnchor(msgspec.Struct, frozen=True):
    key: bytes

    @classmethod
    def mint(cls) -> typing.Self:
        return cls(secrets.token_bytes(KEY_BYTES))

    @classmethod
    def parse(cls, raw: bytes) -> typing.Self | None:
        if len(raw) != STORE_ANCHOR_LENGTH:
            return None
        return cls(key=raw[1:])

    @property
    def token(self) -> str:
        return wire.pack_bytes(self.key)

    def pack(self) -> bytes:
        if len(self.key) != KEY_BYTES:
            msg = f"a store key is exactly {KEY_BYTES} bytes, got {len(self.key)}"
            raise ValueError(msg)
        return PLACEMENT_STORE + self.key


def carve(
    anchor: bytes,
    capacities: collections.abc.Sequence[int],
    *,
    wire_codec: codecs.Codec,
    view_name: str,
) -> collections.abc.Sequence[str]:
    fragments: list[str] = []
    position = 0
    usable = 0
    for capacity in capacities[: codec.MAX_FRAGMENTS]:
        room = wire_codec.max_bytes(max(0, min(capacity, codec.MAX_FRAGMENT_LENGTH)))
        usable += room
        take = min(room, len(anchor) - position)
        fragments.append(wire_codec.encode(anchor[position : position + take]) if take else "")
        position += take
    if position < len(anchor):
        raise errors.StateOverflowError(
            view_name,
            needed=len(anchor),
            available=usable,
            slots=len(fragments),
            codec_name=wire_codec.name,
        )
    fragments.extend("" for _ in capacities[codec.MAX_FRAGMENTS :])
    return fragments


def gather(fragments: collections.abc.Mapping[int, bytes]) -> bytes | None:
    if 0 not in fragments:
        return None
    parts: list[bytes] = []
    index = 0
    while index in fragments:
        parts.append(fragments[index])
        index += 1
    return b"".join(parts)


def parse(raw: bytes) -> MessageAnchor | StoreAnchor | None:
    head = raw[:1]
    if head == PLACEMENT_MESSAGE:
        return MessageAnchor.parse(raw)
    if head == PLACEMENT_STORE:
        return StoreAnchor.parse(raw)
    return None
