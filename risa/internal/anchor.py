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
"""How a view's state reaches the message its components live on.

Every rendered component carries a slice of one string -- the view's *anchor*
-- alongside its routing header. The anchor comes in two dialects, told apart
by a leading tag, and which one a view uses is its registered placement:

``m``
    The state itself: a fingerprint of the schema it was written under, a
    counter, and the durable fields packed as a positional array. Split across
    the components the view renders and read back by reassembling them, so the
    message *is* the storage.
``s``
    A random key naming a record in a :class:`~risa.state.store.Store`.
    Replicated whole into every component rather than split, because it is
    small and because every component then stands alone.

What this module offers:

* :meth:`MessageAnchor.encode` and :meth:`StoreAnchor.encode` write the two
  dialects; :func:`parse` reads either one back, or ``None`` if what arrived is
  damaged.
* :func:`split_across` and :func:`replicate` are the two ways to distribute an
  anchor over a message's components -- cut into pieces, or handed to each
  whole. :func:`join_fragments` reads back either, since a replicated anchor is
  just fragments that all agree.
* :func:`make_state_key` mints the key the store dialect names a record by.

Splitting and rejoining treat the anchor as opaque: everything a reader needs
to prove it has the whole thing lives inside the message dialect's own header,
so :func:`split_across` and :func:`join_fragments` never look at a tag.
"""

from __future__ import annotations

import secrets
import typing

import msgspec

from risa.internal import wire

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "MAX_ANCHOR_LENGTH",
    "MAX_SEQ",
    "MESSAGE_TAG",
    "STATE_FINGERPRINT_LENGTH",
    "STATE_KEY_LENGTH",
    "STORE_TAG",
    "Anchor",
    "MessageAnchor",
    "StoreAnchor",
    "join_fragments",
    "make_state_key",
    "parse",
    "replicate",
    "split_across",
)

MESSAGE_TAG: typing.Final[str] = "m"
"""Tag of an anchor that carries the state itself."""

STORE_TAG: typing.Final[str] = "s"
"""Tag of an anchor that carries a store key."""

STATE_FINGERPRINT_LENGTH: typing.Final[int] = 3
"""Width of the schema fingerprint an in-message anchor carries.

Three characters rather than the two a signature fingerprint uses: a view
computes one fingerprint per *prefix* of its durable fields, so the collision
budget is spent within a single view rather than across a codebase.
"""

_TAG_WIDTH: typing.Final[int] = 1
_LENGTH_WIDTH: typing.Final[int] = 2
_SEQ_WIDTH: typing.Final[int] = 3

# The message dialect's header, field by field, in order.
_MESSAGE_WIDTHS: typing.Final[tuple[int, ...]] = (
    _TAG_WIDTH,
    _LENGTH_WIDTH,
    STATE_FINGERPRINT_LENGTH,
    _SEQ_WIDTH,
)
_MESSAGE_HEADER_LENGTH: typing.Final[int] = sum(_MESSAGE_WIDTHS)

MAX_SEQ: typing.Final[int] = wire.largest_value(_SEQ_WIDTH)
"""Largest value the anchor's counter reaches before it wraps."""

MAX_ANCHOR_LENGTH: typing.Final[int] = wire.largest_value(_LENGTH_WIDTH)
"""Largest anchor the length field can describe."""

_STATE_KEY_BYTES: typing.Final[int] = 12

STATE_KEY_LENGTH: typing.Final[int] = len(wire.pack_bytes(bytes(_STATE_KEY_BYTES)))
"""Width of a store key, which is what :data:`_STATE_KEY_BYTES` renders to."""

_STORE_ANCHOR_LENGTH: typing.Final[int] = _TAG_WIDTH + STATE_KEY_LENGTH


class MessageAnchor(msgspec.Struct, frozen=True):
    """An anchor carrying the view's state in the message itself.

    Attributes
    ----------
    fingerprint
        Hash of the durable-field prefix the state was written under. Compared
        against the view's own prefixes to decide whether the state still fits
        the schema.
    seq
        The counter this state was written with, used to tell a fresh message
        snapshot from one that predates an edit this process already made.
    state
        The packed durable fields, still encoded.
    """

    fingerprint: str
    seq: int
    state: str

    def encode(self) -> str:
        """Assemble this anchor into the string its components carry.

        Returns
        -------
        str
            The anchor, ready to be distributed across components.

        Raises
        ------
        ValueError
            If the assembled anchor is longer than the length field can
            describe.
        """
        total = _MESSAGE_HEADER_LENGTH + len(self.state)
        if total > MAX_ANCHOR_LENGTH:
            msg = f"anchor of {total} characters exceeds the {MAX_ANCHOR_LENGTH} the length field describes"
            raise ValueError(msg)
        return "".join(
            (
                MESSAGE_TAG,
                wire.pack_uint(total, _LENGTH_WIDTH),
                self.fingerprint,
                wire.pack_uint(self.seq % (MAX_SEQ + 1), _SEQ_WIDTH),
                self.state,
            ),
        )


