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
"""State that lives in the message its components are attached to.

Reading is free: Discord delivers the whole message with every interaction, so
the state arrives with the click. Writing is the message edit itself -- there
is no second place to put anything, which is why this placement has no crash
window between committing and displaying.

Two things guard it. A lock per message id keeps two clicks on one message from
interleaving, which is enough on a gateway bot because every click on a message
reaches the process that owns its shard. And a counter in the anchor, together
with a small cache of what this process last wrote, closes the one gap a lock
cannot: the message Discord attaches to an interaction is a *snapshot* taken
when the click happened, so a click made before an edit landed carries state
older than the truth. Comparing counters is what notices.

The cache is an optimisation of correctness, never a store: losing it entirely
costs nothing but the ability to notice that particular staleness, and a
restart empties it precisely when no interaction old enough to matter can still
be answered.

One restriction falls out of reading state off the message: a message carries at
most one instance of any given view. Two instances of the same view class would
write their fragments into the same numbered slots, and rejoining them yields
nothing either of them wrote. Send them as separate messages, or give them
separate views.
"""

from __future__ import annotations

import asyncio
import logging
import typing
import weakref

import msgspec

from risa import errors
from risa.internal import anchor as anchor_
from risa.internal import reader
from risa.state import backend

if typing.TYPE_CHECKING:
    import hikari

    from risa import view as view_
    from risa.internal import codec
    from risa.internal import registry

__all__ = ("MessageBackend", "MessageSession", "MessageStateCache", "encode_state")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.state.message")

_REMEMBERED_EDITS: typing.Final[int] = 4
"""How many superseded anchors are remembered per view per message.

Enough to recognise the clicks that can still be in flight -- Discord allows
three seconds to answer one, and a message is not usefully edited more often
than that -- without keeping a history nobody will ever consult.
"""


def encode_state(view: view_.View, *, meta: registry.ViewMeta, seq: int) -> tuple[str, str]:
    """Pack a view's durable fields into an anchor the message can carry.

    Parameters
    ----------
    view
        The view whose state to pack.
    meta
        The view's metadata, whose schema decides what is durable.
    seq
        The counter to stamp the anchor with.

    Returns
    -------
    tuple[str, str]
        The encoded anchor and the packed state inside it. Both, because
        packing is the most expensive thing a re-render does and the caller
        needs the state as well as the anchor wrapped around it.

    Raises
    ------
    StateOverflowError
        If the packed state is larger than an anchor can describe. Distinct
        from the tree being too small to carry it, which the build pass
        reports, and reached only by genuinely enormous state.
    """
    packed = meta.schema.pack(meta.schema.values(view))
    try:
        encoded = anchor_.MessageAnchor(fingerprint=meta.schema.fingerprint, seq=seq, state=packed).encode()
    except ValueError as exc:
        raise errors.StateOverflowError(meta.name, len(packed), anchor_.MAX_ANCHOR_LENGTH) from exc
    return encoded, packed


class _Written(msgspec.Struct, frozen=True):
    """What this process last published to one view on one message.

    Attributes
    ----------
    anchor
        The anchor the last edit carried.
    superseded
        The anchors this process has replaced on this message, oldest first. A
        click carrying one of these was made before that edit landed, which is
        the one thing this cache exists to notice -- and the only thing that
        tells a stale snapshot apart from a message somebody has legitimately
        rebuilt since.
    """

    anchor: str
    superseded: tuple[str, ...]


