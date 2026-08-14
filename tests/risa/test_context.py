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

import typing
import unittest.mock

import hikari
import pytest

import risa
from risa import ui
from risa.internal import constants
from risa.internal import registry


@risa.register(name="context-anchor")
class Anchor(risa.View):
    note: str = "hello"

    def render(self) -> ui.Layout:
        return ui.TextDisplay(self.note)


def component_ctx(
    interaction: unittest.mock.Mock,
    state: risa.DispatchState | None = None,
    view: Anchor | None = None,
) -> risa.ComponentContext:
    meta = getattr(Anchor, constants.VIEW_META)
    assert isinstance(meta, registry.ViewMeta)
    return risa.ComponentContext(
        interaction,
        rest=unittest.mock.Mock(spec=hikari.api.RESTClient),
        view=view if view is not None else Anchor(),
        meta=meta,
        state=state,
    )


def rest_of(ctx: risa.ComponentContext) -> unittest.mock.Mock:
    return typing.cast("unittest.mock.Mock", ctx.rest)


def component_interaction() -> unittest.mock.Mock:
    return unittest.mock.Mock(spec=hikari.ComponentInteraction)


def mock_message(message_id: int) -> unittest.mock.Mock:
    return unittest.mock.Mock(spec=hikari.Message, id=hikari.Snowflake(message_id))


def test_the_context_exposes_the_interaction_it_was_built_from() -> None:
    interaction = component_interaction()

    ctx = component_ctx(interaction)

    assert ctx.interaction is interaction


def test_the_interaction_is_read_only() -> None:
    ctx = component_ctx(component_interaction())

    with pytest.raises(AttributeError):
        ctx.interaction = component_interaction()  # type: ignore[reportAttributeAccessIssue]


def test_the_context_holds_no_attributes_beyond_its_slots() -> None:
    ctx = component_ctx(component_interaction())

    with pytest.raises(AttributeError):
        ctx.extra = None  # type: ignore[reportAttributeAccessIssue]


async def test_the_first_respond_becomes_the_initial_response() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    await ctx.respond("pong")

    call = rest_of(ctx).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.MESSAGE_CREATE
    assert call.args[3] == "pong"
    rest_of(ctx).execute_webhook.assert_not_called()


async def test_every_later_respond_is_a_followup() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)
    rest_of(ctx).execute_webhook.return_value = mock_message(999)

    await ctx.respond("one")
    await ctx.respond("two")

    rest_of(ctx).create_interaction_response.assert_awaited_once()
    call = rest_of(ctx).execute_webhook.await_args
    assert call is not None
    assert call.args[2] == "two"


async def test_a_respond_after_a_defer_is_a_followup() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)
    rest_of(ctx).execute_webhook.return_value = mock_message(999)

    await ctx.defer()
    await ctx.respond("late")

    rest_of(ctx).execute_webhook.assert_awaited_once()


async def test_an_ephemeral_respond_sets_the_flag() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    await ctx.respond("secret", ephemeral=True)

    call = rest_of(ctx).create_interaction_response.await_args
    assert call is not None
    assert call.kwargs["flags"] is hikari.MessageFlag.EPHEMERAL


async def test_defer_defaults_to_the_silent_update_ack() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    await ctx.defer()

    call = rest_of(ctx).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE
    assert call.kwargs["flags"] is hikari.UNDEFINED


async def test_a_thinking_defer_shows_the_spinner() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    await ctx.defer(thinking=True, ephemeral=True)

    call = rest_of(ctx).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    assert call.kwargs["flags"] is hikari.MessageFlag.EPHEMERAL


async def test_a_second_defer_is_refused() -> None:
    ctx = component_ctx(component_interaction())
    await ctx.defer()

    with pytest.raises(risa.AlreadyRespondedError) as exc_info:
        await ctx.defer()

    assert exc_info.value.attempted == "defer"
    assert exc_info.value.already_sent == "DEFERRED_UPDATE"


async def test_a_defer_after_a_respond_is_refused() -> None:
    ctx = component_ctx(component_interaction())
    await ctx.respond("hi")

    with pytest.raises(risa.AlreadyRespondedError) as exc_info:
        await ctx.defer()

    assert exc_info.value.already_sent == "MESSAGE_CREATE"


