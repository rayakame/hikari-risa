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
"""What a view's durable state *is*, and how it survives the view changing.

A view's durable fields are packed positionally: names are not written, since
the reader knows the shape. That makes the packing small, and makes the shape
itself the thing that has to be pinned down -- which is what the *fingerprint*
does.

A schema fingerprints not only its whole field list but every prefix of it, and
that is what lets a view grow. State written before a field was appended
carries the shorter prefix's fingerprint, so it is decoded against the shape it
was actually written under and the new field simply takes its default. Every
other change -- removing, renaming, retyping or reordering -- alters the
fingerprints from that point on, so old state matches no prefix and is refused
rather than poured into a shape it no longer fits.

Refusing matters most for the change that looks harmless: swapping two fields
of the same type leaves the packed shape identical, so nothing but the names
distinguishes the old state from the new. Names are therefore hashed alongside
types, which is the opposite of the choice made for handler arguments, where a
parameter rename is a refactor rather than a change of meaning.
"""

from __future__ import annotations

import typing

import msgspec

from risa import errors
from risa.internal import anchor
from risa.internal import wire

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("DurableField", "Prop", "StateSchema", "structural_type_id")


class _PropMarker:
    """What :data:`Prop` tags an annotation with. Never instantiated."""

    __slots__ = ()


type Prop[T] = typing.Annotated[T, _PropMarker]
"""Marks a view field as a per-dispatch *prop* rather than durable state.

A view's fields are durable by default: risa persists them, and they come back
on the next click. A field marked ``Prop`` is the opposite -- it is never
written anywhere, costs nothing against the message's capacity, and arrives at
every dispatch holding its default. Whatever ``render`` needs from it, the view
refills in :meth:`~risa.View.load`::

    @risa.register(name="board", version=1)
    class Leaderboard(risa.View):
        guild_id: int                       # durable, rides the message
        page: int = 0                       # durable, survives restarts
        rows: risa.Prop[list[Row]] = []      # fresh every dispatch

        async def load(self, db: Database = linkd.INJECTED) -> None:
            self.rows = await db.top(self.guild_id, page=self.page)

Use it for anything whose real home is elsewhere -- rows from a database, a
computed summary, anything large. The litmus test is that **if a handler
assigns to a field and expects the value to still be there next click, it
cannot be a Prop**: the assignment is simply discarded.

Every prop needs a default, since dispatch builds the view from those defaults
before ``load`` runs. The marker must also sit at the top of the annotation:
``Prop[int | None]`` is a prop holding an optional int, while
``Prop[int] | None`` is rejected when the view is registered, because a marker
buried inside a union would otherwise be silently ignored.
"""

type _PrefixDecoder = msgspec.msgpack.Decoder[tuple[object, ...]]
"""Decoder for one prefix of a view's durable fields, as a positional array."""


class DurableField(msgspec.Struct, frozen=True):
    """One field of a view that is persisted rather than refilled per dispatch.

    Attributes
    ----------
    name
        The attribute's name, which is half of its wire identity.
    annotation
        The resolved annotation, packed and unpacked by its own type.
    has_default
        Whether the field can be left out of stored state. Only a defaulted
        field may be appended to a live view without retiring its components.
    """

    name: str
    annotation: object
    has_default: bool


class _Prefix(msgspec.Struct, frozen=True):
    """One shape a view's state may have been written under.

    Attributes
    ----------
    decoder
        Reads a positional array of exactly this prefix's fields.
    tail_is_defaulted
        Whether every field appended after this prefix can supply its own
        value. When it cannot, state this old cannot be completed and is
        refused.
    """

    decoder: _PrefixDecoder
    tail_is_defaulted: bool


def structural_type_id(annotation: object) -> str:
    """Render an annotation as the canonical id the fingerprint hashes.

    Structural rather than nominal: two annotations share an id exactly when
    they pack and unpack identically, so a rename of a struct *class* is free
    while changing what it holds is not. Walks into generic parameters and
    struct fields, so a change buried inside ``list[Row]`` is as visible as one
    at the top level.

    Types msgspec cannot describe structurally -- anything it reports as a
    custom type -- render alike, so a change between two such annotations is
    invisible here. They are also the annotations msgspec cannot pack without a
    hook, so they cannot be durable fields in the first place.

    Parameters
    ----------
    annotation
        The field's resolved annotation.

    Returns
    -------
    str
        A canonical id, stable across runs and processes.
    """
    return _render(msgspec.inspect.type_info(annotation), set())


