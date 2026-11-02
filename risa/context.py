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
"""The contexts handlers and hooks answer interactions through."""

from __future__ import annotations

import asyncio
import enum
import typing

import hikari
import msgspec

from risa import errors
from risa.ui import build as build_

if typing.TYPE_CHECKING:
    import collections.abc

    from hikari.api import special_endpoints

    from risa import ui
    from risa import view as view_
    from risa.internal import registry
    from risa.state import backend

__all__ = (
    "ComponentContext",
    "Context",
    "Response",
    "ResponseGate",
)


class _InitialResponse(enum.StrEnum):
    NONE = enum.auto()
    DEFERRED_UPDATE = enum.auto()
    DEFERRED_THINKING = enum.auto()
    MESSAGE_CREATE = enum.auto()
    MESSAGE_UPDATE = enum.auto()


class ResponseGate(msgspec.Struct):
    """Whether an interaction has been answered, shared by everything that answers it.

    risa makes one per interaction, so a handler and the auto-defer watchdog never both
    send the initial response.
    """

    lock: asyncio.Lock = msgspec.field(default_factory=asyncio.Lock)
    acknowledged: asyncio.Event = msgspec.field(default_factory=asyncio.Event)
    response: _InitialResponse = msgspec.field(default=_InitialResponse.NONE)
    adopted: bool = False

    @property
    def responded(self) -> bool:
        """Whether the interaction has its initial response."""
        return self.response is not _InitialResponse.NONE


class ViewSlot(msgspec.Struct):
    view: view_.View | None = None
    session: backend.StateSession | None = None


