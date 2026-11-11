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
"""What the pages claim about an example, held by running it."""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import logging
import pathlib
import sys
import typing
import unittest.mock

import fakeredis
import hikari
import linkd
import msgspec
import pytest

import risa
from risa import codecs
from risa import ui
from risa.internal import registry
from risa.store.redis import RedisStore

if typing.TYPE_CHECKING:
    import types

_EXAMPLES: typing.Final = pathlib.Path(__file__).parents[2] / "docs" / "examples"
_FIVE_VOTERS: typing.Final = [1298471056230400001 + index * 7919 for index in range(5)]


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _EXAMPLES / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client() -> risa.Client:
    app = unittest.mock.Mock(rest=unittest.mock.AsyncMock())
    return risa.client_from_app(typing.cast("risa.GatewayClientAppT", app))


async def test_the_store_of_chapter_12_passes_verify_store() -> None:
    await _load("ch12_dict_store").test_dict_store()


async def test_the_wallet_of_chapter_06_opens_for_each_click_from_one_database() -> None:
    module = _load("ch06_wallet")
    client = _client()
    module.add_database(client)
    module.add_wallet(client)

    wallets: list[object] = []
    databases: list[object] = []
    for _ in range(2):
        async with (
            client.di.enter_context(risa.Contexts.DEFAULT),
            client.di.enter_context(risa.Contexts.COMPONENT) as container,
        ):
            container.add_value(risa.ComponentContext, unittest.mock.Mock(spec=risa.ComponentContext))
            wallets.append(await container.get(module.Wallet))
            databases.append(await container.get(module.Database))
    await client.di.close()

    assert wallets[0] is not wallets[1]
    assert databases[0] is databases[1]


async def test_the_function_of_chapter_06_needs_the_context_it_opens() -> None:
    module = _load("ch06_feedback")
    client = _client()
    module.add_databases(client)

    with pytest.raises(linkd.DependencyNotSatisfiableException):
        await module.post_votes()

    async with client.di.enter_context(risa.Contexts.DEFAULT):
        await module.post_votes()
    await client.di.close()

    typing.cast("unittest.mock.AsyncMock", client.rest).create_message.assert_awaited_once()


@pytest.mark.parametrize(
    ("wire_codec", "voters"),
    [(codecs.BASE85, 12), (codecs.BASE32768, 33), (codecs.URO14, 30)],
)
async def test_the_poll_of_chapter_08_fits_what_its_page_says(wire_codec: codecs.Codec, voters: int) -> None:
    @risa.register(name=f"examples-poll-{wire_codec.name}", state=risa.InMessage(codec=wire_codec))
    class Poll(risa.View):
        question: str
        yes: int = 0
        no: int = 0
        voters: list[int] = msgspec.field(default_factory=list[int])

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.vote, label="Yes"), ui.Button(self.vote, label="No"))

        @risa.handler
        async def vote(self, ctx: risa.ComponentContext) -> None: ...

    client = _client()
    ids = [1298471056230400001 + index * 7919 for index in range(voters + 1)]

    await client.build(Poll(question="Pizza on Friday?", yes=voters, voters=ids[:voters]))
    with pytest.raises(risa.StateOverflowError):
        await client.build(Poll(question="Pizza on Friday?", yes=voters, voters=ids))


@pytest.fixture
def own_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_GLOBAL_REGISTRY", registry.Registry())


def _client_with(view: type[risa.View]) -> risa.Client:
    client = _client()
    client.add_view(view)
    return client


def _poll_client(store: risa.Store, *views: type[risa.View]) -> risa.Client:
    app = unittest.mock.Mock(rest=unittest.mock.AsyncMock())
    client = risa.client_from_app(typing.cast("risa.GatewayClientAppT", app), store=store, key_prefix="examples")
    for view in views:
        client.add_view(view)
    return client


def _ids(components: typing.Sequence[hikari.api.ComponentBuilder]) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            entries = typing.cast("dict[str, object]", node)
            if isinstance(raw := entries.get("custom_id"), str):
                found.append(raw)
            for value in entries.values():
                walk(value)
        elif isinstance(node, list):
            for value in typing.cast("list[object]", node):
                walk(value)

    for builder in components:
        walk(builder.build()[0])
    return found