def _kind(info: msgspec.inspect.Type) -> str:
    """Name an inspected type's category, as the fingerprint spells it.

    Returns
    -------
    str
        The category name.
    """
    return type(info).__name__.removesuffix("Type").lower()


def _render(info: msgspec.inspect.Type, open_structs: set[int]) -> str:  # ruff:ignore[too-many-return-statements]
    """Render one inspected type, guarding against a struct that contains itself.

    Returns
    -------
    str
        The canonical rendering of this type.
    """
    if isinstance(info, msgspec.inspect.StructType):
        if id(info.cls) in open_structs:
            return "rec"
        nested = open_structs | {id(info.cls)}
        fields = ",".join(f"{field.encode_name}:{_render(field.type, nested)}" for field in info.fields)
        return f"struct({fields})"
    if isinstance(info, msgspec.inspect.CollectionType):
        return f"{_kind(info)}({_render(info.item_type, open_structs)})"
    if isinstance(info, msgspec.inspect.DictType):
        return f"dict({_render(info.key_type, open_structs)},{_render(info.value_type, open_structs)})"
    if isinstance(info, msgspec.inspect.TupleType):
        return f"tuple({','.join(_render(item, open_structs) for item in info.item_types)})"
    if isinstance(info, msgspec.inspect.UnionType):
        return f"union({','.join(sorted(_render(item, open_structs) for item in info.types))})"
    if isinstance(info, msgspec.inspect.EnumType):
        return f"enum({','.join(sorted(str(member.value) for member in info.cls))})"
    return _kind(info)


def _fingerprint(fields: collections.abc.Sequence[DurableField]) -> str:
    """Hash a prefix of a view's durable fields into the characters it carries.

    Returns
    -------
    str
        The fingerprint of exactly these fields.
    """
    joined = ",".join(f"{field.name}:{structural_type_id(field.annotation)}" for field in fields)
    return wire.pack_digest(joined, width=anchor.STATE_FINGERPRINT_LENGTH)


def _decoder(fields: collections.abc.Sequence[DurableField]) -> _PrefixDecoder:
    """Build the strict decoder for exactly these fields.

    Strict is right even though the schema may have grown: which prefix a blob
    belongs to is settled by its fingerprint before any decoding happens, so
    within that prefix the array's arity is known exactly and a mismatch is
    corruption rather than evolution.

    The tuple type is assembled by calling ``__class_getitem__`` rather than by
    subscripting, because the parameters are only known at runtime and writing
    that as ``tuple[...]`` reads to a type checker as a type expression it
    cannot evaluate.

    Returns
    -------
    _PrefixDecoder
        A decoder for a positional array of exactly these fields.
    """
    return msgspec.msgpack.Decoder(tuple.__class_getitem__(tuple(field.annotation for field in fields)))


def _is_prop(annotation: object) -> bool:
    """Whether an annotation is marked as a per-dispatch prop.

    Identity against the alias rather than a look at ``__metadata__``: a PEP
    695 alias does not surface its metadata through the usual inspection path,
    so a reader that went looking for the marker there would silently find
    nothing and treat every prop as durable.

    Returns
    -------
    bool
        Whether the marker is at the top of this annotation.
    """
    return getattr(annotation, "__origin__", None) is Prop


def _hides_a_prop(annotation: object) -> bool:
    """Whether a marker is buried somewhere it will not be honoured.

    Returns
    -------
    bool
        Whether any part of the annotation below the top level is a prop.
    """
    return any(_is_prop(arg) or _hides_a_prop(arg) for arg in typing.get_args(annotation))


def _unwrap(annotation: object) -> object:
    """Return what a prop annotation wraps, or the annotation itself.

    Returns
    -------
    object
        The type the field actually holds.
    """
    args = typing.get_args(annotation)
    return args[0] if _is_prop(annotation) and args else annotation


