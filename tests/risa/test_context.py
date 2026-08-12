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

import unittest.mock

import hikari
import pytest

import risa


def component_interaction() -> unittest.mock.Mock:
    return unittest.mock.Mock(spec=hikari.ComponentInteraction)


def mock_message(message_id: int) -> unittest.mock.Mock:
    return unittest.mock.Mock(spec=hikari.Message, id=hikari.Snowflake(message_id))


def test_the_context_exposes_the_interaction_it_was_built_from() -> None:
    interaction = component_interaction()

    ctx = risa.ComponentContext(interaction)

    assert ctx.interaction is interaction


def test_the_interaction_is_read_only() -> None:
    ctx = risa.ComponentContext(component_interaction())

    with pytest.raises(AttributeError):
        ctx.interaction = component_interaction()  # type: ignore[reportAttributeAccessIssue]


def test_the_context_holds_no_attributes_beyond_its_slots() -> None:
    ctx = risa.ComponentContext(component_interaction())

    with pytest.raises(AttributeError):
        ctx.extra = None  # type: ignore[reportAttributeAccessIssue]


async def test_the_first_respond_becomes_the_initial_response() -> None:
    interaction = component_interaction()
    ctx = risa.ComponentContext(interaction)

    await ctx.respond("pong")

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.MESSAGE_CREATE
    assert call.args[1] == "pong"
    interaction.execute.assert_not_called()


async def test_every_later_respond_is_a_followup() -> None:
    interaction = component_interaction()
    interaction.execute.return_value = mock_message(999)
    ctx = risa.ComponentContext(interaction)

    await ctx.respond("one")
    await ctx.respond("two")

    interaction.create_initial_response.assert_awaited_once()
    call = interaction.execute.await_args
    assert call is not None
    assert call.args[0] == "two"


async def test_a_respond_after_a_defer_is_a_followup() -> None:
    interaction = component_interaction()
    interaction.execute.return_value = mock_message(999)
    ctx = risa.ComponentContext(interaction)

    await ctx.defer()
    await ctx.respond("late")

    interaction.execute.assert_awaited_once()


async def test_an_ephemeral_respond_sets_the_flag() -> None:
    interaction = component_interaction()
    ctx = risa.ComponentContext(interaction)

    await ctx.respond("secret", ephemeral=True)

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.kwargs["flags"] is hikari.MessageFlag.EPHEMERAL


async def test_defer_defaults_to_the_silent_update_ack() -> None:
    interaction = component_interaction()
    ctx = risa.ComponentContext(interaction)

    await ctx.defer()

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE
    assert call.kwargs["flags"] is hikari.UNDEFINED


async def test_a_thinking_defer_shows_the_spinner() -> None:
    interaction = component_interaction()
    ctx = risa.ComponentContext(interaction)

    await ctx.defer(thinking=True, ephemeral=True)

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    assert call.kwargs["flags"] is hikari.MessageFlag.EPHEMERAL


async def test_a_second_defer_is_refused() -> None:
    ctx = risa.ComponentContext(component_interaction())
    await ctx.defer()

    with pytest.raises(risa.AlreadyRespondedError) as exc_info:
        await ctx.defer()

    assert exc_info.value.attempted == "defer"
    assert exc_info.value.already_sent == "DEFERRED_UPDATE"


async def test_a_defer_after_a_respond_is_refused() -> None:
    ctx = risa.ComponentContext(component_interaction())
    await ctx.respond("hi")

    with pytest.raises(risa.AlreadyRespondedError) as exc_info:
        await ctx.defer()

    assert exc_info.value.already_sent == "MESSAGE_CREATE"


async def test_the_initial_response_handle_uses_the_initial_endpoints() -> None:
    interaction = component_interaction()
    ctx = risa.ComponentContext(interaction)

    response = await ctx.respond("hi")
    await response.fetch()
    await response.edit("edited")
    await response.delete()

    interaction.fetch_initial_response.assert_awaited_once()
    interaction.edit_initial_response.assert_awaited_once()
    interaction.delete_initial_response.assert_awaited_once()
    interaction.fetch_message.assert_not_called()


async def test_a_followup_handle_targets_its_own_message() -> None:
    interaction = component_interaction()
    interaction.execute.return_value = mock_message(999)
    ctx = risa.ComponentContext(interaction)

    await ctx.respond("one")
    followup = await ctx.respond("two")
    await followup.fetch()
    await followup.edit("edited")
    await followup.delete()

    interaction.fetch_message.assert_awaited_once_with(999)
    interaction.delete_message.assert_awaited_once_with(999)
    call = interaction.edit_message.await_args
    assert call is not None
    assert call.args[0] == 999
    interaction.fetch_initial_response.assert_not_called()


def test_the_context_exposes_select_values() -> None:
    interaction = component_interaction()
    interaction.values = ["red", "blue"]
    ctx = risa.ComponentContext(interaction)

    assert ctx.values == ["red", "blue"]


def test_the_context_exposes_resolved_entities() -> None:
    interaction = component_interaction()
    ctx = risa.ComponentContext(interaction)

    assert ctx.resolved is interaction.resolved


async def test_the_first_response_sets_the_acknowledged_event() -> None:
    state = risa.DispatchState()
    ctx = risa.ComponentContext(component_interaction(), state)

    assert not state.acknowledged.is_set()
    await ctx.defer()
    assert state.acknowledged.is_set()