def _click(ids: typing.Sequence[str], index: int, *, user: int = 777) -> unittest.mock.Mock:
    buttons = [
        hikari.ButtonComponent(
            type=hikari.ComponentType.BUTTON,
            id=0,
            style=hikari.ButtonStyle.PRIMARY,
            label=None,
            emoji=None,
            custom_id=raw,
            url=None,
            is_disabled=False,
        )
        for raw in ids
    ]
    interaction = unittest.mock.Mock(spec=hikari.ComponentInteraction)
    interaction.custom_id = ids[index]
    interaction.user = unittest.mock.Mock(id=hikari.Snowflake(user))
    interaction.member = None
    interaction.message = unittest.mock.Mock(
        spec=hikari.Message,
        id=hikari.Snowflake(1),
        flags=hikari.MessageFlag.NONE,
        components=[hikari.ActionRowComponent(type=hikari.ComponentType.ACTION_ROW, id=0, components=buttons)],
    )
    return interaction


async def _process(client: risa.Client, interaction: unittest.mock.Mock) -> None:
    await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


def _rest(client: risa.Client) -> unittest.mock.Mock:
    return typing.cast("unittest.mock.Mock", client.rest)


def _answer(client: risa.Client) -> list[hikari.api.ComponentBuilder]:
    return _rest(client).create_interaction_response.await_args.kwargs["components"]


async def _poll_at_version_2(store: risa.MemoryStore) -> list[str]:
    module = _load("ch10_poll_free")
    poll = module.Poll(question="Pizza on Friday?", yes=3, no=2, voters=[1, 2, 3, 4, 5])
    ids = _ids((await _poll_client(store, module.Poll).build(poll)).components)
    registry.global_registry().clear()
    return ids