class MessageStateCache:
    """What this process last wrote to each message, and the locks over them.

    Bounded and lossy on purpose. Every entry can be dropped at any moment
    without making anything incorrect: the worst that follows is that a stale
    click is taken at face value, which is the same thing that happens on the
    very first click after a restart.

    The locks are held weakly, so one lives exactly as long as somebody holds or
    waits on it rather than accumulating one per message ever clicked.

    Parameters
    ----------
    max_entries
        How many messages to remember before the least recently written is
        forgotten.
    """

    __slots__ = ("_locks", "_max_entries", "_written")

    def __init__(self, *, max_entries: int = 10_000) -> None:
        self._written: dict[tuple[int, str], _Written] = {}
        self._locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
        self._max_entries = max_entries

    def lock(self, message_id: int) -> asyncio.Lock:
        """Return the lock serialising work on one message.

        Parameters
        ----------
        message_id
            The message to lock.

        Returns
        -------
        asyncio.Lock
            The lock, created on first use.
        """
        lock = self._locks.get(message_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[message_id] = lock
        return lock

    def last_written(self, message_id: int, cookie: str) -> _Written | None:
        """Return what this process last published for one view on a message.

        Kept per view rather than per message, because a message may carry more
        than one, and what one of them wrote says nothing about another.

        Parameters
        ----------
        message_id
            The message to look up.
        cookie
            The cookie of the view to look up.

        Returns
        -------
        _Written | None
            The last anchor written, or ``None``.
        """
        return self._written.get((message_id, cookie))

    def note(self, message_id: int, cookie: str, *, anchor: str, replaced: str) -> None:
        """Record that an edit carrying ``anchor`` has landed.

        Parameters
        ----------
        message_id
            The message that was edited.
        cookie
            The cookie of the view that edited it.
        anchor
            The anchor that edit carried.
        replaced
            The anchor the message was showing until that edit landed. This is
            what a click made just before it will arrive carrying.
        """
        key = (message_id, cookie)
        previous = self._written.pop(key, None)
        history = (*(previous.superseded if previous is not None else ()), replaced)
        self._written[key] = _Written(anchor=anchor, superseded=history[-_REMEMBERED_EDITS:])
        while len(self._written) > self._max_entries:
            self._written.pop(next(iter(self._written)))


class MessageSession(backend.StateSession):
    """One dispatch against a view whose state rides its own message.

    Parameters
    ----------
    meta
        The view being dispatched.
    interaction
        The interaction, whose message carries the state.
    cache
        Where this process records what it last wrote.

    Notes
    -----
    The clicked component's own fragment is not consulted at all: the message is
    read whole, which is exactly what lets a click on a stale component still see
    current state. Two things are tracked as the dispatch runs -- the whole
    anchor the message is taken to be showing, which is what a click made just
    before the next edit will arrive carrying, and the packed state inside it,
    which is what an unrendered change is compared against.
    """

    __slots__ = ("_cache", "_current", "_interaction", "_lock", "_meta", "_seen", "_seq", "_staged", "_staged_state")

    def __init__(
        self,
        *,
        meta: registry.ViewMeta,
        interaction: hikari.ComponentInteraction,
        cache: MessageStateCache,
    ) -> None:
        self._meta = meta
        self._interaction = interaction
        self._cache = cache
        self._lock: asyncio.Lock | None = None
        self._seq = 0
        self._staged: str | None = None
        self._staged_state = ""
        self._seen: str = ""
        self._current = ""

    @typing.override
    async def open(self) -> None:
        """Take the lock over this message."""
        self._lock = self._cache.lock(self._interaction.message.id)
        await self._lock.acquire()

    @typing.override
    async def close(self) -> None:
        """Release the lock over this message."""
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    @typing.override
    async def restore(self) -> backend.Restored | backend.Unreadably:
        """Rejoin the state from the message, preferring whatever is newest.

        A snapshot is taken as stale only when it carries an anchor this process
        has itself replaced, rather than merely a lower counter: a message
        somebody has legitimately rebuilt since counts from zero, and reverting
        that to whatever was last written here would silently undo the rebuild.

        Returns
        -------
        backend.Restored | backend.Unreadably
            The rebuilt view, or why it could not be rebuilt.
        """
        gathered = (
            anchor_.join_fragments(
                reader.fragments_of(self._interaction.message.components, cookie=self._meta.cookie),
            )
            or ""
        )
        parsed = anchor_.parse(gathered) if gathered else None
        if not isinstance(parsed, anchor_.MessageAnchor):
            _LOGGER.error(
                "interaction %s: view %r could not rejoin its state from the message. Its components"
                " were edited by something other than risa, or its placement changed under them.",
                self._interaction.id,
                self._meta.name,
            )
            return backend.Unreadably.OUTDATED

        believed = gathered
        written = self._cache.last_written(self._interaction.message.id, self._meta.cookie)
        if written is not None and gathered in written.superseded:
            fresher = anchor_.parse(written.anchor)
            if isinstance(fresher, anchor_.MessageAnchor):
                parsed, believed = fresher, written.anchor

        values = self._meta.schema.unpack(parsed.fingerprint, parsed.state)
        if values is None:
            _LOGGER.error(
                "interaction %s: the state on this message does not fit view %r as it is declared now."
                " Its durable fields changed without bumping the version on @risa.register.",
                self._interaction.id,
                self._meta.name,
            )
            return backend.Unreadably.OUTDATED

        self._seq = parsed.seq
        self._seen = believed
        self._current = parsed.state
        names = (field.name for field in self._meta.schema.fields)
        return backend.Restored(view=self._meta.cls(**dict(zip(names, values, strict=False))))

    @typing.override
    async def stage(self, view: view_.View) -> str:
        """Encode the view's state into the anchor the next render will carry.

        Nothing is written here, because for this placement the edit that
        carries the anchor *is* the write.

        Parameters
        ----------
        view
            The view about to be rendered.

        Returns
        -------
        str
            The anchor to build against.

        Raises
        ------
        StateOverflowError
            If the state is larger than an anchor can describe.
        """
        self._staged, self._staged_state = encode_state(view, meta=self._meta, seq=self._seq + 1)
        return self._staged

    @typing.override
    async def published(self, view: view_.View) -> None:
        """Record the staged anchor as this message's current state.

        Parameters
        ----------
        view
            The view that edit rendered, which is by construction the one
            :meth:`stage` packed a moment earlier.
        """
        staged = self._staged
        if staged is None:
            return
        self._seq += 1
        self._current = self._staged_state
        self._cache.note(self._interaction.message.id, self._meta.cookie, anchor=staged, replaced=self._seen)
        self._seen = staged
        self._staged = None

    @typing.override
    async def settle(self, view: view_.View) -> bool:
        """Report whether the handler left changes no edit has published yet.

        Parameters
        ----------
        view
            The view as the handler left it.

        Returns
        -------
        bool
            Whether an edit is still owed. There is nothing else to do here:
            with no store behind it, an unpublished change can only be written
            by rendering it.
        """
        return self._meta.schema.pack(self._meta.schema.values(view)) != self._current


class MessageBackend(backend.StateBackend):
    """Opens sessions for views that carry their state in their message.

    Parameters
    ----------
    cache
        Where this process records what it last wrote to each message.
    """

    __slots__ = ("_cache",)

    def __init__(self, cache: MessageStateCache | None = None) -> None:
        self._cache = cache if cache is not None else MessageStateCache()

    @typing.override
    def session(
        self,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> MessageSession:
        """Open a session against one message.

        Parameters
        ----------
        meta
            The view the interaction resolved to.
        component
            The clicked component, decoded. Carries only one slice of the
            anchor, which is why the message is read rather than the click.
        interaction
            The interaction being dispatched.

        Returns
        -------
        MessageSession
            A session that has not yet taken its lock.
        """
        return MessageSession(meta=meta, interaction=interaction, cache=self._cache)

    @typing.override
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> str:
        """Encode the view's state into the anchor its first render carries.

        Parameters
        ----------
        meta
            The view being sent.
        view
            The view instance, already loaded.

        Returns
        -------
        str
            The anchor, counting from zero so the first re-render advances to
            one.

        Raises
        ------
        StateOverflowError
            If the state is larger than an anchor can describe.
        """
        encoded, _ = encode_state(view, meta=meta, seq=0)
        return encoded