async def test_the_initial_response_handle_uses_the_initial_endpoints() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    response = await ctx.respond("hi")
    await response.fetch()
    await response.edit("edited")
    await response.delete()

    rest_of(ctx).fetch_interaction_response.assert_awaited_once()
    rest_of(ctx).edit_interaction_response.assert_awaited_once()
    rest_of(ctx).delete_interaction_response.assert_awaited_once()
    rest_of(ctx).fetch_webhook_message.assert_not_called()


async def test_a_followup_handle_targets_its_own_message() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)
    rest_of(ctx).execute_webhook.return_value = mock_message(999)

    await ctx.respond("one")
    followup = await ctx.respond("two")
    await followup.fetch()
    await followup.edit("edited")
    await followup.delete()

    fetch = rest_of(ctx).fetch_webhook_message.await_args
    assert fetch is not None
    assert fetch.args[2] == 999
    delete = rest_of(ctx).delete_webhook_message.await_args
    assert delete is not None
    assert delete.args[2] == 999
    edit = rest_of(ctx).edit_webhook_message.await_args
    assert edit is not None
    assert edit.args[2] == 999
    rest_of(ctx).fetch_interaction_response.assert_not_called()


def test_the_context_exposes_select_values() -> None:
    interaction = component_interaction()
    interaction.values = ["red", "blue"]
    ctx = component_ctx(interaction)

    assert ctx.values == ["red", "blue"]


def test_the_context_exposes_resolved_entities() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    assert ctx.resolved is interaction.resolved


async def test_the_first_response_sets_the_acknowledged_event() -> None:
    state = risa.DispatchState()
    ctx = component_ctx(component_interaction(), state)

    assert not state.acknowledged.is_set()
    await ctx.defer()
    assert state.acknowledged.is_set()


async def test_edit_with_nothing_sent_is_the_initial_message_update() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    await ctx.edit(ui.TextDisplay("changed"))

    call = rest_of(ctx).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.MESSAGE_UPDATE
    assert ctx.responded


async def test_edit_after_a_silent_defer_edits_the_initial_response() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    await ctx.defer()
    await ctx.edit(ui.TextDisplay("changed"))

    rest_of(ctx).create_interaction_response.assert_awaited_once()
    rest_of(ctx).edit_interaction_response.assert_awaited_once()


async def test_a_second_edit_edits_the_initial_response() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction)

    await ctx.edit(ui.TextDisplay("one"))
    await ctx.edit(ui.TextDisplay("two"))

    rest_of(ctx).create_interaction_response.assert_awaited_once()
    rest_of(ctx).edit_interaction_response.assert_awaited_once()


async def test_edit_after_a_thinking_defer_edits_the_origin_message() -> None:
    interaction = component_interaction()
    interaction.message = mock_message(123)
    ctx = component_ctx(interaction)

    await ctx.defer(thinking=True)
    await ctx.edit(ui.TextDisplay("changed"))

    rest_of(ctx).edit_message.assert_awaited_once()
    rest_of(ctx).edit_interaction_response.assert_not_called()


async def test_edit_after_a_respond_edits_the_origin_message() -> None:
    interaction = component_interaction()
    interaction.message = mock_message(123)
    ctx = component_ctx(interaction)

    await ctx.respond("hi")
    await ctx.edit(ui.TextDisplay("changed"))

    rest_of(ctx).edit_message.assert_awaited_once()
    rest_of(ctx).edit_interaction_response.assert_not_called()


async def test_edit_sets_the_acknowledged_event() -> None:
    state = risa.DispatchState()
    ctx = component_ctx(component_interaction(), state)

    await ctx.edit(ui.TextDisplay("x"))

    assert state.acknowledged.is_set()


async def test_rerender_paints_what_render_returns() -> None:
    interaction = component_interaction()
    ctx = component_ctx(interaction, view=Anchor(note="fresh"))

    await ctx.rerender()

    call = rest_of(ctx).create_interaction_response.await_args
    assert call is not None
    (sent,) = call.kwargs["components"]
    payload, _attachments = sent.build()
    assert payload["content"] == "fresh"