@pytest.mark.usefixtures("own_registry")
async def test_a_raised_version_of_chapter_10_lets_old_buttons_fail_without_its_hook(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = risa.MemoryStore()
    ids = await _poll_at_version_2(store)
    client = _poll_client(store, _load("ch10_poll_bumped").Poll)

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await _process(client, _click(ids, 0))

    _rest(client).create_interaction_response.assert_not_awaited()
    assert "this client has no view for it" in caplog.text


@pytest.mark.usefixtures("own_registry")
async def test_the_retired_class_of_chapter_10_answers_a_poll_at_version_1() -> None:
    first = _load("ch08_poll")
    ids = _ids((await _client().build(first.Poll(question="Pizza on Friday?"))).components)
    registry.global_registry().clear()
    module = _load("ch10_poll_bumped")
    client = _poll_client(risa.MemoryStore(), module.Poll, module.OldPoll, module.RetiredPoll)

    await _process(client, _click(ids, 0))

    answered = _rest(client).create_interaction_response.await_args
    assert answered.args[3] == "This button no longer works."
    assert answered.kwargs["flags"] == hikari.MessageFlag.EPHEMERAL


@pytest.mark.usefixtures("own_registry")
async def test_the_closed_poll_of_chapter_10_reads_the_old_state_and_logs_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = risa.MemoryStore()
    ids = await _poll_at_version_2(store)
    module = _load("ch10_poll_bumped")
    client = _poll_client(store, module.Poll, module.OldPoll, module.RetiredPoll)
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await _process(client, _click(ids, 0))

    assert "It ended with 3 yes and 2 no." in str(_answer(client)[0].build()[0])
    assert not caplog.records


@pytest.mark.usefixtures("own_registry")
async def test_the_kept_class_of_chapter_10_keeps_an_old_poll_at_its_version() -> None:
    store = risa.MemoryStore()
    ids = await _poll_at_version_2(store)
    module = _load("ch10_poll_kept")
    client = _poll_client(store, module.Poll, module.OldPoll)

    await _process(client, _click(ids, 0))

    assert "Yes: 4" in str(_answer(client)[0].build()[0])
    assert [raw[:8] for raw in _ids(_answer(client))] == [raw[:8] for raw in ids]


@pytest.mark.usefixtures("own_registry")
async def test_the_upgrade_of_chapter_10_moves_an_old_poll_and_counts_the_click(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = risa.MemoryStore()
    ids = await _poll_at_version_2(store)
    module = _load("ch10_poll_upgraded")
    client = _poll_client(store, module.Poll, module.OldPoll)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="risa"):
        await _process(client, _click(ids, 0))

    assert not caplog.records
    components = _answer(client)
    assert "Yes: 4" in str(components[0].build()[0])
    assert "No: 2" in str(components[0].build()[0])

    upgraded = _ids(components)
    assert {raw[2:8] for raw in upgraded} == {registry.require_meta(module.Poll).cookie}

    await _process(client, _click(upgraded, 1, user=888))
    assert "No: 3" in str(_answer(client)[0].build()[0])

    await _process(client, _click(upgraded, 1, user=777))
    assert _rest(client).create_interaction_response.await_args.args[3] == "You already voted."


@pytest.mark.usefixtures("own_registry")
async def test_the_upgrade_of_chapter_10_counts_nobody_twice() -> None:
    store = risa.MemoryStore()
    ids = await _poll_at_version_2(store)
    module = _load("ch10_poll_upgraded")
    client = _poll_client(store, module.Poll, module.OldPoll)

    await _process(client, _click(ids, 0, user=1))

    assert "Yes: 3" in str(_answer(client)[0].build()[0])


@pytest.mark.usefixtures("own_registry")
async def test_the_base_class_of_chapter_10_answers_every_older_version() -> None:
    first = _load("ch08_poll")
    version_1 = _ids((await _client().build(first.Poll(question="q"))).components)
    registry.global_registry().clear()
    store = risa.MemoryStore()
    version_2 = await _poll_at_version_2(store)
    module = _load("ch10_poll_retired")
    client = _poll_client(store, module.Poll, module.PollV1, module.PollV2)

    for ids in (version_1, version_2):
        _rest(client).create_interaction_response.reset_mock()
        await _process(client, _click(ids, 0))
        assert _rest(client).create_interaction_response.await_args.args[3] == "This button no longer works."


@pytest.mark.usefixtures("own_registry")
async def test_the_old_method_of_chapter_10_moves_a_button_to_the_new_handler_version() -> None:
    store = risa.MemoryStore()
    sent = _load("ch10_poll_bumped")
    poll = sent.Poll(question="Pizza on Friday?", votes={"Yes": 3, "No": 2})
    ids = _ids((await _poll_client(store, sent.Poll).build(poll)).components)
    registry.global_registry().clear()
    module = _load("ch10_poll_handlers")
    client = _poll_client(store, module.Poll)

    await _process(client, _click(ids, 1, user=1))
    assert "No: 3" in str(_answer(client)[0].build()[0])

    moved = _ids(_answer(client))
    assert [raw[8:10] for raw in moved] == [module.Poll.vote.token] * 2

    await _process(client, _click(moved, 0, user=2))
    assert "Yes: 4" in str(_answer(client)[0].build()[0])


@pytest.mark.usefixtures("own_registry")
async def test_an_option_of_chapter_10_fits_its_button_only_up_to_what_its_page_says() -> None:
    sent = _load("ch10_poll_bumped")
    client = _poll_client(risa.MemoryStore(), sent.Poll)

    await client.build(sent.Poll(question="q", votes={"x" * 54: 0}))
    with pytest.raises(risa.CustomIdOverflowError):
        await client.build(sent.Poll(question="q", votes={"x" * 55: 0}))

    registry.global_registry().clear()
    module = _load("ch10_poll_handlers")
    await _poll_client(risa.MemoryStore(), module.Poll).build(module.Poll(question="q", votes={"x" * 55: 0}))


@pytest.mark.usefixtures("own_registry")
async def test_a_changed_default_of_chapter_10_reaches_only_state_saved_without_the_field() -> None:
    seen: list[str] = []

    @risa.register(name="examples-default")
    class Before(risa.View):
        total: int = 0

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.go))

        @risa.handler
        async def go(self, ctx: risa.ComponentContext) -> None: ...

    without_field = _ids((await _client_with(Before).build(Before())).components)
    registry.global_registry().clear()

    @risa.register(name="examples-default")
    class First(risa.View):
        total: int = 0
        note: str = "first"

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.go))

        @risa.handler
        async def go(self, ctx: risa.ComponentContext) -> None: ...

    with_field = _ids((await _client_with(First).build(First())).components)
    registry.global_registry().clear()

    @risa.register(name="examples-default")
    class Second(risa.View):
        total: int = 0
        note: str = "second"

        @risa.handler
        async def go(self, _: risa.ComponentContext) -> None:
            seen.append(self.note)

    client = _client_with(Second)

    await _process(client, _click(without_field, 0))
    await _process(client, _click(with_field, 0))

    assert seen == ["second", "first"]


