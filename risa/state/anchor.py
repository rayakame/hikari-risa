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
"""The anchor: the bytes a message carries for its view's state.

An anchor comes in three dialects, told apart by its first byte. A message anchor
carries the state itself, `m | total:2 | schema fingerprint:3 | seq:3 | payload`; a
store anchor carries only the key of a store record, `s | key:12`; a taken-over anchor,
`t`, carries nothing, since the key of its record derives from the message. The anchor
is cut into fragments, one per interactive component in render order, each written into
that component's `custom_id`.
"""

from __future__ import annotations

import hashlib
import secrets
import typing

import msgspec

from risa import errors
from risa.internal import codec

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
    "PLACEMENT_TAKEN_OVER",
    "SCHEMA_FP_LENGTH",
    "SEQ_WIDTH",
    "STORE_ANCHOR_LENGTH",
    "TOTAL_WIDTH",
    "MessageAnchor",
    "Slot",
    "StoreAnchor",
    "TakenOverAnchor",
    "carve",
    "distribute",
    "gather",
    "parse",
)


PLACEMENT_MESSAGE: typing.Final[bytes] = b"m"
PLACEMENT_STORE: typing.Final[bytes] = b"s"
PLACEMENT_TAKEN_OVER: typing.Final[bytes] = b"t"

TOTAL_WIDTH: typing.Final[int] = 2
SCHEMA_FP_LENGTH: typing.Final[int] = 3
SEQ_WIDTH: typing.Final[int] = 3
MESSAGE_HEADER_LENGTH: typing.Final[int] = 1 + TOTAL_WIDTH + SCHEMA_FP_LENGTH + SEQ_WIDTH
MAX_TOTAL: typing.Final[int] = 256**TOTAL_WIDTH - 1
MAX_SEQ: typing.Final[int] = 256**SEQ_WIDTH - 1

KEY_BYTES: typing.Final[int] = 12  # 96 bits
STORE_ANCHOR_LENGTH: typing.Final[int] = 1 + KEY_BYTES


class Slot(msgspec.Struct, frozen=True):
    """What one interactive component carries of the anchor.

    Attributes
    ----------
    index
        Which fragment it is.
    fragment
        The fragment, encoded.
    """

    index: int
    fragment: str


class MessageAnchor(msgspec.Struct, frozen=True):
    """An anchor that carries the state itself.

    Attributes
    ----------
    schema_fp
        The fingerprint of the durable fields that wrote `payload`.
    seq
        A counter bumped on every commit, which tells a fresher cached commit from the
        message a click arrived with.
    payload
        The packed durable fields.
    """

    schema_fp: str
    seq: int
    payload: bytes

    @classmethod
    def parse(cls, raw: bytes) -> typing.Self | None:
        """Read a message anchor, or return `None` if its length or header does not add up."""
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
        """Write the anchor out, header first.

        Returns
        -------
        bytes
            The anchor.

        Raises
        ------
        ValueError
            If a header field does not fit its width.
        """
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
    """An anchor that carries the key of a store record.

    Attributes
    ----------
    key
        96 random bits. Every interactive component carries the whole anchor, so each one
        can locate the record on its own.
    """

    key: bytes

    @classmethod
    def mint(cls) -> typing.Self:
        """Return a store anchor with a fresh random key."""
        return cls(secrets.token_bytes(KEY_BYTES))

    @classmethod
    def parse(cls, raw: bytes) -> typing.Self | None:
        """Read a store anchor, or return `None` if it has the wrong length."""
        if len(raw) != STORE_ANCHOR_LENGTH:
            return None
        return cls(key=raw[1:])

    @property
    def token(self) -> str:
        """The key as hex, the form store keys are built from."""
        return self.key.hex()

    def pack(self) -> bytes:
        """Write the anchor out, raising `ValueError` for a key of the wrong length."""
        if len(self.key) != KEY_BYTES:
            msg = f"a store key is exactly {KEY_BYTES} bytes, got {len(self.key)}"
            raise ValueError(msg)
        return PLACEMENT_STORE + self.key


class TakenOverAnchor(msgspec.Struct, frozen=True):
    """An anchor of a message whose state moved into a store when its view took it over.

    It carries no key: the key derives from the message id Discord delivers with a click,
    so every click on the message finds the same record, one that still carries the
    message's own state too, and a `custom_id` cannot point at another message's record.
    """

    @staticmethod
    def token(message_id: int, cookie: str) -> str:
        """Return the key of the record a message's view `cookie` keeps, led by a `t` no `StoreAnchor` token has."""
        return "t" + hashlib.blake2b(f"{message_id}:{cookie}".encode(), digest_size=KEY_BYTES).hexdigest()

    def pack(self) -> bytes:  # ruff:ignore[no-self-use]
        """Write the anchor out."""
        return PLACEMENT_TAKEN_OVER


def carve(
    anchor: bytes,
    capacities: collections.abc.Sequence[int],
    *,
    wire_codec: codecs.Codec,
    view_name: str,
) -> collections.abc.Sequence[str]:
    """Cut `anchor` into one encoded fragment per interactive component, in render order.

    Each component takes as many bytes as its spare characters hold under `wire_codec`.
    Only the first `MAX_FRAGMENTS` components can carry a fragment; the rest get empty
    ones here, and `distribute` parks them.

    Returns
    -------
    Sequence[str]
        One fragment per capacity.

    Raises
    ------
    StateOverflowError
        If the components cannot carry the whole anchor.
    """
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


def distribute(fragments: collections.abc.Sequence[str]) -> collections.abc.Sequence[Slot]:
    """Assign each component its slot.

    A component past the last fragment index is parked on that index with a copy of its
    fragment, since an index is one character and cannot count further.

    Returns
    -------
    Sequence[Slot]
        One slot per fragment, in render order.
    """
    last = codec.MAX_FRAGMENTS - 1
    return [
        Slot(index=index, fragment=fragment) if index <= last else Slot(index=last, fragment=fragments[last])
        for index, fragment in enumerate(fragments)
    ]


def gather(fragments: collections.abc.Mapping[int, bytes]) -> bytes | None:
    """Join fragments from index 0 up to the first gap, or return `None` without fragment 0."""
    if 0 not in fragments:
        return None
    parts: list[bytes] = []
    index = 0
    while index in fragments:
        parts.append(fragments[index])
        index += 1
    return b"".join(parts)


def parse(raw: bytes) -> MessageAnchor | StoreAnchor | TakenOverAnchor | None:
    """Read an anchor of any dialect, or return `None` for one of none."""
    head = raw[:1]
    if head == PLACEMENT_MESSAGE:
        return MessageAnchor.parse(raw)
    if head == PLACEMENT_STORE:
        return StoreAnchor.parse(raw)
    if raw == PLACEMENT_TAKEN_OVER:
        return TakenOverAnchor()
    return None
