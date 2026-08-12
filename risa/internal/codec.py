from __future__ import annotations

import typing

import msgspec

from risa import errors
from risa.internal import constants
from risa.internal import wire

__all__ = (
    "COOKIE_LENGTH",
    "FRAGMENT_INDEX_WIDTH",
    "FRAGMENT_LEN_WIDTH",
    "HANDLER_LENGTH",
    "HEADER_LENGTH",
    "MAX_FRAGMENT_LENGTH",
    "VERSION",
    "CustomID",
    "make_cookie",
    "make_handler_token",
    "parse_custom_id",
)

VERSION: typing.Final[str] = "1"

COOKIE_LENGTH: typing.Final[int] = 6
HANDLER_LENGTH: typing.Final[int] = 2
FRAGMENT_INDEX_WIDTH: typing.Final[int] = 1
FRAGMENT_LEN_WIDTH: typing.Final[int] = 1
HEADER_LENGTH: typing.Final[int] = (
    len(VERSION) + COOKIE_LENGTH + HANDLER_LENGTH + FRAGMENT_INDEX_WIDTH + FRAGMENT_LEN_WIDTH
)
MAX_FRAGMENT_LENGTH: typing.Final[int] = constants.MAX_CUSTOM_ID_LENGTH - HEADER_LENGTH


def make_cookie(name: str, version: int) -> str:
    return wire.pack_digest(f"{name}:{version}", chars=COOKIE_LENGTH)


def make_handler_token(handler_id: str, version: int) -> str:
    return wire.pack_digest(f"{handler_id}:{version}", chars=HANDLER_LENGTH)


class CustomID(msgspec.Struct, frozen=True):
    cookie: str
    handler: str
    fragment_index: int
    fragment: str
    tail: str

    def encode(self) -> str:
        if len(self.cookie) != COOKIE_LENGTH:
            msg = f"cookie must be exactly {COOKIE_LENGTH} characters, got {len(self.cookie)}"
            raise ValueError(msg)
        if len(self.handler) != HANDLER_LENGTH:
            msg = f"handler token must be exactly {HANDLER_LENGTH} characters, got {len(self.handler)}"
            raise ValueError(msg)

        encoded = "".join(
            (
                VERSION,
                self.cookie,
                self.handler,
                wire.pack_uint(self.fragment_index, FRAGMENT_INDEX_WIDTH),
                wire.pack_uint(len(self.fragment), FRAGMENT_LEN_WIDTH),
                self.fragment,
                self.tail,
            ),
        )
        if len(encoded) > constants.MAX_CUSTOM_ID_LENGTH:
            raise errors.CustomIdOverflowError(view_name=f"cookie: {self.cookie}", length=len(encoded))

        return encoded


def parse_custom_id(raw: str) -> CustomID | None:
    if not (HEADER_LENGTH <= len(raw) <= constants.MAX_CUSTOM_ID_LENGTH):
        return None
    if raw[0] != VERSION:
        return None

    cookie_end = 1 + COOKIE_LENGTH
    handler_end = cookie_end + HANDLER_LENGTH
    index_end = handler_end + FRAGMENT_INDEX_WIDTH
    if (fragment_index := wire.unpack_uint(raw[handler_end:index_end])) is None:
        return None
    if (fragment_length := wire.unpack_uint(raw[index_end : index_end + FRAGMENT_LEN_WIDTH])) is None:
        return None
    if HEADER_LENGTH + fragment_length > len(raw):
        return None

    return CustomID(
        cookie=raw[1:cookie_end],
        handler=raw[cookie_end:handler_end],
        fragment_index=fragment_index,
        fragment=raw[HEADER_LENGTH : HEADER_LENGTH + fragment_length],
        tail=raw[HEADER_LENGTH + fragment_length :],
    )
