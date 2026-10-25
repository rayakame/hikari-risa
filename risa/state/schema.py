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

import hikari
import msgspec

from risa import errors
from risa.internal import wire
from risa.state import anchor

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "DurableField",
    "Prop",
    "StateSchema",
    "structural_type_id",
)

type Prop[T] = typing.Annotated[T, _PropMarker]

type _PrefixDecoder = msgspec.msgpack.Decoder[tuple[object, ...]]


class _PropMarker:
    __slots__ = ()


class DurableField(msgspec.Struct, frozen=True):
    name: str
    annotation: object
    has_default: bool


class _Prefix(msgspec.Struct, frozen=True):
    decoder: _PrefixDecoder
    arity: int
    tail_is_defaulted: bool


class StateSchema:
    __slots__ = ("_encoder", "_fields", "_fingerprint", "_names", "_prefixes", "_view_name")

    def __init__(self, fields: collections.abc.Sequence[DurableField], *, view_name: str) -> None:
        self._fields = tuple(fields)
        self._names = tuple(f.name for f in self._fields)
        self._view_name = view_name
        self._encoder = msgspec.msgpack.Encoder(enc_hook=_encode_unknown)
        self._prefixes = {
            _fingerprint(self._fields[:size]): _Prefix(
                decoder=_decoder(self._fields[:size]),
                arity=size,
                tail_is_defaulted=all(f.has_default for f in self._fields[size:]),
            )
            for size in range(len(self._fields) + 1)
        }
        self._fingerprint = _fingerprint(self._fields)

    @classmethod
    def of(cls, view_cls: type[msgspec.Struct], *, view_name: str) -> StateSchema:
        durable: list[DurableField] = []
        for field in _fields_of(view_cls, view_name):
            if _hides_a_prop(field.type):
                reason = (
                    f"field {field.name!r} hides a risa.Prop marker inside its annotation, where it"
                    " would be ignored; write risa.Prop[...] around the whole annotation instead"
                )
                raise errors.ViewDeclarationError(view_name, reason)

            if _is_prop(field.type):
                if field.required:
                    reason = (
                        f"prop {field.name!r} has no default; dispatch builds the view from its"
                        " defaults before load() runs, so every prop needs one - or drop the"
                        " risa.Prop marker if the field should persist"
                    )
                    raise errors.ViewDeclarationError(view_name, reason)
                continue

            if field.type is Prop:
                reason = f"field {field.name!r} is annotated bare risa.Prop; write risa.Prop[<type>]"
                raise errors.ViewDeclarationError(view_name, reason)

            try:
                structural_type_id(field.type)
            except ValueError as exc:
                raise errors.ViewDeclarationError(view_name, f"field {field.name!r} {exc}") from exc

            durable.append(
                DurableField(name=field.name, annotation=field.type, has_default=not field.required),
            )

        return cls(durable, view_name=view_name)

    @property
    def fields(self) -> collections.abc.Sequence[DurableField]:
        return self._fields

    @property
    def durable(self) -> bool:
        return bool(self._fields)

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def accepts(self, fingerprint: str) -> bool:
        prefix = self._prefixes.get(fingerprint)
        return prefix is not None and prefix.tail_is_defaulted

    def values(self, view: msgspec.Struct) -> collections.abc.Sequence[object]:
        return [getattr(view, f.name) for f in self._fields]

    def adopt(self, view: msgspec.Struct, source: msgspec.Struct) -> None:
        for field in self._fields:
            setattr(view, field.name, getattr(source, field.name))

    def pack(self, values: collections.abc.Sequence[object]) -> bytes:
        if not self._fields:
            return b""
        try:
            return self._encoder.encode(tuple(values))
        except (OverflowError, TypeError) as exc:
            raise self._encode_error(values, exc) from exc

    def _encode_error(
        self,
        values: collections.abc.Sequence[object],
        fallback: Exception,
    ) -> errors.StateEncodeError:
        for name, value in zip(self._names, values, strict=False):
            try:
                self._encoder.encode(value)
            except (OverflowError, TypeError) as exc:
                return errors.StateEncodeError(self._view_name, name, str(exc))
        return errors.StateEncodeError(self._view_name, "?", str(fallback))

    def unpack(self, fingerprint: str, raw: bytes) -> collections.abc.Sequence[object] | None:
        prefix = self._prefixes.get(fingerprint)
        if prefix is None or not prefix.tail_is_defaulted:
            return None
        if prefix.arity == 0:
            return () if not raw else None
        try:
            return prefix.decoder.decode(raw)
        except msgspec.DecodeError:
            return None


