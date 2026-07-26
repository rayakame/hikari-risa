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
"""The interaction a handler is invoked for, and the ways it can be answered.

Every response funnels through one initial-response gate -- a lock and a state
flag -- shared by the handler's own calls and the auto-defer watchdog, so
exactly one of them issues the initial response no matter how the race falls.
Responses are issued through the same REST calls on every transport; a REST
bot's listener merely waits for the gate to open before answering Discord's
webhook with ``204 No Content``.

The two surfaces that touch messages mirror the thing that defines them: a
followup is an ordinary message risa does not own, so :meth:`Context.respond`
mirrors hikari's kwargs, while the component's own message is defined by
``render()``, so :meth:`ComponentContext.edit` takes exactly a
:data:`~risa.ui.Layout`.

Everything module-level that is absent from ``__all__`` -- the dispatch state
and the functions driving it -- is plumbing shared with the client, and not
part of the public API.
"""

from __future__ import annotations

import abc
import asyncio
import enum
import logging
import typing

import hikari

from risa import errors
from risa import view as view_
from risa.internal import anchor as anchor_
from risa.internal import constants
from risa.ui import build as build_

if typing.TYPE_CHECKING:
    import collections.abc

    from risa.client import Client
    from risa.internal import registry
    from risa.ui import nodes

__all__ = ("ComponentContext", "Context", "InteractionT", "ModalContext", "Response")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.context")

type InteractionT = hikari.ComponentInteraction | hikari.ModalInteraction
"""An interaction risa dispatches a handler for."""


class _InitialResponse(enum.Enum):
    """What has been issued as the interaction's initial response so far.

    Discord permits exactly one initial response per interaction; everything
    after it travels as webhook calls. Which kind went out first decides how
    later calls reach their target, so the gate records it rather than just
    whether one happened.
    """

    NONE = enum.auto()
    """Nothing yet: the next response call wins the initial response."""

    UPDATE_DEFERRED = enum.auto()
    """A silent ack; the initial response is bound to the origin message."""

    THINKING_DEFERRED = enum.auto()
    """A "thinking…" spinner is showing, waiting to be replaced."""

    ORIGIN_EDIT = enum.auto()
    """The initial response edited the origin message."""

    MESSAGE = enum.auto()
    """The initial response was, or completed into, a message of its own."""


class DispatchState:
    """The response gate and dispatch wiring behind one context.

    Owned jointly by the context, which funnels every response through it, and
    the client, which starts the watchdog and settles it when dispatch ends.
    Internal plumbing, deliberately absent from ``__all__``; nothing here is
    public API.
    """

    __slots__ = (
        "acknowledged",
        "autodefer_options",
        "autodefer_task",
        "initial",
        "lock",
        "meta",
        "state_key",
        "view",
    )

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.initial = _InitialResponse.NONE
        self.acknowledged = asyncio.Event()
        self.autodefer_task: asyncio.Task[None] | None = None
        self.autodefer_options: tuple[bool, bool] | None = None
        self.meta: registry.ViewMeta | None = None
        self.state_key: str | None = None
        self.view: view_.View | None = None

    def transition(self, initial: _InitialResponse) -> None:
        """Record the issued initial response and open the acknowledgement gate."""
        self.initial = initial
        self.acknowledged.set()

    def cancel_autodefer(self) -> None:
        """Stop the watchdog; called under the lock by whoever won the race."""
        if self.autodefer_task is not None:
            self.autodefer_task.cancel()
            self.autodefer_task = None


