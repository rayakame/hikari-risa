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
"""Turning a view into the bytes a store keeps, and back again.

State is written wrapped in a small envelope recording which view wrote it and
under which schema version::

    {"v": 1, "n": "poll", "d": {"question": "Ship it?", "votes": 3}}

The version is what makes a shape change safe to read back. Adding a field with
a default needs nothing: state written before it simply takes the default.
Anything else -- removing a field, renaming one, changing what one means -- is a
new shape, and bumping the view's version both changes its cookie, so components
rendered by the old shape stop resolving, and marks the stored record so that it
is refused here rather than being poured into a shape it no longer fits.

JSON rather than a packed binary format, so that whatever is in the store can be
read by a person looking at it.
"""

from __future__ import annotations

import typing

import msgspec

from risa import errors

if typing.TYPE_CHECKING:
    from risa.internal import registry
    from risa.view import View

__all__ = ("dumps", "loads")


class _Record(msgspec.Struct):
    """The envelope a view's state is stored inside.

    Attributes
    ----------
    v
        Schema version of the view that wrote the state.
    n
        Name of the view that wrote it, recorded so that a store can be read and
        understood on its own.
    d
        The view's own fields, left encoded until the version has been checked.
    """

    v: int
    n: str
    d: msgspec.Raw


_ENCODER: typing.Final = msgspec.json.Encoder()
_RECORD_DECODER: typing.Final = msgspec.json.Decoder(_Record)


def dumps(view: View, *, meta: registry.ViewMeta) -> bytes:
    """Encode a view's state for storage.

    Parameters
    ----------
    view
        The view whose state to encode.
    meta
        The view's metadata, whose name and version stamp the envelope.

    Returns
    -------
    bytes
        The encoded record.
    """
    return _ENCODER.encode(_Record(v=meta.version, n=meta.name, d=msgspec.Raw(_ENCODER.encode(view))))


def loads(raw: bytes, *, meta: registry.ViewMeta) -> View:
    """Rebuild a view from stored state.

    Parameters
    ----------
    raw
        The encoded record, as it came out of the store.
    meta
        The view the state is expected to belong to.

    Returns
    -------
    View
        The view, rebuilt with the state it was stored with.

    Raises
    ------
    SchemaMismatchError
        If the state was written under a different schema version, and so
        describes a shape this view no longer has.
    StateDecodeError
        If the record or the state inside it cannot be read.
    """
    try:
        record = _RECORD_DECODER.decode(raw)
    except msgspec.DecodeError as exc:
        raise errors.StateDecodeError(meta.name, str(exc)) from exc

    if record.v != meta.version:
        raise errors.SchemaMismatchError(meta.name, record.v, meta.version)

    try:
        return msgspec.json.decode(record.d, type=meta.cls)
    except msgspec.DecodeError as exc:
        raise errors.StateDecodeError(meta.name, str(exc)) from exc
