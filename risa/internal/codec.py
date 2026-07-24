# Copyright (c) 2025-present Rayakame
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
"""Encoding and decoding of component ``custom_id`` strings.

A ``custom_id`` is the only routing channel Discord returns on a component
interaction, so risa packs everything it needs to dispatch into one. The layout
is a fixed-width header followed by an opaque payload::

    [version:1][cookie:6][handler:2][payload:...]

"""

from __future__ import annotations

import abc
import base64
import enum
import hashlib
import secrets
import typing

import msgspec

from risa import errors as errors_
from risa.internal import constants

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "CODEC_VERSION",
    "COOKIE_LENGTH",
    "FINGERPRINT_LENGTH",
    "HANDLER_LENGTH",
    "STATE_KEY_LENGTH",
    "ArgConverter",
    "ArgSpec",
    "CustomID",
    "HandlerSignature",
    "decode_args",
    "decode_custom_id",
    "encode_args",
    "encode_custom_id",
    "make_cookie",
    "make_handler_token",
    "make_signature_fingerprint",
    "make_state_key",
    "resolve_converter",
)

CODEC_VERSION: typing.Final[int] = 1

_VERSION_LENGTH: typing.Final[int] = 1

# Public because the cookie/handler-token generators must produce exactly these
# widths for decode's fixed slicing to line up. Single source of truth.
COOKIE_LENGTH: typing.Final[int] = 6
HANDLER_LENGTH: typing.Final[int] = 2

_COOKIE_START: typing.Final[int] = _VERSION_LENGTH
_HANDLER_START: typing.Final[int] = _COOKIE_START + COOKIE_LENGTH
_HEADER_LENGTH: typing.Final[int] = _HANDLER_START + HANDLER_LENGTH

_STATE_KEY_BYTES: typing.Final[int] = 12

STATE_KEY_LENGTH: typing.Final[int] = 16
"""Width of a state key, which is what :data:`_STATE_KEY_BYTES` encodes to."""

FINGERPRINT_LENGTH: typing.Final[int] = 2
"""Width of the signature fingerprint carried by components that carry args."""

# The largest arg a one-char length prefix can frame.
_MAX_FRAME_LENGTH: typing.Final[int] = 255


class CustomID(msgspec.Struct, frozen=True):
    """The decoded parts of a component ``custom_id``.

    Attributes
    ----------
    version
        The codec version the string was encoded with. Populated from the wire
        by :func:`decode_custom_id`, which rejects any other value.
    raw_cookie
        The encoded view cookie, exactly ``6`` characters. Opaque here: it is
        resolved to a view by the registry layer, not by this codec.
    handler
        The encoded handler token, exactly ``2`` characters.
    payload
        Trailing data whose structure depends on the resolved view. Empty when
        the component carries neither a state key nor arguments.
    """

    version: int
    raw_cookie: str
    handler: str
    payload: str = ""


def encode_custom_id(custom_id: CustomID, *, view_name: str) -> str:
    """Assemble a ``CustomID`` into its wire string.

    Parameters
    ----------
    custom_id
        The parts to encode. Its ``version`` is written verbatim, so callers
        building a fresh id should set it to :data:`CODEC_VERSION`.
    view_name
        Name of the view the component belongs to. Not written to the wire; used
        only to build a helpful error if the result overflows.

    Returns
    -------
    str
        The encoded ``custom_id``.

    Raises
    ------
    CustomIdOverflowError
        If the assembled string would exceed Discord's 100-character limit.
    """
    encoded = f"{chr(custom_id.version)}{custom_id.raw_cookie}{custom_id.handler}{custom_id.payload}"
    if len(encoded) > constants.MAX_CUSTOM_ID_LENGTH:
        raise errors_.CustomIdOverflowError(view_name, len(encoded))
    return encoded


def decode_custom_id(custom_id: str) -> CustomID | None:
    """Parse a wire ``custom_id`` back into its parts.

    Fails soft: anything risa did not produce -- a string too short to hold the
    header, or one whose version byte is unrecognised -- decodes to ``None``
    rather than raising, so risa can share an interaction stream with other
    component handlers.

    Parameters
    ----------
    custom_id
        The raw ``custom_id`` taken from a component interaction.

    Returns
    -------
    CustomID | None
        The decoded parts, or ``None`` if the string was not produced by this
        codec version.
    """
    if len(custom_id) < _HEADER_LENGTH:
        return None
    if ord(custom_id[0]) != CODEC_VERSION:
        return None
    return CustomID(
        version=ord(custom_id[0]),
        raw_cookie=custom_id[_COOKIE_START:_HANDLER_START],
        handler=custom_id[_HANDLER_START:_HEADER_LENGTH],
        payload=custom_id[_HEADER_LENGTH:],
    )


