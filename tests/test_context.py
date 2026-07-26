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
import typing
import unittest.mock

import hikari
import pytest

import risa
from risa import ui
from risa.internal import codec
from tests.helpers import RecordingStore
from tests.helpers import StubClient
from tests.helpers import all_custom_ids
from tests.helpers import builder_custom_ids
from tests.helpers import first_custom_id
from tests.helpers import interaction
from tests.helpers import mock_of


@risa.register(name="ctx-counter", version=1)
class Counter(risa.View):
    count: int = 0

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.bump, label=str(self.count)))

    @risa.handler
    async def bump(self, ctx: risa.ComponentContext) -> None:
        self.count += 1
        await ctx.rerender()

    @risa.handler
    async def finish(self, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay("done"))

    @risa.handler
    async def greet(self, ctx: risa.ComponentContext) -> None:
        await ctx.respond("hi", ephemeral=True)


@risa.register(name="ctx-slow", version=1)
class Slow(risa.View):
    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(
            ui.Button(self.redraw, label="r"),
            ui.Button(self.reply, label="p"),
        )

    @risa.handler
    async def redraw(self, ctx: risa.ComponentContext) -> None:
        await asyncio.sleep(0.15)
        await ctx.rerender()

    @risa.handler(defer=risa.AutoDefer.THINKING)
    async def reply(self, ctx: risa.ComponentContext) -> None:
        await asyncio.sleep(0.15)
        await ctx.respond("done")


@risa.register(name="ctx-quiet", version=1)
class Quiet(risa.View):
    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.nothing, label="n"))

    @risa.handler
    async def nothing(self, _: risa.ComponentContext) -> None: ...


@risa.register(name="ctx-off", version=1, defer=risa.AutoDefer.OFF)
class Off(risa.View):
    finished: typing.ClassVar[int] = 0

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.linger, label="l"))

    @risa.handler
    async def linger(self, _: risa.ComponentContext) -> None:
        await asyncio.sleep(0.2)
        type(self).finished += 1


@risa.register(name="ctx-boom", version=1)
class Boom(risa.View):
    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.explode, label="b"))

    @risa.handler
    async def explode(self, _: risa.ComponentContext) -> None:
        msg = "boom"
        raise RuntimeError(msg)


@risa.register(name="ctx-hooked", version=1)
class Hooked(risa.View):
    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.current, label="c"))

    @risa.handler
    async def current(self, _: risa.ComponentContext) -> None: ...

    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay("this message is outdated"))


@pytest.fixture
def store() -> RecordingStore:
    return RecordingStore()


@pytest.fixture
def client(store: RecordingStore) -> StubClient:
    built = StubClient(unittest.mock.Mock(spec=hikari.api.RESTClient), store=store)
    built.add_view(Counter)
    built.add_view(Slow)
    built.add_view(Quiet)
    built.add_view(Off)
    built.add_view(Boom)
    built.add_view(Hooked)
    return built


def bare_context(client: StubClient) -> tuple[risa.ComponentContext, hikari.ComponentInteraction]:
    inter = interaction("unused")
    return risa.ComponentContext(client, inter), inter


# --- the response state machine, driven directly ---


async def test_defer_sends_the_silent_ack_by_default(client: StubClient) -> None:
    ctx, inter = bare_context(client)
    await ctx.defer()

    mock_of(inter.create_initial_response).assert_awaited_once_with(
        hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
        flags=hikari.UNDEFINED,
    )


async def test_defer_thinking_shows_the_spinner(client: StubClient) -> None:
    ctx, inter = bare_context(client)
    await ctx.defer(thinking=True, ephemeral=True)

    mock_of(inter.create_initial_response).assert_awaited_once_with(
        hikari.ResponseType.DEFERRED_MESSAGE_CREATE,
        flags=hikari.MessageFlag.EPHEMERAL,
    )


async def test_defer_ephemeral_requires_thinking(client: StubClient) -> None:
    ctx, _ = bare_context(client)
    with pytest.raises(ValueError, match="thinking"):
        await ctx.defer(ephemeral=True)


async def test_defer_can_only_be_the_initial_response(client: StubClient) -> None:
    ctx, _ = bare_context(client)
    await ctx.respond("hello")
    with pytest.raises(risa.AlreadyRespondedError):
        await ctx.defer()