class Response:
    """A handle to one message sent in answer to an interaction.

    Returned by :meth:`Context.respond`, whether the message went out as the
    interaction's initial response or as a followup -- the handle hides which,
    so callers can edit or delete what they sent without tracking message ids.

    Parameters
    ----------
    interaction
        The interaction the message answered.
    message
        The followup message, or ``None`` when the message was the initial
        response, which Discord returns nothing for.
    """

    __slots__ = ("_interaction", "_message")

    def __init__(self, interaction: InteractionT, message: hikari.Message | None) -> None:
        self._interaction = interaction
        self._message = message

    async def fetch(self) -> hikari.Message:
        """Return the message behind this handle.

        Fetched from Discord for an initial response; for a followup the
        message hikari returned at send time is used as-is.

        Returns
        -------
        hikari.Message
            The message.
        """
        if self._message is None:
            return await self._interaction.fetch_initial_response()
        return self._message

    async def edit(  # ruff:ignore[too-many-arguments]
        self,
        content: hikari.UndefinedOr[str] = hikari.UNDEFINED,
        *,
        attachment: hikari.UndefinedOr[hikari.Resourceish] = hikari.UNDEFINED,
        attachments: hikari.UndefinedOr[collections.abc.Sequence[hikari.Resourceish]] = hikari.UNDEFINED,
        component: hikari.UndefinedOr[hikari.api.ComponentBuilder] = hikari.UNDEFINED,
        components: hikari.UndefinedOr[collections.abc.Sequence[hikari.api.ComponentBuilder]] = hikari.UNDEFINED,
        embed: hikari.UndefinedOr[hikari.Embed] = hikari.UNDEFINED,
        embeds: hikari.UndefinedOr[collections.abc.Sequence[hikari.Embed]] = hikari.UNDEFINED,
        mentions_everyone: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        user_mentions: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.PartialUser] | bool] = hikari.UNDEFINED,
        role_mentions: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.PartialRole] | bool] = hikari.UNDEFINED,
    ) -> hikari.Message:
        """Edit the message behind this handle.

        Parameters mirror :meth:`hikari.messages.Message.edit`.

        Returns
        -------
        hikari.Message
            The edited message.
        """
        if self._message is None:
            return await self._interaction.edit_initial_response(
                content,
                attachment=attachment,
                attachments=attachments,
                component=component,
                components=components,
                embed=embed,
                embeds=embeds,
                mentions_everyone=mentions_everyone,
                user_mentions=user_mentions,
                role_mentions=role_mentions,
            )
        return await self._message.edit(
            content,
            attachment=attachment,
            attachments=attachments,
            component=component,
            components=components,
            embed=embed,
            embeds=embeds,
            mentions_everyone=mentions_everyone,
            user_mentions=user_mentions,
            role_mentions=role_mentions,
        )

    async def delete(self) -> None:
        """Delete the message behind this handle."""
        if self._message is None:
            await self._interaction.delete_initial_response()
        else:
            await self._message.delete()