def make_cookie(view_name: str, schema_version: int) -> str:
    """Derive the routing cookie for a view.

    The name and schema version are hashed together, so bumping the version
    changes the cookie and retires components rendered from the old shape. The
    result is one-way: it is recognised by a registry lookup, never decoded.

    Parameters
    ----------
    view_name
        The view name.
    schema_version
        The view's schema version.

    Returns
    -------
    str
        A cookie of :data:`COOKIE_LENGTH` characters.
    """
    digest = hashlib.blake2s(f"{view_name}:{schema_version}".encode(), digest_size=4).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:COOKIE_LENGTH]


def make_handler_token(handler_id: str, version: int) -> str:
    """Derive the token for a handler.

    The token depends only on the handler's own id and version, so reordering,
    adding or removing sibling handlers never changes it. Bumping the version
    changes the token, which is how old components are retired -- or kept alive,
    by leaving a handler registered under the old version.

    Parameters
    ----------
    handler_id
        The handler's stable id -- its method name by default.
    version
        The handler's version.

    Returns
    -------
    str
        A token of :data:`HANDLER_LENGTH` characters.
    """
    digest = hashlib.blake2s(f"{handler_id}:{version}".encode(), digest_size=2).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:HANDLER_LENGTH]


def make_state_key() -> str:
    """Mint the key a view's state is filed under.

    Random rather than derived from the message or the shard that sent it, which
    is what keeps a key meaningful to every process: nothing about it has to be
    recomputed when shards are added or a message is edited by a different
    worker than the one that sent it.

    Its width is charged against the ``custom_id`` limit, so the header and the
    key together leave the remainder of the hundred characters for arguments.

    Returns
    -------
    str
        A key of :data:`STATE_KEY_LENGTH` characters.
    """
    return secrets.token_urlsafe(_STATE_KEY_BYTES)


class ArgConverter(abc.ABC):
    """Turns one wire argument into ``custom_id`` characters and back.

    Converters are synchronous by design: decoding happens inside the window
    Discord allows for a response, before the handler can defer, so nothing
    here may do I/O.
    """

    __slots__ = ()

    @property
    @abc.abstractmethod
    def type_id(self) -> str:
        """The canonical id this converter hashes into the fingerprint.

        Shared by every annotation with the same wire encoding, so that
        switching between such annotations does not read as a signature change.
        """

    @abc.abstractmethod
    def encode(self, value: object) -> str:
        """Turn ``value`` into its wire characters.

        Parameters
        ----------
        value
            The argument to encode. Must be of the type this converter serves.

        Returns
        -------
        str
            The encoded characters, excluding the length prefix.

        Raises
        ------
        TypeError
            If ``value`` is not of the type this converter serves.
        """

    @abc.abstractmethod
    def decode(self, raw: str) -> object:
        """Turn wire characters back into the argument they encode.

        Parameters
        ----------
        raw
            The characters of one frame, without the length prefix.

        Returns
        -------
        object
            The decoded argument.

        Raises
        ------
        ValueError
            If ``raw`` is not something this converter produced.
        """