class StoreAnchor(msgspec.Struct, frozen=True):
    """An anchor naming a record in a store.

    Attributes
    ----------
    key
        The key the view's state is filed under.
    """

    key: str

    def encode(self) -> str:
        """Assemble this anchor into the string every component carries.

        Returns
        -------
        str
            The anchor, small enough that every component carries it whole.
        """
        return f"{STORE_TAG}{self.key}"


type Anchor = MessageAnchor | StoreAnchor
"""What a component's fragments reassemble into."""


def make_state_key() -> str:
    """Mint the key a view's state is filed under.

    Random rather than derived from the message or the shard that sent it,
    which is what keeps a key meaningful to every process: nothing about it has
    to be recomputed when shards are added or a message is edited by a
    different worker than the one that sent it.

    Returns
    -------
    str
        A key of :data:`STATE_KEY_LENGTH` characters.
    """
    return wire.pack_bytes(secrets.token_bytes(_STATE_KEY_BYTES))


def parse(raw: str) -> Anchor | None:
    """Read an assembled anchor back into the dialect it was written in.

    Fails soft: a tag risa does not know, a truncated header, a length that
    disagrees with what arrived, or a malformed counter all read as ``None``
    rather than raising. Everything here came off a message that anything --
    another library, a moderator's edit, a partial delivery -- may have
    touched, so the only safe reading of a damaged anchor is no reading at all.

    Parameters
    ----------
    raw
        The rejoined anchor.

    Returns
    -------
    Anchor | None
        The parsed anchor, or ``None`` if it is not intact.
    """
    if not raw:
        return None

    if raw[0] == STORE_TAG:
        return StoreAnchor(key=raw[_TAG_WIDTH:]) if len(raw) == _STORE_ANCHOR_LENGTH else None

    if raw[0] != MESSAGE_TAG or len(raw) < _MESSAGE_HEADER_LENGTH:
        return None

    _, raw_total, fingerprint, raw_seq = wire.split_fields(raw, _MESSAGE_WIDTHS)
    seq = wire.unpack_uint(raw_seq)
    if wire.unpack_uint(raw_total) != len(raw) or seq is None:
        return None

    return MessageAnchor(fingerprint=fingerprint, seq=seq, state=raw[_MESSAGE_HEADER_LENGTH:])


def split_across(anchor: str, capacities: collections.abc.Sequence[int]) -> list[str] | None:
    """Cut an anchor into one fragment per component.

    Greedy, which is optimal here: fragments are rejoined by index, so only the
    total capacity matters, and filling earlier components first leaves later
    ones empty rather than spreading a short anchor thinly.

    Parameters
    ----------
    anchor
        The anchor to split. Treated as opaque.
    capacities
        How many characters each component has spare, in the order the tree
        renders them.

    Returns
    -------
    list[str] | None
        One fragment per capacity -- empty for components the anchor does not
        reach -- or ``None`` if the anchor does not fit.
    """
    fragments: list[str] = []
    rest = anchor
    for capacity in capacities:
        fragments.append(rest[:capacity])
        rest = rest[capacity:]
    return None if rest else fragments


def replicate(anchor: str, count: int) -> list[str]:
    """Give every component the whole anchor.

    The other way to distribute one, and what the store dialect uses: a key is
    small enough that splitting it would only cost every component the ability
    to stand on its own. Because each fragment then claims index zero and they
    all agree, :func:`join_fragments` reads them back with no special case.

    Parameters
    ----------
    anchor
        The anchor to hand out.
    count
        How many components are being rendered.

    Returns
    -------
    list[str]
        The same anchor, ``count`` times.
    """
    return [anchor] * count


def join_fragments(fragments: collections.abc.Iterable[tuple[int, str]]) -> str | None:
    """Rejoin an anchor from the fragments a message carries.

    Fails closed. A message whose components were edited by something other
    than risa can arrive with a fragment missing, duplicated or reordered, and
    the one thing that must never happen is quietly rejoining those into a
    shorter anchor that still parses. Contiguity is checked here; that the
    result is *whole* is checked by :func:`parse` against the length the
    anchor declares about itself.

    Parameters
    ----------
    fragments
        ``(index, fragment)`` for every risa component found on the message, in
        any order. A replicated anchor arrives as the same fragment at index
        zero several times, which is accepted.

    Returns
    -------
    str | None
        The rejoined anchor, or ``None`` if the fragments are not a contiguous
        run starting at zero.
    """
    by_index: dict[int, str] = {}
    for index, fragment in fragments:
        if by_index.setdefault(index, fragment) != fragment:
            return None

    if not by_index:
        return None
    try:
        return "".join(by_index[index] for index in range(len(by_index)))
    except KeyError:
        return None
