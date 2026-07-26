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
"""One dispatch's dealings with a view's state, whichever placement it uses.

A *session* is opened for every interaction that resolves to a view, and lives
exactly as long as that dispatch. It is the only thing that knows where the
state actually is, so everything above it -- the client, the context, the build
pass -- is written once and works under either placement.

A backend also answers for the *other* direction, the send that first puts a
view on screen. There is no interaction there and nothing to lock, so it is two
plain calls rather than a session: :meth:`~StateBackend.first_anchor` says what
the first render should carry, and :meth:`~StateBackend.commit_first` persists
whatever that anchor points at -- afterwards, so a view that cannot be rendered
leaves nothing behind.

Two of the hooks default to doing nothing rather than being abstract, because
for half the placements there is genuinely nothing to do: a store-backed view has
already committed by the time its edit goes out, and a message-resident one has
nothing to write once the message carries it. Only the hooks a new placement must
answer for itself are abstract.

The shape of a dispatch, in the order a session sees it::

    async with backend.session(...) as session:  # takes the state's lock
        outcome = await session.restore()  # gather / read; may fail closed
        ...  # load(), then the handler
        anchor = await session.stage(view)  # what the next render carries
        ...  # the message edit goes out
        await session.published(view)  # that edit is now the truth
        await session.settle(view)  # nothing left uncommitted

The lock is taken when the session opens and released when it closes, and a
session is only ever open for work risa itself is doing -- never across a modal
a human is filling in. That is the locking law of DESIGN.md 7.3, and it is the
reason a session is a context manager rather than something a handler holds.
"""

from __future__ import annotations

import abc
import enum
import typing

import msgspec

if typing.TYPE_CHECKING:
    import types

    import hikari

    from risa import view as view_
    from risa.internal import codec
    from risa.internal import registry

__all__ = ("Restored", "StateBackend", "StateSession", "Unreadably")


class Unreadably(enum.Enum):
    """Why a view could not be rebuilt, and therefore how to answer the click."""

    MISSING = enum.auto()
    """The record is gone -- expired, evicted, or deleted.

    Routed to ``on_state_missing``, and reachable only for a view whose state
    lives in a store: state carried by a message cannot go missing while the
    message exists.
    """

    OUTDATED = enum.auto()
    """The state does not fit the view any more, or never did.

    A schema edited without a version bump, a placement changed under live
    components, or a message something other than risa has torn apart. Routed
    to ``on_outdated``, since the component is the thing at fault rather than
    the state being absent.
    """


class Restored(msgspec.Struct, frozen=True):
    """A view rebuilt from wherever its state was kept.

    Attributes
    ----------
    view
        The rebuilt view, with its durable fields filled in and its props at
        their defaults.
    """

    view: view_.View


class StateSession(abc.ABC):
    """One dispatch's transaction against a single view's state."""

    __slots__ = ()

    async def __aenter__(self) -> typing.Self:
        """Take whatever lock protects this view's state.

        Returns
        -------
        typing.Self
            This session.
        """
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        """Release the lock, however the dispatch ended."""
        await self.close()

    @abc.abstractmethod
    async def open(self) -> None:
        """Take the lock protecting this view's state."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release the lock."""

    @abc.abstractmethod
    async def restore(self) -> Restored | Unreadably:
        """Rebuild the view from wherever its state was kept.

        Returns
        -------
        Restored | Unreadably
            The view, or why it could not be rebuilt.
        """

    @abc.abstractmethod
    async def stage(self, view: view_.View) -> str:
        """Prepare the anchor the next render should carry.

        Called before every message edit. A backend that must have the state
        committed *before* the message can show it does that write here, so
        that a crash between the two leaves the store ahead of the message
        rather than behind it.

        Parameters
        ----------
        view
            The view about to be rendered.

        Returns
        -------
        str
            The anchor to build the components against.
        """

    async def published(self, view: view_.View) -> None:  # ruff:ignore[empty-method-without-abstract-decorator]
        """Record that an edit carrying the staged anchor has landed.

        Does nothing by default: a placement whose state was already committed
        by :meth:`stage` has nothing left to learn from the edit going out.

        Parameters
        ----------
        view
            The view that edit rendered.
        """

    @abc.abstractmethod
    async def settle(self, view: view_.View) -> bool:
        """Finish the dispatch, leaving nothing uncommitted.

        Parameters
        ----------
        view
            The view as the handler left it.

        Returns
        -------
        bool
            Whether the view still holds changes that only a message edit can
            publish, so the caller knows to issue one.
        """


class StateBackend(abc.ABC):
    """Opens sessions for views placed one particular way."""

    __slots__ = ()

    @abc.abstractmethod
    def session(
        self,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> StateSession:
        """Open a session for one interaction.

        Parameters
        ----------
        meta
            The view the interaction resolved to.
        component
            The clicked component, decoded. Its fragment is this component's
            share of the anchor, which only means anything alongside the index
            saying which share it is.
        interaction
            The interaction being dispatched, which is also where a
            message-resident view reads its state from.

        Returns
        -------
        StateSession
            A session that has not yet taken its lock.
        """

    @abc.abstractmethod
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> str:
        """Return the anchor a view's very first render should carry.

        Parameters
        ----------
        meta
            The view being sent.
        view
            The view instance, already loaded.

        Returns
        -------
        str
            The anchor, or ``""`` for a view with no durable state.

        Raises
        ------
        StateOverflowError
            If the state is larger than an anchor can describe at all.
        """

    async def commit_first(  # ruff:ignore[empty-method-without-abstract-decorator]
        self,
        *,
        meta: registry.ViewMeta,
        view: view_.View,
        anchor: str,
    ) -> None:
        """Persist what a first render's anchor points at.

        Called only once the tree has built, so a view that cannot be rendered
        leaves nothing behind. Does nothing by default: a placement whose anchor
        already holds the state has nothing else to write.

        Parameters
        ----------
        meta
            The view being sent.
        view
            The view instance that was rendered.
        anchor
            What :meth:`first_anchor` returned.
        """