class _IntConverter(ArgConverter):
    """Ints as minimal-width little-endian signed bytes, rendered as latin-1."""

    __slots__ = ("_target",)

    def __init__(self, target: type[int]) -> None:
        self._target = target

    @property
    @typing.override
    def type_id(self) -> str:
        return "i"

    @typing.override
    def encode(self, value: object) -> str:
        if not isinstance(value, int) or isinstance(value, bool):
            msg = f"expected an int, got {type(value).__name__}"
            raise TypeError(msg)
        data = value.to_bytes((value.bit_length() + 8) // 8, "little", signed=True)
        return data.decode("latin-1")

    @typing.override
    def decode(self, raw: str) -> object:
        if not raw:
            msg = "an int frame cannot be empty"
            raise ValueError(msg)
        return self._target(int.from_bytes(raw.encode("latin-1"), "little", signed=True))


class _StrConverter(ArgConverter):
    """Strings carried as-is; the length prefix is what delimits them."""

    __slots__ = ("_target",)

    def __init__(self, target: type[str]) -> None:
        self._target = target

    @property
    @typing.override
    def type_id(self) -> str:
        return "s"

    @typing.override
    def encode(self, value: object) -> str:
        if not isinstance(value, str):
            msg = f"expected a str, got {type(value).__name__}"
            raise TypeError(msg)
        return value

    @typing.override
    def decode(self, raw: str) -> object:
        return self._target(raw)


class _BoolConverter(ArgConverter):
    """Bools as a single character."""

    __slots__ = ()

    @property
    @typing.override
    def type_id(self) -> str:
        return "b"

    @typing.override
    def encode(self, value: object) -> str:
        if not isinstance(value, bool):
            msg = f"expected a bool, got {type(value).__name__}"
            raise TypeError(msg)
        return "\x01" if value else "\x00"

    @typing.override
    def decode(self, raw: str) -> object:
        if raw not in {"\x00", "\x01"}:
            msg = f"not a bool frame: {raw!r}"
            raise ValueError(msg)
        return raw == "\x01"


class _EnumConverter(ArgConverter):
    """Enums by member value, validated against the enum on decode.

    The fingerprint records only the value kind, not the enum class: renaming
    or extending the enum is not a signature change, and a stale wire value is
    caught anyway because looking it up on the enum raises.
    """

    __slots__ = ("_inner", "_kind", "_target")

    def __init__(self, target: type[enum.Enum], inner: ArgConverter, kind: str) -> None:
        self._target = target
        self._inner = inner
        self._kind = kind

    @property
    @typing.override
    def type_id(self) -> str:
        return self._kind

    @typing.override
    def encode(self, value: object) -> str:
        if not isinstance(value, self._target):
            msg = f"expected {self._target.__name__}, got {type(value).__name__}"
            raise TypeError(msg)
        return self._inner.encode(value.value)

    @typing.override
    def decode(self, raw: str) -> object:
        return self._target(self._inner.decode(raw))


def resolve_converter(annotation: object) -> ArgConverter | None:
    """Return the converter for a parameter annotation, if it has one.

    ``None`` is the classifier for "not a wire argument": a parameter whose
    annotation resolves to no converter belongs to dependency injection.

    Parameters
    ----------
    annotation
        The parameter's annotation, already resolved to a runtime object.

    Returns
    -------
    ArgConverter | None
        The converter, or ``None`` when the annotation has no wire encoding.
    """
    if annotation is bool:
        return _BoolConverter()
    if not isinstance(annotation, type):
        return None

    converter: ArgConverter | None = None
    if issubclass(annotation, enum.Enum):
        converter = _resolve_enum_converter(annotation)
    elif issubclass(annotation, int) and not issubclass(annotation, bool):
        converter = _IntConverter(annotation)
    elif issubclass(annotation, str):
        converter = _StrConverter(annotation)
    return converter


def _resolve_enum_converter(annotation: type[enum.Enum]) -> ArgConverter | None:
    """Pick the converter for an enum by the type of its member values.

    Returns
    -------
    ArgConverter | None
        The converter, or ``None`` when the member values are neither all ints
        nor all strings.
    """
    values: list[object] = [member.value for member in annotation]
    if values and all(type(value) is int for value in values):
        return _EnumConverter(annotation, _IntConverter(int), "ei")
    if values and all(type(value) is str for value in values):
        return _EnumConverter(annotation, _StrConverter(str), "es")
    return None


class ArgSpec(msgspec.Struct, frozen=True):
    """One wire parameter of a handler.

    Attributes
    ----------
    name
        The parameter's name, used to normalise keyword bindings and to name
        the parameter in errors. Deliberately absent from the fingerprint.
    converter
        The converter serving the parameter's annotation.
    required
        Whether the parameter has no default, and so must always be bound.
    """

    name: str
    converter: ArgConverter
    required: bool


class HandlerSignature(msgspec.Struct, frozen=True):
    """The wire shape of one handler's parameters.

    Attributes
    ----------
    args
        The wire parameters, in declaration order.
    fingerprint
        Hash of the converter chain, or ``""`` for a handler with no wire
        parameters, which carries no fingerprint at all.
    """

    args: tuple[ArgSpec, ...]
    fingerprint: str


def make_signature_fingerprint(type_ids: collections.abc.Iterable[str]) -> str:
    """Hash a converter chain into the fingerprint its components carry.

    Parameters
    ----------
    type_ids
        The canonical type id of each wire parameter, in order.

    Returns
    -------
    str
        A fingerprint of :data:`FINGERPRINT_LENGTH` characters.
    """
    digest = hashlib.blake2s(",".join(type_ids).encode(), digest_size=2).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:FINGERPRINT_LENGTH]


