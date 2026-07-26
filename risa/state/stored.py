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
"""State that lives in a store, named by a key the message carries.

The message holds nothing but a random key, so the state behind it is unbounded
and can expire -- and, with a store every process can reach, is correct even
when two clicks on one message land on two different processes.

Two rules shape the writing. **The store is written before the message is
edited**, so a crash between the two leaves the store ahead of what is on
screen: the next click reads the truth and the message catches up. The reverse
ordering would leave a message showing state nothing agrees with.

And **the version check is what makes it correct, not the lock**. A lock held
here means nothing to another process, so every write is conditional on the
state not having moved since it was read. Losing that check is not an error the
user should see: the handler has already had its say, so the losing dispatch
converges the message onto whatever won rather than raising into a click that
was, from the user's side, perfectly reasonable.
"""

from __future__ import annotations

import contextlib
import logging
import typing

from risa import errors
from risa.internal import anchor as anchor_
from risa.state import backend
from risa.state import placement as placement_
from risa.state import serde

if typing.TYPE_CHECKING:
    import hikari

    from risa import view as view_
    from risa.internal import codec
    from risa.internal import registry
    from risa.state import store as store_

__all__ = ("StoredBackend", "StoredSession")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.state.stored")


class StoredSession(backend.StateSession):
    """One dispatch against a view whose state lives in a store.

    Parameters
    ----------
    meta
        The view being dispatched.
    key
        The key its state is filed under, taken from the clicked component.
    store
        Where that state lives.
    ttl
        Seconds an entry survives without being touched.
    interaction
        The interaction being dispatched, for diagnostics.
    """

    __slots__ = ("_held", "_interaction", "_key", "_meta", "_raw", "_renewed", "_store", "_ttl", "_version")

    def __init__(
        self,
        *,
        meta: registry.ViewMeta,
        key: str,
        store: store_.Store,
        ttl: float | None,
        interaction: hikari.ComponentInteraction,
    ) -> None:
        self._meta = meta
        self._key = key
        self._store = store
        self._ttl = ttl
        self._interaction = interaction
        self._held = contextlib.AsyncExitStack()
        self._raw = b""
        self._version = 0
        self._renewed = False

    @typing.override
    async def open(self) -> None:
        """Take the store's lock over this key.

        A component that names no key takes no lock. There is nothing behind it
        to protect, and every such click would otherwise queue on one lock over
        the empty string -- across the whole fleet, for a distributed store.

        Raises
        ------
        LockTimeoutError
            If the key stays held for longer than the store allows waiting.
        """
        if self._key:
            await self._held.enter_async_context(self._store.lock(self._key))

    @typing.override
    async def close(self) -> None:
        """Release the store's lock."""
        await self._held.aclose()

    @typing.override
    async def restore(self) -> backend.Restored | backend.Unreadably:
        """Read the record this key names and rebuild the view from it.

        Returns
        -------
        backend.Restored | backend.Unreadably
            The rebuilt view, or why it could not be rebuilt.

        Raises
        ------
        StoreUnavailableError
            If the store cannot be reached at all. Deliberately not caught: an
            outage is not a state condition, and there is no honest thing to
            tell the user about a view whose state nobody can read.
        """
        if not self._key:
            _LOGGER.error(
                "interaction %s: view %r keeps its state in a store, but the component carries no key."
                " Its placement changed without a version bump.",
                self._interaction.id,
                self._meta.name,
            )
            return backend.Unreadably.OUTDATED

        versioned = await self._store.get_versioned(self._key)
        if versioned is None:
            return backend.Unreadably.MISSING

        self._raw, self._version = versioned
        try:
            view = serde.loads(self._raw, meta=self._meta)
        except errors.SerializationError:
            _LOGGER.exception(
                "interaction %s: the record under key %r no longer fits view %r. Its durable fields"
                " changed without bumping the version on @risa.register.",
                self._interaction.id,
                self._key,
                self._meta.name,
            )
            return backend.Unreadably.OUTDATED

        return backend.Restored(view=view)

    @typing.override
    async def stage(self, view: view_.View) -> str:
        """Commit the view, then hand back the key the message keeps carrying.

        The write happens here rather than after the edit, so that a message
        can never show state the store has not accepted. The anchor itself does
        not change, which is what keeps a component's ``custom_id`` stable
        across re-renders: a click already in flight stays valid.

        Parameters
        ----------
        view
            The view about to be rendered.

        Returns
        -------
        str
            The unchanged anchor.

        Raises
        ------
        StateConflictError
            If the record moved while this handler was running.
        """
        await self._commit(view)
        return anchor_.StoreAnchor(key=self._key).encode()

    @typing.override
    async def published(self, view: view_.View) -> None:
        """Do nothing: the store was already written before the edit went out.

        Parameters
        ----------
        view
            The view that edit rendered.
        """

    @typing.override
    async def settle(self, view: view_.View) -> bool:
        """Write anything the handler changed but never rendered.

        A handler that mutates state and returns without redrawing still meant
        the change, so it is committed here. A handler that changed nothing
        costs no write at all -- only a ``touch``, so that a view still being
        used never expires, and not even that if this dispatch already renewed
        the entry by re-rendering.

        Parameters
        ----------
        view
            The view as the handler left it.

        Returns
        -------
        bool
            Always ``False``: this placement has its own home for state, so
            nothing is owed to a message edit.

        Raises
        ------
        StateConflictError
            If the record moved while this handler was running.
        """
        await self._commit(view)
        return False

    async def _commit(self, view: view_.View) -> None:
        """Write the view back if it differs from what was read.

        Every write sets the entry's expiry, so a dispatch that has already
        committed once owes the store nothing further when it finds the view
        unchanged the second time.

        Raises
        ------
        StateConflictError
            If the record moved since it was read, so this write would undo
            somebody else's.
        """
        encoded = serde.dumps(view, meta=self._meta)
        if encoded == self._raw:
            if self._ttl is not None and not self._renewed:
                await self._store.touch(self._key, ttl=self._ttl)
                self._renewed = True
            return

        written = await self._store.put_if_version(
            self._key,
            encoded,
            expected=self._version,
            ttl=self._ttl,
        )
        if not written:
            raise errors.StateConflictError(self._key)

        self._raw = encoded
        self._version += 1
        self._renewed = True


