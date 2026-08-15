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

import asyncio
import enum
import typing

import hikari
import msgspec

from risa import errors
from risa import ui

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import view as view_
    from risa.internal import registry

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
    lock: asyncio.Lock = msgspec.field(default_factory=asyncio.Lock)
    acknowledged: asyncio.Event = msgspec.field(default_factory=asyncio.Event)
    response: _InitialResponse = msgspec.field(default=_InitialResponse.NONE)
    adopted: bool = False

    @property
    def responded(self) -> bool:
        return self.response is not _InitialResponse.NONE


class Response:
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
        if self._message is not None:
            return await self._rest.fetch_webhook_message(
                self._interaction.application_id,
                self._interaction.token,
                self._message.id,
            )
        return await self._rest.fetch_interaction_response(self._interaction.application_id, self._interaction.token)

    async def delete(self) -> None:
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
    __slots__ = ("_gate", "_interaction", "_rest")

    def __init__(self, interaction: T, *, rest: hikari.api.RESTClient, gate: ResponseGate | None = None) -> None:
        self._interaction = interaction
        self._rest = rest
        self._gate = gate if gate is not None else ResponseGate()

    @property
    def responded(self) -> bool:
        return self._gate.responded

    @property
    def rest(self) -> hikari.api.RESTClient:
        return self._rest

    @property
    def interaction(self) -> T:
        return self._interaction

    @property
    def custom_id(self) -> str:
        return self._interaction.custom_id

    @property
    def user(self) -> hikari.User:
        return self._interaction.user

    @property
    def member(self) -> hikari.InteractionMember | None:
        return self._interaction.member

    @property
    def channel_id(self) -> hikari.Snowflake:
        return self._interaction.channel_id

    @property
    def guild_id(self) -> hikari.Snowflake | None:
        return self._interaction.guild_id

    @property
    def message(self) -> hikari.Message | None:
        return self._interaction.message

    async def defer(self, *, thinking: bool = False, ephemeral: bool = False) -> None:
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
    __slots__ = ("_meta", "_view")

    def __init__(
        self,
        interaction: hikari.ComponentInteraction,
        *,
        rest: hikari.api.RESTClient,
        view: view_.View,
        meta: registry.ViewMeta,
        gate: ResponseGate | None = None,
    ) -> None:
        super().__init__(interaction, rest=rest, gate=gate)
        self._view = view
        self._meta = meta

    @property
    @typing.override
    def message(self) -> hikari.Message:
        return self._interaction.message

    @property
    def values(self) -> collections.abc.Sequence[str]:
        return self._interaction.values

    @property
    def resolved(self) -> hikari.ResolvedOptionData | None:
        return self._interaction.resolved

    async def edit(self, layout: ui.Layout, /) -> None:
        builders = ui.build(layout, self._meta)
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
                if hikari.MessageFlag.EPHEMERAL in self.message.flags:
                    raise errors.EphemeralOriginError(self._meta.name)
                await self._rest.edit_message(self.message.channel_id, self.message.id, components=builders)

    async def rerender(self) -> None:
        await self.edit(self._view.render())
