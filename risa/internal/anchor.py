from __future__ import annotations

import secrets
import typing

import msgspec

from risa import errors
from risa.internal import codec
from risa.internal import wire

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "KEY_BYTES",
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
    "pack_payload",
    "parse",
    "unpack_payload",
)


PLACEMENT_MESSAGE: typing.Final[str] = "m"
PLACEMENT_STORE: typing.Final[str] = "s"

TOTAL_WIDTH: typing.Final[int] = 2
SCHEMA_FP_LENGTH: typing.Final[int] = 3
SEQ_WIDTH: typing.Final[int] = 3
MESSAGE_HEADER_LENGTH: typing.Final[int] = 1 + TOTAL_WIDTH + SCHEMA_FP_LENGTH + SEQ_WIDTH  # 9

KEY_BYTES: typing.Final[int] = 12  # 96 bits, §7.6
STORE_ANCHOR_LENGTH: typing.Final[int] = 1 + len(wire.pack_bytes(bytes(KEY_BYTES)))  # 16


class MessageAnchor(msgspec.Struct, frozen=True):
    schema_fp: str
    seq: int
    payload: str

    @classmethod
    def parse(cls, raw: str) -> typing.Self | None:
        if len(raw) < MESSAGE_HEADER_LENGTH:
            return None
        total = wire.unpack_uint(raw[1 : 1 + TOTAL_WIDTH])
        if total != len(raw):  # None != int, so a bad digit is covered too
            return None
        fp_end = 1 + TOTAL_WIDTH + SCHEMA_FP_LENGTH
        seq = wire.unpack_uint(raw[fp_end : fp_end + SEQ_WIDTH])
        if seq is None:
            return None
        return cls(schema_fp=raw[1 + TOTAL_WIDTH : fp_end], seq=seq, payload=raw[MESSAGE_HEADER_LENGTH:])

    def pack(self) -> str:
        if len(self.schema_fp) != SCHEMA_FP_LENGTH:
            msg = f"schema fingerprint must be exactly {SCHEMA_FP_LENGTH} characters, got {len(self.schema_fp)}"
            raise ValueError(msg)
        total = MESSAGE_HEADER_LENGTH + len(self.payload)
        return "".join(
            (
                PLACEMENT_MESSAGE,
                wire.pack_uint(total, TOTAL_WIDTH),
                self.schema_fp,
                wire.pack_uint(self.seq, SEQ_WIDTH),
                self.payload,
            )
        )


class StoreAnchor(msgspec.Struct, frozen=True):
    key: str

    @classmethod
    def mint(cls) -> typing.Self:
        return cls(wire.pack_bytes(secrets.token_bytes(KEY_BYTES)))

    @classmethod
    def parse(cls, raw: str) -> typing.Self | None:
        if len(raw) != STORE_ANCHOR_LENGTH:
            return None
        key = raw[1:]
        if wire.unpack_bytes(key) is None:
            return None
        return cls(key=key)

    def pack(self) -> str:
        return PLACEMENT_STORE + self.key


def pack_payload(values: collections.abc.Sequence[object]) -> str:
    return wire.pack_bytes(msgspec.msgpack.encode(values))


def carve(
    anchor: str,
    capacities: collections.abc.Sequence[int],
    *,
    view_name: str,
) -> collections.abc.Sequence[str]:
    fragments: list[str] = []
    position = 0
    usable = 0
    for capacity in capacities[: codec.MAX_FRAGMENTS]:
        room = max(0, min(capacity, codec.MAX_FRAGMENT_LENGTH))
        usable += room
        take = min(room, len(anchor) - position)
        fragments.append(anchor[position : position + take])
        position += take
    if position < len(anchor):
        raise errors.StateOverflowError(view_name, needed=len(anchor), available=usable, slots=len(fragments))
    fragments.extend("" for _ in capacities[codec.MAX_FRAGMENTS :])
    return fragments


def gather(fragments: collections.abc.Mapping[int, str]) -> str | None:
    if 0 not in fragments:
        return None
    parts: list[str] = []
    index = 0
    while index in fragments:
        parts.append(fragments[index])
        index += 1
    return "".join(parts)


def parse(raw: str) -> MessageAnchor | StoreAnchor | None:
    if not raw:
        return None
    if raw[0] == PLACEMENT_MESSAGE:
        return MessageAnchor.parse(raw)
    if raw[0] == PLACEMENT_STORE:
        return StoreAnchor.parse(raw)
    return None


def unpack_payload(payload: str) -> list[object] | None:
    data = wire.unpack_bytes(payload)
    if data is None:
        return None
    try:
        decoded = msgspec.msgpack.decode(data, type=list[object])
    except Exception:  # ruff:ignore[blind-except]  msgspec raises several types on forged bytes
        return None
    return decoded
