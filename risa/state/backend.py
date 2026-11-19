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
"""The interface to a placement: one backend per placement, one session per click or update."""

from __future__ import annotations

import abc
import enum
import typing

import msgspec

if typing.TYPE_CHECKING:
    import collections.abc
    import contextlib
    import types

    import hikari

    from risa import view as view_
    from risa.internal import codec
    from risa.internal import registry

__all__ = (
    "Origin",
    "OriginFetcher",
    "Restored",
    "StateBackend",
    "StateSession",
    "Unreadably",
    "hydrate",
)


type OriginFetcher = collections.abc.Callable[[], collections.abc.Awaitable[Origin]]


class Unreadably(enum.Enum):
    """Why a session could not restore a view.

    Attributes
    ----------
    MISSING
        The view's store record is gone. Dispatch calls `on_state_missing`.
    OUTDATED
        The state was written by code this view no longer is, or cannot be read at all.
        Dispatch calls `on_outdated`.
    """

    MISSING = enum.auto()
    OUTDATED = enum.auto()


class Restored(msgspec.Struct, frozen=True):
    """A view rebuilt from its state."""

    view: view_.View


class Origin(msgspec.Struct, frozen=True):
    """What a session works on, whether a click delivered it or `Client.open` fetched it.

    Attributes
    ----------
    message
        The message that holds the view.
    component
        The clicked component, or for `Client.open` the view's first one on the message.
    asker
        What the log names: `interaction 123` for a click, `client.open` otherwise.
    application_id
        The application's id, when an interaction supplies it.
    """

    message: hikari.Message
    component: codec.CustomID
    asker: str
    application_id: hikari.Snowflake | None = None

    @classmethod
    def from_click(cls, interaction: hikari.ComponentInteraction, component: codec.CustomID) -> typing.Self:
        return cls(
            message=interaction.message,
            component=component,
            asker=f"interaction {interaction.id}",
            application_id=interaction.application_id,
        )


class StateSession(abc.ABC):
    """One click's hold on a view's state, or that of one update through `Client.open`.

    A click drives it in this order: `open`, `restore`, then any number of `stage` and
    `published` pairs, one per rerender, then `settle` after the handler, and `close` last,
    whatever happened. For an update, `StateBackend.update` stands in for `open` and
    `close`, and nothing settles.
    """

    __slots__ = ()

    async def __aenter__(self) -> typing.Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        await self.close()

    @abc.abstractmethod
    async def open(self) -> None:
        """Take whatever lock keeps other clicks on the view out until `close`."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Release the lock, after any last bookkeeping such as an `InStore` touch."""

    @abc.abstractmethod
    async def restore(self) -> Restored | Unreadably:
        """Rebuild the view from its state, or say why it cannot be."""

    @abc.abstractmethod
    async def stage(self, view: view_.View) -> bytes:
        """Prepare a commit of `view`'s durable state, and return the anchor to render with it.

        An `InStore` session writes to the store here, so the message never shows state the
        store has not accepted; an `InMessage` session writes when the message is edited.
        """

    @abc.abstractmethod
    async def published(self, view: view_.View) -> None:
        """Record that the message now shows the commit `stage` prepared."""

    @abc.abstractmethod
    async def settle(self, view: view_.View) -> bool:
        """Say whether `view` holds durable changes no commit has saved yet."""


class StateBackend(abc.ABC):
    """How one placement reads, writes and locks a view's state."""

    __slots__ = ()

    @abc.abstractmethod
    def session(self, *, meta: registry.ViewMeta, origin: Origin) -> StateSession:
        """Return a session for one click on a component of the view."""

    @abc.abstractmethod
    def update(
        self,
        *,
        meta: registry.ViewMeta,
        message_id: hikari.Snowflake,
        origin: OriginFetcher,
    ) -> contextlib.AbstractAsyncContextManager[StateSession]:
        """Hold a session for one update through `Client.open`, for as long as the block is entered.

        `origin` fetches the message the update works on. A placement whose lock needs
        nothing from the message runs it under that lock, so the copy it reads is never one a
        click is about to change. The session it yields is ready for `restore`, and leaving
        releases it: nobody calls its `open` or `close`. Releasing logs a failure and never
        raises, so it cannot take the place of what the block raised or of an update that
        went through.
        """

    @abc.abstractmethod
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> bytes:
        """Return the anchor a newly built view's components carry."""

    @abc.abstractmethod
    async def commit_first(
        self,
        *,
        meta: registry.ViewMeta,
        view: view_.View,
        anchor: bytes,
    ) -> None:
        """Save a newly built view's state under `anchor`, before it is sent."""


def hydrate(meta: registry.ViewMeta, values: collections.abc.Sequence[object]) -> view_.View:
    """Build a view from the values of its durable fields, leaving its props at their defaults."""
    kwargs = {field.name: value for field, value in zip(meta.schema.fields, values, strict=False)}
    return meta.cls(**kwargs)