class Response:
    """A message sent with `respond()`, to fetch, edit or delete later."""

    __slots__ = ("_interaction", "_message", "_rest")

    def __init__(
        self,
        rest: hikari.api.RESTClient,
        interaction: hikari.ComponentInteraction | hikari.ModalInteraction,
        message: hikari.Message | None,
    ) -> None:
        self._rest = rest
        self._interaction = interaction
        self._message = message

    async def fetch(self) -> hikari.Message:
        """Fetch the message as it is now."""
        if self._message is not None:
            return await self._rest.fetch_webhook_message(
                self._interaction.application_id,
                self._interaction.token,
                self._message.id,
            )
        return await self._rest.fetch_interaction_response(self._interaction.application_id, self._interaction.token)

    async def delete(self) -> None:
        """Delete the message."""
        if self._message is not None:
            await self._rest.delete_webhook_message(
                self._interaction.application_id,
                self._interaction.token,
                self._message.id,
            )
        else:
            await self._rest.delete_interaction_response(self._interaction.application_id, self._interaction.token)

    async def edit(  # ruff:ignore[too-many-arguments]
        self,
        content: hikari.UndefinedNoneOr[str] = hikari.UNDEFINED,
        *,
        attachment: hikari.UndefinedNoneOr[hikari.Resourceish | hikari.Attachment] = hikari.UNDEFINED,
        attachments: hikari.UndefinedNoneOr[
            collections.abc.Sequence[hikari.Resourceish | hikari.Attachment]
        ] = hikari.UNDEFINED,
        component: hikari.UndefinedNoneOr[hikari.api.ComponentBuilder] = hikari.UNDEFINED,
        components: hikari.UndefinedNoneOr[collections.abc.Sequence[hikari.api.ComponentBuilder]] = hikari.UNDEFINED,
        embed: hikari.UndefinedNoneOr[hikari.Embed] = hikari.UNDEFINED,
        embeds: hikari.UndefinedNoneOr[collections.abc.Sequence[hikari.Embed]] = hikari.UNDEFINED,
        mentions_everyone: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        user_mentions: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.PartialUser] | bool] = hikari.UNDEFINED,
        role_mentions: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.PartialRole] | bool] = hikari.UNDEFINED,
    ) -> hikari.Message:
        """Edit the message. The parameters are those of hikari's message edits."""
        if self._message is not None:
            return await self._rest.edit_webhook_message(
                self._interaction.application_id,
                self._interaction.token,
                self._message.id,
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
        return await self._rest.edit_interaction_response(
            self._interaction.application_id,
            self._interaction.token,
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


class Context[T: hikari.ComponentInteraction | hikari.ModalInteraction]:
    """What a handler or hook gets for the interaction it answers."""

    __slots__ = ("_gate", "_interaction", "_rest")

    def __init__(self, interaction: T, *, rest: hikari.api.RESTClient, gate: ResponseGate | None = None) -> None:
        self._interaction = interaction
        self._rest = rest
        self._gate = gate if gate is not None else ResponseGate()

    @property
    def responded(self) -> bool:
        """Whether the interaction has its initial response."""
        return self._gate.responded

    @property
    def rest(self) -> hikari.api.RESTClient:
        """The REST client risa answers through."""
        return self._rest

    @property
    def interaction(self) -> T:
        """The interaction being answered."""
        return self._interaction

    @property
    def custom_id(self) -> str:
        """The `custom_id` of the component that was used."""
        return self._interaction.custom_id

    @property
    def user(self) -> hikari.User:
        """The user who used the component."""
        return self._interaction.user

    @property
    def member(self) -> hikari.InteractionMember | None:
        """The member who used the component, or `None` outside a guild."""
        return self._interaction.member

    @property
    def channel_id(self) -> hikari.Snowflake:
        """The id of the channel the interaction happened in."""
        return self._interaction.channel_id

    @property
    def guild_id(self) -> hikari.Snowflake | None:
        """The id of the guild the interaction happened in, or `None` outside a guild."""
        return self._interaction.guild_id

    @property
    def message(self) -> hikari.Message | None:
        """The message the component is on, if any."""
        return self._interaction.message

    async def defer(self, *, thinking: bool = False, ephemeral: bool = False) -> None:
        """Acknowledge the interaction now and answer it later.

        By default the acknowledgement is silent, and the answer is a `rerender()` or an
        `edit()` of the message. With `thinking=True`, Discord shows a "thinking..." message
        that the next `respond()` takes the place of.

        Parameters
        ----------
        thinking
            Show a "thinking..." message.
        ephemeral
            Show that message only to the user who clicked.

        Raises
        ------
        AlreadyRespondedError
            If the interaction already has its initial response.
        """
        async with self._gate.lock:
            if self._gate.response is not _InitialResponse.NONE:
                attempted = "defer"
                raise errors.AlreadyRespondedError(attempted, self._gate.response.name)
            await self._rest.create_interaction_response(
                self._interaction.id,
                self._interaction.token,
                hikari.ResponseType.DEFERRED_MESSAGE_CREATE
                if thinking
                else hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
                flags=hikari.MessageFlag.EPHEMERAL if ephemeral else hikari.UNDEFINED,
            )
            self._record_initial(_InitialResponse.DEFERRED_THINKING if thinking else _InitialResponse.DEFERRED_UPDATE)

    async def acknowledge(self, *, thinking: bool = False, ephemeral: bool = False) -> None:
        """Acknowledge the interaction like `defer`, unless it already has its initial response.

        Where `defer` raises, this does nothing, so it is safe to call whatever happened
        before.
        """
        async with self._gate.lock:
            if self._gate.response is not _InitialResponse.NONE:
                return
            await self._rest.create_interaction_response(
                self._interaction.id,
                self._interaction.token,
                hikari.ResponseType.DEFERRED_MESSAGE_CREATE
                if thinking
                else hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
                flags=hikari.MessageFlag.EPHEMERAL if ephemeral else hikari.UNDEFINED,
            )
            self._record_initial(_InitialResponse.DEFERRED_THINKING if thinking else _InitialResponse.DEFERRED_UPDATE)

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
        """Send a message in answer to the interaction.

        The first response answers the interaction itself; every later one is a follow-up
        message. Apart from `ephemeral`, the parameters are hikari's.

        Parameters
        ----------
        ephemeral
            Show the message only to the user who clicked.

        Returns
        -------
        Response
            The message, to fetch, edit or delete later.
        """
        flags = hikari.MessageFlag.EPHEMERAL if ephemeral else hikari.UNDEFINED

        async with self._gate.lock:
            if self._gate.response is _InitialResponse.NONE:
                await self._rest.create_interaction_response(
                    self._interaction.id,
                    self._interaction.token,
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
                self._record_initial(_InitialResponse.MESSAGE_CREATE)
                return Response(self._rest, self._interaction, None)

            message = await self._rest.execute_webhook(
                self._interaction.application_id,
                self._interaction.token,
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
            return Response(self._rest, self._interaction, message)

    def _record_initial(self, response: _InitialResponse) -> None:
        self._gate.response = response
        self._gate.acknowledged.set()


class ComponentContext(Context[hikari.ComponentInteraction]):
    """The context of a click on one of a view's components.

    A handler answers through it: `rerender()` redraws the message from the view's
    state, `edit(...)` replaces it with another layout, and `respond(...)` sends a new
    message.
    """

    __slots__ = ("_meta", "_slot")

    def __init__(
        self,
        interaction: hikari.ComponentInteraction,
        *,
        rest: hikari.api.RESTClient,
        meta: registry.ViewMeta,
        gate: ResponseGate | None = None,
        slot: ViewSlot | None = None,
    ) -> None:
        super().__init__(interaction, rest=rest, gate=gate)
        self._meta = meta
        self._slot = slot if slot is not None else ViewSlot()

    @property
    @typing.override
    def message(self) -> hikari.Message:
        return self._interaction.message

    @property
    def values(self) -> collections.abc.Sequence[str]:
        """The values picked in a select menu; empty for a button."""
        return self._interaction.values

    @property
    def resolved(self) -> hikari.ResolvedOptionData | None:
        """The users, members, roles and channels picked in an entity select menu, or `None`."""
        return self._interaction.resolved

    async def edit(self, layout: ui.Layout, /) -> None:
        """Replace the message with `layout`, keeping the view's state on it.

        For a screen the view's `render` does not draw, such as a confirmation or a closing
        message. A layout without interactive components ends the view, since nothing on it
        can be clicked any more. Like `rerender()`, it saves the view's state.

        Raises
        ------
        NoViewError
            If the layout has interactive components but this context has no view state, as
            in `on_outdated` and `on_state_missing`.
        EphemeralOriginError
            If the message is ephemeral and the interaction was answered with a new message.
        StateOverflowError
            If an `InMessage` view's state does not fit the layout's components.
        StateConflictError
            If another process saved the view's state first. The view then holds that state.
        """
        plan = build_.plan(layout, self._meta)
        view, session = self._slot.view, self._slot.session
        if view is not None and session is not None:
            await self._commit(view, session, plan)
            return
        if plan.interactive and self._meta.schema.durable:
            raise errors.NoViewError(self._meta.name, "edit() with interactive components")
        await self._update_origin(plan.emit(b""))

    async def rerender(self) -> None:
        """Redraw the message from the view's current state, and save that state.

        Every rerender is a commit. `InMessage` state is written into the message itself;
        `InStore` state goes to the store first, then the message is edited. A change made
        without calling it is saved when the handler returns, but that save can only log a
        failure; call it yourself, before any `respond()`, where a failure must be handled.

        Raises
        ------
        NoViewError
            If the view has durable fields but this context has no view state, as in
            `on_outdated` and `on_state_missing`.
        EphemeralOriginError
            If the message is ephemeral and the interaction was answered with a new message.
        StateOverflowError
            If an `InMessage` view's state no longer fits its components.
        StateConflictError
            If another process saved the view's state first. The view then holds that state,
            and the handler can apply its change again and rerender.
        StateNotFoundError
            If an `InStore` view's record disappeared while the handler ran.
        """
        view, session = self._slot.view, self._slot.session
        if view is not None and session is not None:
            await self._commit(view, session, build_.plan(view.render(), self._meta))
            return
        if self._meta.schema.durable:
            raise errors.NoViewError(self._meta.name, "rerender()")
        await self._update_origin(build_.plan(self._meta.cls().render(), self._meta).emit(b""))

    async def _commit(self, view: view_.View, session: backend.StateSession, plan: build_.Plan) -> None:
        if self._origin_is_out_of_reach():
            raise errors.EphemeralOriginError(self._meta.name)
        state = await session.stage(view)
        await self._update_origin(plan.emit(state))
        await session.published(view)

    async def _update_origin(self, builders: collections.abc.Sequence[special_endpoints.ComponentBuilder]) -> None:
        async with self._gate.lock:
            if self._gate.response is _InitialResponse.NONE:
                await self._rest.create_interaction_response(
                    self._interaction.id,
                    self._interaction.token,
                    hikari.ResponseType.MESSAGE_UPDATE,
                    components=builders,
                )
                self._record_initial(_InitialResponse.MESSAGE_UPDATE)
            elif self._gate.response in {_InitialResponse.DEFERRED_UPDATE, _InitialResponse.MESSAGE_UPDATE}:
                await self._rest.edit_interaction_response(
                    self._interaction.application_id,
                    self._interaction.token,
                    components=builders,
                )
            else:
                if self._origin_is_out_of_reach():
                    raise errors.EphemeralOriginError(self._meta.name)
                await self._rest.edit_message(self.message.channel_id, self.message.id, components=builders)

    def _origin_is_out_of_reach(self) -> bool:
        answered_elsewhere = self._gate.response in {
            _InitialResponse.DEFERRED_THINKING,
            _InitialResponse.MESSAGE_CREATE,
        }
        return answered_elsewhere and hikari.MessageFlag.EPHEMERAL in self.message.flags