class StateSchema:
    """A view's durable fields, and every earlier shape they have had.

    Built once per view at registration.

    Parameters
    ----------
    fields
        The view's durable fields, in declaration order.
    """

    __slots__ = ("_encoder", "_fields", "_fingerprint", "_prefixes")

    def __init__(self, fields: collections.abc.Sequence[DurableField]) -> None:
        self._fields = tuple(fields)
        self._encoder = msgspec.msgpack.Encoder()
        self._prefixes = {
            _fingerprint(self._fields[:size]): _Prefix(
                decoder=_decoder(self._fields[:size]),
                tail_is_defaulted=all(field.has_default for field in self._fields[size:]),
            )
            for size in range(len(self._fields) + 1)
        }
        self._fingerprint = _fingerprint(self._fields)

    @classmethod
    def of(cls, view_cls: type[msgspec.Struct], *, view_name: str) -> StateSchema:
        """Partition a view's fields into durable state and per-dispatch props.

        Every rule a view's field list has to obey is checked here, at import
        time, so a declaration mistake is a traceback on startup rather than a
        click that misbehaves later.

        Parameters
        ----------
        view_cls
            The view class to inspect.
        view_name
            The view's registered name, for errors.

        Returns
        -------
        StateSchema
            The schema of whatever fields remain durable.

        Raises
        ------
        ViewDeclarationError
            If a prop has no default, or a prop marker is buried where it
            would be ignored.
        """
        durable: list[DurableField] = []
        for field in msgspec.structs.fields(view_cls):
            if _hides_a_prop(field.type):
                reason = (
                    f"field {field.name!r} hides a risa.Prop marker inside its annotation, where it"
                    f" would be ignored; write risa.Prop[...] around the whole annotation instead"
                )
                raise errors.ViewDeclarationError(view_name, reason)

            if _is_prop(field.type):
                if field.required:
                    reason = (
                        f"prop {field.name!r} has no default; dispatch builds the view from its"
                        f" defaults before load() runs, so every prop needs one"
                    )
                    raise errors.ViewDeclarationError(view_name, reason)
                continue

            durable.append(
                DurableField(name=field.name, annotation=_unwrap(field.type), has_default=not field.required),
            )

        return cls(durable)

    @property
    def fields(self) -> tuple[DurableField, ...]:
        """The durable fields, in declaration order."""
        return self._fields

    @property
    def durable(self) -> bool:
        """Whether this view has any state to persist at all."""
        return bool(self._fields)

    def values(self, view: msgspec.Struct) -> list[object]:
        """Read the current value of every durable field off a view.

        Parameters
        ----------
        view
            The view to read.

        Returns
        -------
        list[object]
            One value per durable field, in declaration order.
        """
        return [getattr(view, field.name) for field in self._fields]

    @property
    def fingerprint(self) -> str:
        """The fingerprint of the view's current, complete field list."""
        return self._fingerprint

    def pack(self, values: collections.abc.Sequence[object]) -> str:
        """Encode the current value of every durable field.

        Parameters
        ----------
        values
            One value per durable field, in declaration order.

        Returns
        -------
        str
            The packed state, ready to sit in an anchor.
        """
        return wire.pack_bytes(self._encoder.encode(tuple(values)))

    def unpack(self, fingerprint: str, raw: str) -> tuple[object, ...] | None:
        """Decode stored state against the shape it was written under.

        Parameters
        ----------
        fingerprint
            The fingerprint the state arrived with.
        raw
            The packed state.

        Returns
        -------
        tuple[object, ...] | None
            The values for the leading fields the state covers -- fewer than
            the view now declares when it predates an appended field -- or
            ``None`` if the state fits no shape this view has ever had, omits a
            field that has no default, or does not decode.
        """
        prefix = self._prefixes.get(fingerprint)
        if prefix is None or not prefix.tail_is_defaulted:
            return None

        data = wire.unpack_bytes(raw)
        if data is None:
            return None
        try:
            return prefix.decoder.decode(data)
        except msgspec.DecodeError:
            return None