@pytest.mark.usefixtures("own_registry")
async def test_a_codec_of_chapter_08_that_left_the_registry_makes_its_ids_foreign(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    built_in = {codecs.BASE85.tag: codecs.BASE85}
    monkeypatch.setattr(codecs, "_REGISTRY", dict(built_in))
    own = codecs.Codec(
        tag="z",
        name="own",
        encode=codecs.BASE85.encode,
        decode=codecs.BASE85.decode,
        max_bytes=codecs.BASE85.max_bytes,
    )

    @risa.register(name="examples-own", state=risa.InMessage(codec=own))
    class Sent(risa.View):
        total: int = 0

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.go))

        @risa.handler
        async def go(self, ctx: risa.ComponentContext) -> None: ...

    ids = _ids((await _client_with(Sent).build(Sent())).components)
    registry.global_registry().clear()
    monkeypatch.setattr(codecs, "_REGISTRY", dict(built_in))

    @risa.register(name="examples-own")
    class Current(risa.View):
        total: int = 0

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.go))

        @risa.handler
        async def go(self, ctx: risa.ComponentContext) -> None:
            self.total += 1
            await ctx.rerender()

    client = _client_with(Current)
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await _process(client, _click(ids, 0))

    _rest(client).create_interaction_response.assert_not_awaited()
    assert not caplog.records

    codecs.register(own)
    await _process(client, _click(ids, 0))

    assert _ids(_answer(client))[0][1] == codecs.BASE85.tag


@pytest.mark.usefixtures("own_registry")
async def test_a_handler_runs_another_through_its_plain_function_and_cannot_call_it() -> None:
    @risa.register(name="examples-delegate")
    class Delegating(risa.View):
        total: int = 0

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.add_one, label=str(self.total)))

        @risa.handler
        async def add(self, ctx: risa.ComponentContext, amount: int) -> None:
            self.total += amount
            await ctx.rerender()

        @risa.handler
        async def add_one(self, ctx: risa.ComponentContext) -> None:
            await Delegating.add.func(self, ctx, 1)

    client = _client_with(Delegating)

    await _process(client, _click(_ids((await client.build(Delegating())).components), 0))

    assert "'label': '1'" in str(_answer(client)[0].build()[0])
    with pytest.raises(risa.HandlerNotCallableError):
        typing.cast("typing.Callable[..., object]", Delegating().add)(None, 1)


@pytest.mark.usefixtures("own_registry")
async def test_a_render_with_fewer_buttons_can_leave_an_old_message_no_room(caplog: pytest.LogCaptureFixture) -> None:
    voters = [1298471056230400001 + index * 7919 for index in range(9)]

    @risa.register(name="examples-room")
    class TwoButtons(risa.View):
        voters: list[int] = msgspec.field(default_factory=list[int])

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.vote), ui.Button(self.vote))

        @risa.handler
        async def vote(self, ctx: risa.ComponentContext) -> None:
            self.voters.append(ctx.user.id)
            await ctx.rerender()

    ids = _ids((await _client_with(TwoButtons).build(TwoButtons(voters=voters))).components)
    registry.global_registry().clear()

    @risa.register(name="examples-room")
    class OneButton(risa.View):
        voters: list[int] = msgspec.field(default_factory=list[int])

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.vote))

        @risa.handler
        async def vote(self, ctx: risa.ComponentContext) -> None:
            self.voters.append(ctx.user.id)
            await ctx.rerender()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await _process(_client_with(OneButton), _click(ids, 0))

    assert "on view examples-room failed in handler 'vote' (version 1)" in caplog.text
    assert "StateOverflowError" in caplog.text


def _shown(*clients: risa.Client) -> set[str]:
    shown: set[str] = set()
    for client in clients:
        for call in _rest(client).create_interaction_response.await_args_list:
            for count in (5, 6, 7):
                if f"Yes: {count}" in str(call.kwargs["components"][0].build()[0]):
                    shown.add(f"Yes: {count}")
    return shown