class Context[T: InteractionT](abc.ABC):
    """What a handler is given about the interaction it is answering.

    Holds what every interaction shares: the identifying properties, and the
    response machinery -- :meth:`respond` and :meth:`defer` behave identically
    for components and modals. Subclassed per kind for what differs: a
    component always has a message behind it and may redraw it, while a modal
    need not have one. The generic parameter is what gives each subclass a
    precisely typed :attr:`interaction` without restating it.

    Parameters
    ----------
    client
        The client that dispatched the interaction.
    interaction
        The interaction being answered.
    state
        The dispatch state to share with the client. Internal; leave unset
        outside of risa's own dispatch.
    """

    __slots__ = ("_client", "_interaction", "_state")

    def __init__(self, client: Client, interaction: T, state: DispatchState | None = None) -> None:
        self._client = client
        self._interaction = interaction
        self._state = state if state is not None else DispatchState()

    @property
    def client(self) -> Client:
        """The client that dispatched this interaction."""
        return self._client

    @property
    def interaction(self) -> T:
        """The interaction being answered."""
        return self._interaction

    @property
    def custom_id(self) -> str:
        """The identifier the interaction routed under."""
        return self._interaction.custom_id

    @property
    def user(self) -> hikari.User:
        """The user who triggered the interaction."""
        return self._interaction.user

    @property
    def member(self) -> hikari.InteractionMember | None:
        """The guild member who triggered the interaction, outside a DM."""
        return self._interaction.member

    @property
    def channel(self) -> hikari.InteractionChannel:
        """The channel the interaction was invoked in."""
        return self._interaction.channel

    @property
    def channel_id(self) -> hikari.Snowflake:
        """The channel the interaction was invoked in."""
        return self._interaction.channel_id

    @property
    def guild_id(self) -> hikari.Snowflake | None:
        """The guild the interaction was invoked in, or ``None`` in a DM."""
        return self._interaction.guild_id

    @property
    def app_permissions(self) -> hikari.Permissions | None:
        """What the bot is permitted to do in the channel, or ``None`` in a DM."""
        return self._interaction.app_permissions

    @property
    def locale(self) -> str | hikari.Locale:
        """The locale of the user who triggered the interaction."""
        return self._interaction.locale

    @property
    def guild_locale(self) -> str | hikari.Locale | None:
        """The guild's preferred locale, or ``None`` in a DM."""
        return self._interaction.guild_locale

    @property
    @abc.abstractmethod
    def message(self) -> hikari.Message | None:
        """The message this interaction came from, if there is one.

        Declared here so every context carries it, and narrowed by subclasses
        that always have one.
        """

    async def defer(self, *, thinking: bool = False, ephemeral: bool = False) -> None:
        """Acknowledge the interaction now and answer properly later.

        Rarely needed by hand: the auto-defer watchdog issues this for any
        handler that outruns Discord's deadline. Call it explicitly to choose
        the moment or the flavour yourself.

        Parameters
        ----------
        thinking
            Whether to show the "thinking…" spinner, for a handler about to
            answer with a new message. The default is the silent ack, for a
            handler about to edit its own message.
        ephemeral
            Whether the spinner -- and the message that replaces it -- is
            visible only to whoever clicked. Only meaningful with ``thinking``.

        Raises
        ------
        AlreadyRespondedError
            If any response has been issued already; a defer can only ever be
            the initial response.
        ValueError
            If ``ephemeral`` is passed without ``thinking``; a silent ack has
            nothing to hide.
        """
        if ephemeral and not thinking:
            msg = "ephemeral only applies to a thinking defer; pass thinking=True as well"
            raise ValueError(msg)

        async with self._state.lock:
            if self._state.initial is not _InitialResponse.NONE:
                attempted = "defer"
                raise errors.AlreadyRespondedError(attempted)
            self._state.cancel_autodefer()
            await _issue_deferral(self._interaction, self._state, thinking=thinking, ephemeral=ephemeral)

    async def respond(  # ruff:ignore[too-many-arguments]
        self,
        content: hikari.UndefinedOr[str] = hikari.UNDEFINED,
        *,
        ephemeral: bool = False,
        tts: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        attachment: hikari.UndefinedOr[hikari.Resourceish] = hikari.UNDEFINED,
        attachments: hikari.UndefinedOr[collections.abc.Sequence[hikari.Resourceish]] = hikari.UNDEFINED,
        component: hikari.UndefinedOr[hikari.api.ComponentBuilder] = hikari.UNDEFINED,
        components: hikari.UndefinedOr[collections.abc.Sequence[hikari.api.ComponentBuilder]] = hikari.UNDEFINED,
        embed: hikari.UndefinedOr[hikari.Embed] = hikari.UNDEFINED,
        embeds: hikari.UndefinedOr[collections.abc.Sequence[hikari.Embed]] = hikari.UNDEFINED,
        mentions_everyone: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        user_mentions: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.PartialUser] | bool] = hikari.UNDEFINED,
        role_mentions: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.PartialRole] | bool] = hikari.UNDEFINED,
    ) -> Response:
        """Send a message to whoever triggered the interaction.

        Always a message of its own -- never the component's message, which
        :meth:`ComponentContext.rerender` and :meth:`ComponentContext.edit`
        redraw. Parameters mirror hikari's, because a plain message is
        hikari's to describe.

        Usable any number of times: the first call becomes the interaction's
        initial response (or replaces the "thinking…" spinner, if one is
        showing), and every later call is delivered as a followup.

        Parameters
        ----------
        content
            The text of the message.
        ephemeral
            Whether the message is visible only to whoever triggered the
            interaction.
        tts
            Whether the message is read aloud.
        attachment
            One attachment for the message.
        attachments
            Attachments for the message.
        component
            One component builder for the message.
        components
            Component builders for the message.
        embed
            One embed for the message.
        embeds
            Embeds for the message.
        mentions_everyone
            Whether an ``@everyone`` in the content pings.
        user_mentions
            The users the message may ping, or a bool for all or none.
        role_mentions
            The roles the message may ping, or a bool for all or none.

        Returns
        -------
        Response
            A handle to the sent message.
        """
        flags = hikari.MessageFlag.EPHEMERAL if ephemeral else hikari.UNDEFINED

        async with self._state.lock:
            if self._state.initial is _InitialResponse.NONE:
                self._state.cancel_autodefer()
                await self._interaction.create_initial_response(
                    hikari.ResponseType.MESSAGE_CREATE,
                    content,
                    flags=flags,
                    tts=tts,
                    attachment=attachment,
                    attachments=attachments,
                    component=component,
                    components=components,
                    embed=embed,
                    embeds=embeds,
                    mentions_everyone=mentions_everyone,
                    user_mentions=user_mentions,
                    role_mentions=role_mentions,
                )
                self._state.transition(_InitialResponse.MESSAGE)
                return Response(self._interaction, None)

            if self._state.initial is _InitialResponse.THINKING_DEFERRED:
                await self._interaction.edit_initial_response(
                    content,
                    attachment=attachment,
                    attachments=attachments,
                    component=component,
                    components=components,
                    embed=embed,
                    embeds=embeds,
                    mentions_everyone=mentions_everyone,
                    user_mentions=user_mentions,
                    role_mentions=role_mentions,
                )
                self._state.initial = _InitialResponse.MESSAGE
                return Response(self._interaction, None)

            message = await self._interaction.execute(
                content,
                flags=flags,
                tts=tts,
                attachment=attachment,
                attachments=attachments,
                component=component,
                components=components,
                embed=embed,
                embeds=embeds,
                mentions_everyone=mentions_everyone,
                user_mentions=user_mentions,
                role_mentions=role_mentions,
            )
            return Response(self._interaction, message)