class StoredBackend(backend.StateBackend):
    """Opens sessions for views whose state lives in a store.

    Parameters
    ----------
    stores
        The client's stores, by the name a view's placement refers to.
    """

    __slots__ = ("_stores",)

    def __init__(self, stores: dict[str, store_.Store]) -> None:
        self._stores = stores

    def store_for(self, meta: registry.ViewMeta) -> store_.Store:
        """Return the store a view's placement names.

        Parameters
        ----------
        meta
            The view whose placement names a store.

        Returns
        -------
        store_.Store
            The store that view's state lives in.

        Raises
        ------
        ViewDeclarationError
            If the view names a store this client was never given. Checked
            again when the view is added, so reaching it here means the view
            was routed without ever passing through that check.
        """
        placement = meta.placement
        name = placement.store if isinstance(placement, placement_.InStore) else ""
        store = self._stores.get(name)
        if store is None:
            reason = f"store {name!r} is not one of this client's stores: {sorted(self._stores)}"
            raise errors.ViewDeclarationError(meta.name, reason)
        return store

    @typing.override
    def session(
        self,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> StoredSession:
        """Open a session against one stored record.

        A key is replicated whole into every component, so it is only ever the
        anchor's first slice. Insisting on that is what stops a chunk of some
        other dialect -- which is opaque text, and can begin with the store
        tag by chance -- from being read as a key that was never written.

        Parameters
        ----------
        meta
            The view the interaction resolved to.
        component
            The clicked component, decoded. Its fragment names the record.
        interaction
            The interaction being dispatched.

        Returns
        -------
        StoredSession
            A session that has not yet taken its lock.

        Raises
        ------
        ViewDeclarationError
            If the view names a store this client was never given.
        """
        key = ""
        if component.fragment_index == 0:
            parsed = anchor_.parse(component.fragment)
            key = parsed.key if isinstance(parsed, anchor_.StoreAnchor) else ""

        return StoredSession(
            meta=meta,
            key=key,
            store=self.store_for(meta),
            ttl=meta.ttl,
            interaction=interaction,
        )

    @typing.override
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> str:
        """Mint the key this view's state will be filed under.

        Parameters
        ----------
        meta
            The view being sent.
        view
            The view instance, already loaded.

        Returns
        -------
        str
            An anchor naming a record that does not exist yet.
        """
        return anchor_.StoreAnchor(key=anchor_.make_state_key()).encode()

    @typing.override
    async def commit_first(self, *, meta: registry.ViewMeta, view: view_.View, anchor: str) -> None:
        """Write the record the first render's components now point at.

        Parameters
        ----------
        meta
            The view being sent.
        view
            The view instance that was rendered.
        anchor
            The anchor :meth:`first_anchor` minted.

        Raises
        ------
        ViewDeclarationError
            If the view names a store this client was never given.
        """
        parsed = anchor_.parse(anchor)
        if not isinstance(parsed, anchor_.StoreAnchor):
            return
        await self.store_for(meta).put(parsed.key, serde.dumps(view, meta=meta), ttl=meta.ttl)