def encode_args(
    values: collections.abc.Sequence[object],
    signature: HandlerSignature,
    *,
    handler_id: str,
) -> str:
    """Encode bound argument values into a component's args payload.

    ``values`` may cover only a prefix of the wire parameters; the omitted tail
    must be defaulted, and dispatch will let the current Python defaults apply.
    The fingerprint always hashes the whole chain, so a partial binding is
    still checked against the full signature on decode.

    Parameters
    ----------
    values
        The bound values, in wire-parameter order.
    signature
        The handler's wire signature.
    handler_id
        Id of the handler, for errors.

    Returns
    -------
    str
        The args payload: the fingerprint followed by one length-prefixed
        frame per value, or ``""`` for a handler with no wire parameters.

    Raises
    ------
    ArgBindError
        If there are more values than wire parameters, an omitted parameter
        has no default, or a value cannot be encoded.
    """
    if not signature.args:
        if values:
            reason = f"handler takes no wire arguments, got {len(values)}"
            raise errors_.ArgBindError(handler_id, reason)
        return ""

    if len(values) > len(signature.args):
        reason = f"handler takes at most {len(signature.args)} wire arguments, got {len(values)}"
        raise errors_.ArgBindError(handler_id, reason)

    for spec in signature.args[len(values) :]:
        if spec.required:
            reason = f"missing required wire argument {spec.name!r}"
            raise errors_.ArgBindError(handler_id, reason)

    frames: list[str] = []
    for spec, value in zip(signature.args, values, strict=False):
        try:
            data = spec.converter.encode(value)
        except TypeError as exc:
            reason = f"cannot encode {spec.name!r}: {exc}"
            raise errors_.ArgBindError(handler_id, reason) from exc
        if len(data) > _MAX_FRAME_LENGTH:
            reason = (
                f"encoded value for {spec.name!r} is {len(data)} characters; the frame limit is {_MAX_FRAME_LENGTH}"
            )
            raise errors_.ArgBindError(handler_id, reason)
        frames.append(chr(len(data)) + data)

    return signature.fingerprint + "".join(frames)


def decode_args(
    payload: str,
    signature: HandlerSignature,
    *,
    view_name: str,
    handler_id: str,
    version: int,
) -> tuple[object, ...]:
    """Decode a component's args payload against its handler's signature.

    The returned tuple holds only the arguments the component actually
    carried; a defaulted tail the binding omitted is left for Python's own
    defaults when the handler is called.

    Parameters
    ----------
    payload
        The args portion of the ``custom_id`` payload, after any state key.
    signature
        The wire signature of the handler the component resolved to.
    view_name
        Name of the view, for errors.
    handler_id
        Id of the handler, for errors.
    version
        Version of the handler, for errors.

    Returns
    -------
    tuple[object, ...]
        The decoded arguments, in wire-parameter order.

    Raises
    ------
    SignatureMismatchError
        If the payload does not fit the signature: the handler's signature was
        edited in place after the component was rendered.
    """
    if not signature.args:
        if payload:
            reason = "the component carries arguments, but the handler declares no wire parameters"
            raise errors_.SignatureMismatchError(view_name, handler_id, version, reason)
        return ()

    if len(payload) < FINGERPRINT_LENGTH:
        reason = "the component carries no signature fingerprint, but the handler declares wire parameters"
        raise errors_.SignatureMismatchError(view_name, handler_id, version, reason)

    if payload[:FINGERPRINT_LENGTH] != signature.fingerprint:
        reason = "the fingerprints differ; bump the handler's version instead of editing its signature in place"
        raise errors_.SignatureMismatchError(view_name, handler_id, version, reason)

    frames = _split_frames(payload[FINGERPRINT_LENGTH:])
    if frames is None:
        reason = "an argument frame is truncated"
        raise errors_.SignatureMismatchError(view_name, handler_id, version, reason)
    if len(frames) > len(signature.args):
        reason = f"the component carries more than the {len(signature.args)} declared wire arguments"
        raise errors_.SignatureMismatchError(view_name, handler_id, version, reason)

    return _decode_values(frames, signature, view_name=view_name, handler_id=handler_id, version=version)


def _split_frames(data: str) -> list[str] | None:
    """Split length-prefixed frames, or return ``None`` on a truncated one.

    Returns
    -------
    list[str] | None
        The frames without their prefixes, or ``None`` when one runs past the
        end of the data.
    """
    frames: list[str] = []
    index = 0
    while index < len(data):
        length = ord(data[index])
        frame = data[index + 1 : index + 1 + length]
        if len(frame) != length:
            return None
        frames.append(frame)
        index += 1 + length
    return frames


def _decode_values(
    frames: list[str],
    signature: HandlerSignature,
    *,
    view_name: str,
    handler_id: str,
    version: int,
) -> tuple[object, ...]:
    """Run each frame through its converter and check the omitted tail.

    Returns
    -------
    tuple[object, ...]
        The decoded arguments, in wire-parameter order.

    Raises
    ------
    SignatureMismatchError
        If a frame does not decode, or an omitted parameter has no default.
    """
    values: list[object] = []
    for spec, data in zip(signature.args, frames, strict=False):
        try:
            values.append(spec.converter.decode(data))
        except ValueError as exc:
            reason = f"cannot decode {spec.name!r}: {exc}"
            raise errors_.SignatureMismatchError(view_name, handler_id, version, reason) from exc

    for spec in signature.args[len(frames) :]:
        if spec.required:
            reason = f"the component does not carry the required wire argument {spec.name!r}"
            raise errors_.SignatureMismatchError(view_name, handler_id, version, reason)

    return tuple(values)