async def test_respond_is_initial_first_and_followup_after(client: StubClient) -> None:
    ctx, inter = bare_context(client)
    await ctx.respond("one")
    await ctx.respond("two")

    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[:2] == (hikari.ResponseType.MESSAGE_CREATE, "one")

    followup = mock_of(inter.execute)
    followup.assert_awaited_once()
    assert followup.await_args is not None
    assert followup.await_args.args[0] == "two"


async def test_respond_replaces_a_thinking_spinner(client: StubClient) -> None:
    ctx, inter = bare_context(client)
    await ctx.defer(thinking=True)
    await ctx.respond("answer")
    await ctx.respond("extra")

    edit = mock_of(inter.edit_initial_response)
    edit.assert_awaited_once()
    assert edit.await_args is not None
    assert edit.await_args.args[0] == "answer"
    mock_of(inter.execute).assert_awaited_once()


async def test_respond_ephemeral_sets_the_flag(client: StubClient) -> None:
    ctx, inter = bare_context(client)
    await ctx.respond("psst", ephemeral=True)

    initial = mock_of(inter.create_initial_response)
    assert initial.await_args is not None
    assert initial.await_args.kwargs["flags"] == hikari.MessageFlag.EPHEMERAL


async def test_the_initial_response_handle_targets_the_initial_response(client: StubClient) -> None:
    ctx, inter = bare_context(client)
    response = await ctx.respond("hello")

    await response.edit("edited")
    await response.fetch()
    await response.delete()

    mock_of(inter.edit_initial_response).assert_awaited_once()
    mock_of(inter.fetch_initial_response).assert_awaited_once()
    mock_of(inter.delete_initial_response).assert_awaited_once()


async def test_a_followup_handle_targets_its_own_message(client: StubClient) -> None:
    ctx, inter = bare_context(client)
    await ctx.respond("one")
    followup = await ctx.respond("two")

    message = mock_of(inter.execute).return_value
    assert await followup.fetch() is message

    await followup.edit("edited")
    await followup.delete()
    mock_of(message.edit).assert_awaited_once()
    mock_of(message.delete).assert_awaited_once()
    mock_of(inter.edit_initial_response).assert_not_awaited()


async def test_rerender_needs_a_dispatched_view(client: StubClient) -> None:
    ctx, _ = bare_context(client)
    with pytest.raises(RuntimeError, match="view instance"):
        await ctx.rerender()


async def test_edit_needs_dispatch_metadata(client: StubClient) -> None:
    ctx, _ = bare_context(client)
    with pytest.raises(RuntimeError, match="metadata"):
        await ctx.edit(ui.TextDisplay("x"))


# --- dispatch integration ---


async def test_rerender_edits_the_origin_as_the_initial_response(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Counter())
    inter = interaction(custom_id)
    await client.dispatch(inter)

    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.MESSAGE_UPDATE
    assert "components" in initial.await_args.kwargs


async def test_rerendered_components_keep_their_custom_ids(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Counter())
    inter = interaction(custom_id)
    await client.dispatch(inter)

    initial = mock_of(inter.create_initial_response)
    assert initial.await_args is not None
    assert builder_custom_ids(list(initial.await_args.kwargs["components"])) == [custom_id]


async def test_rerender_persists_through_dispatch_not_itself(client: StubClient, store: RecordingStore) -> None:
    custom_id = await first_custom_id(client, Counter())
    writes_after_build = len(store.writes)
    await client.dispatch(interaction(custom_id))

    # Exactly one write-back for the whole click, made by dispatch after the
    # handler -- not by rerender inside it.
    assert len(store.writes) == writes_after_build + 1


async def test_edit_shows_a_terminal_screen(client: StubClient) -> None:
    # The rendered tree only carries the bump button; craft the finish
    # handler's id by hand, reusing the state key bump's id carries.
    bump_id = await first_custom_id(client, Counter())
    decoded = codec.CustomID.parse(bump_id)
    assert decoded is not None
    finish_id = codec.CustomID(
        raw_cookie=decoded.raw_cookie,
        handler=codec.make_handler_token("finish", 1),
        fragment=decoded.fragment,
    ).encode(view_name="ctx-counter")

    inter = interaction(finish_id)
    await client.dispatch(inter)

    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.MESSAGE_UPDATE
    # The terminal tree holds no interactive components at all.
    assert builder_custom_ids(list(initial.await_args.kwargs["components"])) == []


async def test_the_watchdog_stays_quiet_for_a_fast_handler(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Counter())
    inter = interaction(custom_id)
    await client.dispatch(inter)

    # Only the rerender's MESSAGE_UPDATE; no deferred response was ever sent.
    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.MESSAGE_UPDATE