class ComponentContext(Context[hikari.ComponentInteraction]):
    """The interaction a component handler is answering.

    A component always belongs to a message, so :attr:`message` is always
    there, the message can always be redrawn, and a component is the one
    interaction Discord permits to open a modal.
    """

    __slots__ = ()

    @property
    @typing.override
    def message(self) -> hikari.Message:
        """The message the component is attached to."""
        return self._interaction.message

    @property
    def values(self) -> collections.abc.Sequence[str]:
        """What was selected, for a select menu.

        Empty for a button. A menu of users, roles, mentionables or channels
        yields snowflakes as strings; the objects behind them are in
        :attr:`resolved`.
        """
        return self._interaction.values

    @property
    def resolved(self) -> hikari.ResolvedOptionData | None:
        """The objects behind the selected snowflakes, if any were resolved."""
        return self._interaction.resolved

    async def rerender(self) -> None:
        """Redraw the component's message from the view's current state.

        The headline ergonomic: mutate ``self`` in a handler, call this, done.
        Literally :meth:`edit` of what ``render()`` returns right now, so the
        message always shows the state -- and because the state key is reused,
        the redrawn components keep the same ``custom_id``, and a click already
        in flight is never invalidated.

        Does not write state to the store; dispatch persists the view once,
        after the handler returns.

        Raises
        ------
        RuntimeError
            If there is no view instance to render -- inside
            ``on_state_missing`` or ``on_outdated``, whose reason for running
            is precisely that no view could be rebuilt. Use :meth:`edit` there.
        """
        if self._state.view is None:
            msg = "rerender needs a view instance and this context has none; answer with edit() or respond() instead"
            raise RuntimeError(msg)
        await self.edit(self._state.view.render())

    async def edit(self, layout: nodes.Layout) -> None:
        """Replace the component's message with an explicitly given tree.

        The escape hatch under :meth:`rerender`, for showing something the
        view's state does not describe -- most often a terminal screen::

            await ctx.edit(ui.TextDisplay("Poll closed."))

        The tree is built against the same view and state key, so any
        interactive components in it still route -- but the message no longer
        matches ``render(state)``, and the next ``rerender`` will overwrite it.
        Prefer trees without interactive components here; anything longer-lived
        belongs in state.

        Always lands on the component's message, whatever was responded
        before: as the initial response when nothing was, through the deferred
        initial response after a silent ack, or as a plain message edit once
        the interaction's own channels point elsewhere.

        Parameters
        ----------
        layout
            The tree to show, as one top-level component or several.

        Raises
        ------
        RuntimeError
            If the context was never dispatched by risa, and so has no view
            metadata to build against.
        LayoutError
            If a component routes to a handler the view does not have.
        CustomIdOverflowError
            If an encoded ``custom_id`` would exceed Discord's length limit.
        """
        if self._state.meta is None:
            msg = "edit() needs the view metadata risa attaches during dispatch; this context has none"
            raise RuntimeError(msg)
        anchor = anchor_.StoreAnchor(key=self._state.state_key).encode() if self._state.state_key else ""
        builders = build_.build(layout, meta=self._state.meta, anchor=anchor)

        async with self._state.lock:
            if self._state.initial is _InitialResponse.NONE:
                self._state.cancel_autodefer()
                await self._interaction.create_initial_response(
                    hikari.ResponseType.MESSAGE_UPDATE,
                    components=builders,
                )
                self._state.transition(_InitialResponse.ORIGIN_EDIT)
                return

            if self._state.initial in {_InitialResponse.UPDATE_DEFERRED, _InitialResponse.ORIGIN_EDIT}:
                await self._interaction.edit_initial_response(components=builders)
                self._state.initial = _InitialResponse.ORIGIN_EDIT
                return

            await self._interaction.message.edit(components=builders)


