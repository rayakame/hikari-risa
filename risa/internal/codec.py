from __future__ import annotations

import abc
import typing

import msgspec

from risa import errors
from risa.internal import constants
from risa.internal import wire

if typing.TYPE_CHECKING:
    import collections.abc
    import enum

__all__ = (
    "COOKIE_LENGTH",
    "FRAGMENT_INDEX_WIDTH",
    "FRAGMENT_LEN_WIDTH",
    "HANDLER_LENGTH",
    "HEADER_LENGTH",
    "MAX_FRAGMENT_LENGTH",
    "VERSION",
    "ArgConverter",
    "BoolConverter",
    "CustomID",
    "EnumConverter",
    "IntConverter",
    "StrConverter",
    "make_cookie",
    "make_handler_token",
    "pack_frames",
    "parse_custom_id",
    "unpack_frames",
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


class ArgConverter(abc.ABC):
    __slots__ = ()

    @property
    @abc.abstractmethod
    def type_id(self) -> str: ...

    @abc.abstractmethod
    def encode(self, value: object) -> str: ...

    @abc.abstractmethod
    def decode(self, raw: str) -> object | None: ...


class IntConverter(ArgConverter):
    __slots__ = ("_target",)

    def __init__(self, target: collections.abc.Callable[[int], object]) -> None:
        self._target = target

    @property
    @typing.override
    def type_id(self) -> str:
        return "i"

    @typing.override
    def encode(self, value: object) -> str:
        if type(value) is bool or not isinstance(value, int):
            msg = f"expected an int, got {type(value).__name__}"
            raise TypeError(msg)
        data = value.to_bytes((value.bit_length() + 8) // 8, "little", signed=True)
        return wire.pack_bytes(data)

    @typing.override
    def decode(self, raw: str) -> object | None:
        data = wire.unpack_bytes(raw)
        if data is None:
            return None
        return self._target(int.from_bytes(data, "little", signed=True))


class StrConverter(ArgConverter):
    __slots__ = ()

    @property
    @typing.override
    def type_id(self) -> str:
        return "s"

    @typing.override
    def encode(self, value: object) -> str:
        if not isinstance(value, str):
            msg = f"expected a str, got {type(value).__name__}"
            raise TypeError(msg)
        return wire.pack_bytes(value.encode())

    @typing.override
    def decode(self, raw: str) -> object | None:
        data = wire.unpack_bytes(raw)
        if data is None:
            return None
        try:
            return data.decode()
        except UnicodeDecodeError:
            return None


class BoolConverter(ArgConverter):
    __slots__ = ()

    @property
    @typing.override
    def type_id(self) -> str:
        return "b"

    @typing.override
    def encode(self, value: object) -> str:
        if type(value) is not bool:
            msg = f"expected a bool, got {type(value).__name__}"
            raise TypeError(msg)
        return wire.pack_uint(int(value), 1)

    @typing.override
    def decode(self, raw: str) -> object | None:
        if len(raw) != 1:
            return None
        number = wire.unpack_uint(raw)
        if number is None or number > 1:
            return None
        return number == 1


class EnumConverter(ArgConverter):
    __slots__ = ("_enum", "_inner", "_type_id")

    def __init__(self, enum_cls: type[enum.Enum], inner: ArgConverter, type_id: str) -> None:
        self._enum = enum_cls
        self._inner = inner
        self._type_id = type_id

    @property
    @typing.override
    def type_id(self) -> str:
        return self._type_id

    @typing.override
    def encode(self, value: object) -> str:
        if not isinstance(value, self._enum):
            msg = f"expected {self._enum.__name__}, got {type(value).__name__}"
            raise TypeError(msg)
        return self._inner.encode(value.value)

    @typing.override
    def decode(self, raw: str) -> object | None:
        inner = self._inner.decode(raw)
        if inner is None:
            return None
        try:
            return self._enum(inner)
        except ValueError:
            return None


def pack_frames(parts: collections.abc.Sequence[str]) -> str:
    return "".join(wire.pack_uint(len(part), 1) + part for part in parts)


def unpack_frames(raw: str) -> list[str] | None:
    parts: list[str] = []
    position = 0
    while position < len(raw):
        length = wire.unpack_uint(raw[position])
        if length is None:
            return None
        start = position + 1
        end = start + length
        if end > len(raw):
            return None
        parts.append(raw[start:end])
        position = end
    return parts