async def _two_clicks(first: risa.Client, second: risa.Client, ids: typing.Sequence[str]) -> None:
    await asyncio.gather(
        _process(first, _click(ids, 0, user=1)),
        _process(second, _click(ids, 0, user=2)),
    )


@pytest.mark.usefixtures("own_registry")
async def test_two_clicks_of_chapter_11_both_count_in_a_message_on_one_process() -> None:
    module = _load("ch08_poll")
    client = _poll_client(risa.MemoryStore(), module.Poll)
    ids = _ids((await client.build(module.Poll(question="q", yes=5, voters=_FIVE_VOTERS))).components)

    await _two_clicks(client, client, ids)

    assert "Yes: 7" in _shown(client)


@pytest.mark.usefixtures("own_registry")
async def test_two_clicks_of_chapter_11_lose_one_in_a_message_across_processes_and_log_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _load("ch08_poll")
    first = _poll_client(risa.MemoryStore(), module.Poll)
    second = _poll_client(risa.MemoryStore(), module.Poll)
    ids = _ids((await first.build(module.Poll(question="q", yes=5, voters=_FIVE_VOTERS))).components)
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await _two_clicks(first, second, ids)

    assert _shown(first, second) == {"Yes: 6"}
    assert not caplog.records


@pytest.mark.usefixtures("own_registry")
async def test_two_clicks_of_chapter_11_both_count_on_redis_across_processes() -> None:
    module = _load("ch09_poll_redis")
    connection = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer())
    first = _poll_client(RedisStore(connection), module.Poll)
    second = _poll_client(RedisStore(connection), module.Poll)
    ids = _ids((await first.build(module.Poll(question="q", yes=5, voters=_FIVE_VOTERS))).components)

    await _two_clicks(first, second, ids)
    await connection.aclose()

    assert "Yes: 7" in _shown(first, second)


@pytest.mark.usefixtures("own_registry")
async def test_two_clicks_of_chapter_11_both_count_on_memory_store_on_one_process() -> None:
    module = _load("ch09_poll_memory")
    client = _poll_client(risa.MemoryStore(), module.Poll)
    ids = _ids((await client.build(module.Poll(question="q", yes=5, voters=_FIVE_VOTERS))).components)

    await _two_clicks(client, client, ids)

    assert "Yes: 7" in _shown(client)


@pytest.mark.usefixtures("own_registry")
async def test_a_click_of_chapter_11_finds_no_record_on_a_process_with_its_own_memory_store() -> None:
    module = _load("ch09_poll_memory")
    sender = _poll_client(risa.MemoryStore(), module.Poll)
    other = _poll_client(risa.MemoryStore(), module.Poll)
    ids = _ids((await sender.build(module.Poll(question="q", yes=5, voters=_FIVE_VOTERS))).components)

    await _process(other, _click(ids, 0, user=1))

    assert "This poll has expired." in str(_answer(other)[0].build()[0])


class RacingStore(risa.MemoryStore):
    __slots__ = ("armed",)

    def __init__(self) -> None:
        super().__init__()
        self.armed = False

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        if self.armed:
            self.armed = False
            await self.put(key, b"written by a newer process", ttl=ttl)
        return await super().put_if_version(key, value, expected=expected, ttl=ttl)


@risa.register(name="examples-racing", state=risa.InStore(ttl=60))
class RacingPoll(risa.View):
    yes: int = 0
    voters: list[int] = msgspec.field(default_factory=list[int])

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.vote_yes, label=f"Yes: {self.yes}"))

    @risa.handler
    async def vote_yes(self, ctx: risa.ComponentContext) -> None:
        if not await self._first_vote(ctx):
            return
        self.yes += 1
        try:
            await ctx.rerender()
        except risa.StateConflictError:
            if await self._first_vote(ctx):
                self.yes += 1
                await ctx.rerender()

    async def _first_vote(self, ctx: risa.ComponentContext) -> bool:
        if ctx.user.id in self.voters:
            await ctx.respond("You already voted.", ephemeral=True)
            return False
        self.voters.append(ctx.user.id)
        return True


async def test_a_winner_of_chapter_11_that_cannot_be_read_fails_the_click_with_no_false_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RacingStore()
    client = _poll_client(store, RacingPoll)
    ids = _ids((await client.build(RacingPoll(yes=5, voters=_FIVE_VOTERS))).components)
    store.armed = True
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await _process(client, _click(ids, 0, user=777))

    _rest(client).create_interaction_response.assert_not_awaited()
    assert "StateUnreadableError" in caplog.text