class ModalContext(Context[hikari.ModalInteraction]):
    """The interaction a modal handler is answering.

    A modal opened from a component belongs to that component's message, but one
    opened from a command does not, which is why :attr:`message` is optional.
    """

    __slots__ = ()

    @property
    @typing.override
    def message(self) -> hikari.Message | None:
        """The message whose component opened the modal, if one did."""
        return self._interaction.message

    @property
    def components(self) -> collections.abc.Sequence[hikari.ModalActionRowComponent]:
        """The submitted rows, as Discord returned them."""
        return self._interaction.components

    @property
    def values(self) -> collections.abc.Mapping[str, str]:
        """What was submitted, keyed by the custom id of each text input."""
        return {component.custom_id: component.value for row in self._interaction.components for component in row}


async def _issue_deferral(interaction: InteractionT, state: DispatchState, *, thinking: bool, ephemeral: bool) -> None:
    """Send a deferred initial response. The caller holds the state's lock."""
    await interaction.create_initial_response(
        hikari.ResponseType.DEFERRED_MESSAGE_CREATE if thinking else hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
        flags=hikari.MessageFlag.EPHEMERAL if thinking and ephemeral else hikari.UNDEFINED,
    )
    state.transition(_InitialResponse.THINKING_DEFERRED if thinking else _InitialResponse.UPDATE_DEFERRED)


async def _autodefer_now(interaction: InteractionT, state: DispatchState) -> None:
    """Defer immediately unless a response won the race first.

    A failed deferral is logged rather than raised: the watchdog runs in its
    own task, and leaving the gate unopened lets a later response call still
    try to answer the interaction.
    """
    async with state.lock:
        if state.initial is not _InitialResponse.NONE or state.autodefer_options is None:
            return
        thinking, ephemeral = state.autodefer_options
        try:
            await _issue_deferral(interaction, state, thinking=thinking, ephemeral=ephemeral)
        except hikari.HikariError:
            _LOGGER.exception("auto-defer failed for interaction %s", interaction.id)


async def _run_autodefer(interaction: InteractionT, state: DispatchState) -> None:
    """Wait out the watchdog's grace period, then defer if nothing beat it."""
    await asyncio.sleep(constants.AUTODEFER_DELAY)
    await _autodefer_now(interaction, state)


def prepare_dispatch(
    state: DispatchState,
    interaction: InteractionT,
    *,
    meta: registry.ViewMeta,
    state_key: str | None,
    autodefer: view_.AutoDefer,
) -> None:
    """Wire a dispatch state to its view and start the auto-defer watchdog.

    Internal dispatch plumbing shared by the transports; not public API.

    Parameters
    ----------
    state
        The dispatch state behind the context being dispatched.
    interaction
        The interaction being answered.
    meta
        The view the interaction resolved to.
    state_key
        The state key from the ``custom_id``, or ``None`` for a view that
        stores nothing.
    autodefer
        What the watchdog should send, or :attr:`~risa.view.AutoDefer.OFF` to
        run none.
    """
    state.meta = meta
    state.state_key = state_key
    if autodefer is view_.AutoDefer.OFF:
        return
    thinking = autodefer is not view_.AutoDefer.UPDATE
    ephemeral = autodefer is view_.AutoDefer.THINKING_EPHEMERAL
    state.autodefer_options = (thinking, ephemeral)
    state.autodefer_task = asyncio.create_task(_run_autodefer(interaction, state))


def supply_view(state: DispatchState, view: view_.View) -> None:
    """Give a dispatch state the rebuilt view instance ``rerender`` needs.

    Internal dispatch plumbing; not public API.

    Parameters
    ----------
    state
        The dispatch state behind the context being dispatched.
    view
        The view instance the handler will run on.
    """
    state.view = view


async def conclude_dispatch(state: DispatchState, interaction: InteractionT, *, failed: bool) -> None:
    """Settle the watchdog and open the acknowledgement gate.

    Internal dispatch plumbing; not public API. After a clean dispatch that
    never responded, the deferred ack is issued immediately -- silent, and
    exactly what a handler that only did background work wants. After a failed
    one the watchdog is cancelled instead, so the failure stays visible rather
    than being acked into silence.

    Parameters
    ----------
    state
        The dispatch state behind the context whose dispatch ended.
    interaction
        The interaction that was being answered.
    failed
        Whether dispatch is ending on an exception.
    """
    task = state.autodefer_task
    state.autodefer_task = None
    if task is not None:
        task.cancel()
        if not failed:
            await _autodefer_now(interaction, state)
    state.acknowledged.set()