def _is_prop(annotation: object) -> bool:
    return getattr(annotation, "__origin__", None) is Prop


def _hides_a_prop(annotation: object) -> bool:
    return any(_is_prop(arg) or _hides_a_prop(arg) for arg in typing.get_args(annotation))


def _fields_of(
    view_cls: type[msgspec.Struct],
    view_name: str,
) -> collections.abc.Sequence[msgspec.structs.FieldInfo]:
    try:
        return msgspec.structs.fields(view_cls)
    except NameError as exc:
        reason = (
            f"has a field annotated {exc.name!r}, which is not importable at runtime;"
            " a view's field annotations must be imported outside TYPE_CHECKING"
        )
        raise errors.ViewDeclarationError(view_name, reason) from exc


def _kind(info: msgspec.inspect.Type) -> str:
    return type(info).__name__.removesuffix("Type").lower()


def _render(info: msgspec.inspect.Type, open_structs: set[int]) -> str:  # ruff:ignore[too-many-return-statements]
    if isinstance(info, msgspec.inspect.CustomType):
        if info.cls is hikari.Snowflake:
            return "int"
        msg = f"is typed {info.cls.__qualname__}, which msgspec cannot serialize"
        raise ValueError(msg)
    if isinstance(
        info,
        msgspec.inspect.StructType
        | msgspec.inspect.TypedDictType
        | msgspec.inspect.NamedTupleType
        | msgspec.inspect.DataclassType,
    ):
        if id(info.cls) in open_structs:
            return "rec"
        nested = open_structs | {id(info.cls)}
        fields = ",".join(f"{f.encode_name}:{_render(f.type, nested)}" for f in info.fields)
        return f"{_kind(info)}({fields})"
    if isinstance(info, msgspec.inspect.CollectionType):
        return f"{_kind(info)}({_render(info.item_type, open_structs)})"
    if isinstance(info, msgspec.inspect.DictType):
        return f"dict({_render(info.key_type, open_structs)},{_render(info.value_type, open_structs)})"
    if isinstance(info, msgspec.inspect.TupleType):
        return f"tuple({','.join(_render(item, open_structs) for item in info.item_types)})"
    if isinstance(info, msgspec.inspect.UnionType):
        return f"union({','.join(sorted(_render(item, open_structs) for item in info.types))})"
    if isinstance(info, msgspec.inspect.EnumType | msgspec.inspect.LiteralType):
        return f"{_kind(info)}({','.join(sorted(_members(info)))})"
    return _kind(info)


def _members(info: msgspec.inspect.EnumType | msgspec.inspect.LiteralType) -> collections.abc.Iterable[str]:
    if isinstance(info, msgspec.inspect.EnumType):
        return (str(member.value) for member in info.cls)
    return (repr(value) for value in info.values)


def structural_type_id(annotation: object) -> str:
    try:
        info = msgspec.inspect.type_info(annotation)
    except TypeError as exc:
        msg = f"has an annotation msgspec cannot describe: {exc}"
        raise ValueError(msg) from exc
    return _render(info, set())


def _encode_unknown(value: object) -> object:
    if isinstance(value, hikari.Snowflake):
        return int(value)
    msg = f"cannot serialize {type(value).__name__}"
    raise TypeError(msg)


def _decode_unknown(target: type, value: object) -> object:
    if target is hikari.Snowflake and isinstance(value, int):
        return hikari.Snowflake(value)
    msg = f"cannot rebuild {target.__qualname__} from {type(value).__name__}"
    raise TypeError(msg)


def _fingerprint(fields: collections.abc.Sequence[DurableField]) -> str:
    joined = ",".join(f"{f.name}:{structural_type_id(f.annotation)}" for f in fields)
    return wire.pack_digest(joined, chars=anchor.SCHEMA_FP_LENGTH)


def _decoder(fields: collections.abc.Sequence[DurableField]) -> _PrefixDecoder:
    return msgspec.msgpack.Decoder(
        tuple.__class_getitem__(tuple(f.annotation for f in fields)),
        dec_hook=_decode_unknown,
    )