async def test_the_watchdog_defers_a_slow_handler(
    client: StubClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("risa.internal.constants.AUTODEFER_DELAY", 0.05)
    redraw_id, _ = await all_custom_ids(client, Slow())
    inter = interaction(redraw_id)
    await client.dispatch(inter)

    # The watchdog acked silently at the delay, then the handler's rerender
    # travelled through the deferred initial response.
    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.DEFERRED_MESSAGE_UPDATE
    edit = mock_of(inter.edit_initial_response)
    edit.assert_awaited_once()
    assert edit.await_args is not None
    assert "components" in edit.await_args.kwargs


async def test_a_thinking_override_defers_with_the_spinner(
    client: StubClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("risa.internal.constants.AUTODEFER_DELAY", 0.05)
    _, reply_id = await all_custom_ids(client, Slow())
    inter = interaction(reply_id)
    await client.dispatch(inter)

    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.DEFERRED_MESSAGE_CREATE

    # respond() replaced the spinner instead of posting a message beside it.
    edit = mock_of(inter.edit_initial_response)
    edit.assert_awaited_once()
    assert edit.await_args is not None
    assert edit.await_args.args[0] == "done"
    mock_of(inter.execute).assert_not_awaited()


async def test_a_clean_finish_without_response_is_acked_immediately(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Quiet())
    inter = interaction(custom_id)
    async with asyncio.timeout(0.5):
        await client.dispatch(inter)

    mock_of(inter.create_initial_response).assert_awaited_once_with(
        hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
        flags=hikari.UNDEFINED,
    )


async def test_autodefer_off_leaves_the_interaction_unanswered(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Off())
    inter = interaction(custom_id)
    await client.dispatch(inter)

    mock_of(inter.create_initial_response).assert_not_awaited()


async def test_a_crashing_handler_is_not_acked_into_silence(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Boom())
    inter = interaction(custom_id)
    with pytest.raises(RuntimeError, match="boom"):
        await client.dispatch(inter)

    mock_of(inter.create_initial_response).assert_not_awaited()


async def test_hooks_can_edit_the_origin_message(client: StubClient) -> None:
    outdated = codec.CustomID(
        raw_cookie=codec.make_cookie("ctx-hooked", 1),
        handler=codec.make_handler_token("gone", 1),
    ).encode(view_name="ctx-hooked")
    inter = interaction(outdated)
    await client.dispatch(inter)

    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.MESSAGE_UPDATE


# --- the REST transport ---


class RestApp:
    """The two attributes of a REST application the client construction needs."""

    def __init__(self) -> None:
        self.rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
        self.interaction_server = unittest.mock.Mock(spec=hikari.api.InteractionServer)


class StubRestClient(risa.RestEnabledClient):
    """Drives the REST generator listener the way hikari's server would."""

    __slots__ = ()

    async def drive(self, interaction_: hikari.ComponentInteraction) -> None:
        """Run the listener to completion: the yield, then the continuation."""
        async for _ in self._on_interaction(interaction_):
            pass


@pytest.fixture
def rest_client(store: RecordingStore) -> StubRestClient:
    built = StubRestClient(typing.cast("hikari.impl.RESTBot", RestApp()), store=store)
    built.add_view(Counter)
    built.add_view(Off)
    return built


async def test_rest_listener_answers_after_the_first_response(rest_client: StubRestClient) -> None:
    custom_id = await first_custom_id(rest_client, Counter())
    inter = interaction(custom_id)
    async with asyncio.timeout(1.0):
        await rest_client.drive(inter)

    # The response went out through the same REST call the gateway uses.
    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.MESSAGE_UPDATE


async def test_rest_listener_answers_foreign_components_promptly(rest_client: StubRestClient) -> None:
    inter = interaction("other:thing")
    async with asyncio.timeout(1.0):
        await rest_client.drive(inter)

    mock_of(inter.create_initial_response).assert_not_awaited()


async def test_rest_listener_logs_when_no_response_arrives(
    rest_client: StubRestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("risa.internal.constants.REST_ACK_TIMEOUT", 0.05)
    custom_id = await first_custom_id(rest_client, Off())
    inter = interaction(custom_id)

    before = Off.finished
    async with asyncio.timeout(1.0):
        await rest_client.drive(inter)

    assert "no response was issued" in caplog.text
    # The handler was allowed to finish rather than being cancelled.
    assert Off.finished == before + 1
