from __future__ import annotations

import abc
import enum
import inspect
import re
import typing

import hikari
import linkd
import msgspec

from risa import errors
from risa.internal import constants
from risa.internal import wire

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "COOKIE_LENGTH",
    "FINGERPRINT_LENGTH",
    "FRAGMENT_INDEX_WIDTH",
    "FRAGMENT_LEN_WIDTH",
    "HANDLER_LENGTH",
    "HEADER_LENGTH",
    "MAX_FRAGMENT_LENGTH",
    "MAX_FRAME_LENGTH",
    "VERSION",
    "ArgConverter",
    "BoolConverter",
    "CustomID",
    "EnumConverter",
    "HandlerSignature",
    "IntConverter",
    "StrConverter",
    "make_cookie",
    "make_handler_token",
    "pack_frames",
    "parse_custom_id",
    "resolve_converter",
    "resolve_signature",
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
MAX_FRAME_LENGTH: typing.Final[int] = wire.ALPHABET_SIZE - 1
FINGERPRINT_LENGTH: typing.Final[int] = 2


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


def _enum_converter(enum_cls: type[enum.Enum]) -> EnumConverter:
    value_types: set[type[object]] = {type(member.value) for member in enum_cls}
    if all(t is str for t in value_types) and value_types:
        return EnumConverter(enum_cls, StrConverter(), "es")
    if all(t is not bool and issubclass(t, int) for t in value_types) and value_types:
        return EnumConverter(enum_cls, IntConverter(int), "ei")
    msg = "enum values must be all int or all str"
    raise ValueError(msg)


def resolve_converter(annotation: object) -> ArgConverter | None:
    if annotation is bool:
        return BoolConverter()
    if annotation is int:
        return IntConverter(int)
    if annotation is hikari.Snowflake:
        return IntConverter(hikari.Snowflake)
    if annotation is str:
        return StrConverter()
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return _enum_converter(annotation)
    return None


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
        if not raw:
            return None
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


class HandlerSignature(msgspec.Struct, frozen=True):
    converters: collections.abc.Mapping[str, ArgConverter]
    required: int
    fingerprint: str


def _find_unresolvable_parameter(func: collections.abc.Callable[..., object], missing: str) -> str:
    annotations: dict[str, object] = getattr(func, "__annotations__", {})
    pattern = re.compile(rf"\b{re.escape(missing)}\b")
    for name, raw in annotations.items():
        if name != "return" and isinstance(raw, str) and pattern.search(raw):
            return name
    return missing


def _resolve_hints(func: collections.abc.Callable[..., object]) -> dict[str, object]:
    try:
        return typing.get_type_hints(func)
    except NameError as exc:
        missing = exc.name or "?"
        reason = (
            f"has annotation {missing!r}, which is not importable at runtime; annotations on"
            " handler parameters must be imported outside TYPE_CHECKING"
        )
        raise errors.HandlerSignatureError(
            func.__qualname__,
            _find_unresolvable_parameter(func, missing),
            reason,
        ) from exc


def resolve_signature(func: collections.abc.Callable[..., object]) -> HandlerSignature:
    hints = _resolve_hints(func)
    params = list(inspect.signature(func).parameters.values())[2:]
    converters: dict[str, ArgConverter] = {}
    required_count = 0

    di_flag = False
    for param in params:
        if param.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            di_flag = True
            continue
        if param.kind is inspect.Parameter.KEYWORD_ONLY:
            di_flag = True
        if param.name not in hints:
            di_flag = True
            continue
        if param.default is linkd.INJECTED:
            di_flag = True
            continue
        try:
            converter = resolve_converter(hints[param.name])
        except ValueError as exc:
            raise errors.HandlerSignatureError(func.__qualname__, param.name, str(exc)) from exc

        if converter is None:
            di_flag = True
            continue
        if di_flag:
            raise errors.HandlerSignatureError(
                func.__qualname__,
                param.name,
                "is converter-typed but sits after the wire-argument section; "
                "move it before the first injected parameter, or wrap it in its own type",
            )

        if param.default is inspect.Parameter.empty:
            required_count += 1

        converters[param.name] = converter
    fingerprint = wire.pack_digest("".join(c.type_id for c in converters.values()), chars=FINGERPRINT_LENGTH)
    return HandlerSignature(converters=converters, required=required_count, fingerprint=fingerprint)