@pytest.mark.usefixtures("own_registry")
async def test_the_error_handlers_of_chapter_05_answer_and_log_as_the_page_says(
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _load("ch05_errors")
    client = _client()
    client.add_view(module.Forecast)
    module.add_error_handlers(client)
    ids = _ids((await client.build(module.Forecast())).components)
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await _process(client, _click(ids, 0))

    answered = _rest(client).create_interaction_response.await_args
    assert answered.args[3] == "The weather service is not answering. Try again in a minute."
    assert not caplog.records

    with caplog.at_level(logging.ERROR, logger="risa"):
        await _process(client, _click(ids, 1))

    answered = _rest(client).create_interaction_response.await_args
    assert answered.args[3] == "Something went wrong."
    assert "KeyError" in caplog.text


@pytest.mark.usefixtures("own_registry")
async def test_the_poll_of_chapter_13_closes_only_for_its_author_and_audits_every_click(
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _load("ch13_hooks")
    app = unittest.mock.Mock(rest=unittest.mock.AsyncMock())
    client = module.make_client(typing.cast("hikari.GatewayBot", app))
    ids = _ids((await client.build(module.Poll(question="Pizza on Friday?", author_id=42))).components)

    with caplog.at_level(logging.INFO, logger="bot.audit"):
        await _process(client, _click(ids, 2, user=7))
        refused = _rest(client).create_interaction_response.await_args
        await _process(client, _click(ids, 2, user=42))
        closed = _rest(client).create_interaction_response.await_args

    assert refused.args[3] == "Only the author can close this poll."
    assert closed.args[2] is hikari.ResponseType.MESSAGE_UPDATE
    assert [record.getMessage().rsplit(" ", 1)[1] for record in caplog.records] == ["failed", "ran"]

    ctx = unittest.mock.Mock(spec=risa.ComponentContext)
    await module.test_author_only_lets_the_author_through(ctx)
    with pytest.raises(module.NotTheAuthorError):
        await module.author_only(risa.ExecutionPipeline(), ctx, module.Poll(question="?", author_id=1))


@pytest.mark.usefixtures("own_registry")
async def test_the_reward_of_chapter_14_answers_every_click_it_turns_away() -> None:
    module = _load("ch14_prefab")
    app = unittest.mock.Mock(rest=unittest.mock.AsyncMock())
    app.rest.fetch_application.return_value.owner.id = hikari.Snowflake(42)
    app.rest.fetch_application.return_value.team = None
    client = module.make_client(typing.cast("hikari.GatewayBot", app))
    ids = _ids((await client.build(module.Reward())).components)
    now = dt.datetime.now(dt.UTC)
    answers: list[object] = []

    for button, user, sent in [
        (0, 7, now),
        (0, 7, now),
        (1, 7, now),
        (1, 42, now),
        (0, 8, now - dt.timedelta(hours=2)),
    ]:
        interaction = _click(ids, button, user=user)
        interaction.message.timestamp = sent
        interaction.message.edited_timestamp = None
        await _process(client, interaction)
        call = _rest(client).create_interaction_response.await_args
        answers.append(call.args[3] if len(call.args) > 3 else call.args[2])
    answers[1] = str(answers[1]).split("<")[0]

    assert answers == [
        hikari.ResponseType.MESSAGE_UPDATE,
        "Come back ",
        "You cannot use this.",
        hikari.ResponseType.MESSAGE_UPDATE,
        "This message has expired.",
    ]


@pytest.mark.usefixtures("own_registry")
async def test_the_poll_of_chapter_08_answers_a_full_poll_and_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    module = _load("ch08_poll")
    client = _poll_client(risa.MemoryStore(), module.Poll)
    voters = [1298471056230400001 + index * 7919 for index in range(12)]
    ids = _ids((await client.build(module.Poll(question="Pizza on Friday?", yes=12, voters=voters))).components)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="risa"):
        await _process(client, _click(ids, 0, user=1298471056230499999))

    assert _rest(client).create_interaction_response.await_args.args[3] == "This poll is full."
    assert not caplog.records
