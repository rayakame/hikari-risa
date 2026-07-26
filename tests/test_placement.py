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
import msgspec
import pytest

import risa
from risa import ui
from risa.internal import anchor
from risa.internal import codec
from risa.internal import constants
from risa.state import schema as schema_
from risa.state import serde
from tests.helpers import RecordingStore
from tests.helpers import StubClient
from tests.helpers import all_custom_ids
from tests.helpers import answered_with
from tests.helpers import clicked
from tests.helpers import first_custom_id
from tests.helpers import interaction
from tests.helpers import meta_of
from tests.helpers import mock_of
from tests.helpers import next_click
from tests.helpers import state_key_of


@risa.register(name="place-poll", version=1)
class Poll(risa.View):
    question: str
    votes: int = 0
    seen: typing.ClassVar[list[int]] = []

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.vote, label=str(self.votes)), ui.Button(self.close, label="close"))

    @risa.handler
    async def vote(self, ctx: risa.ComponentContext) -> None:
        type(self).seen.append(self.votes)
        self.votes += 1
        await ctx.rerender()

    @risa.handler
    async def quietly(self, _: risa.ComponentContext) -> None:
        self.votes += 10

    @risa.handler
    async def close(self, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay("Poll closed."))


@risa.register(name="place-wide", version=1)
class Wide(risa.View):
    blob: str = ""

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.noop, label="x"))

    @risa.handler
    async def noop(self, _: risa.ComponentContext) -> None: ...


@risa.register(name="place-spread", version=1)
class Spread(risa.View):
    blob: str = ""

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(*(ui.Button(self.noop.bind(index), label=str(index)) for index in range(5)))

    @risa.handler
    async def noop(self, _: risa.ComponentContext, which: int) -> None: ...


@risa.register(name="place-ledger", version=1, state=risa.InStore(ttl=None))
class Ledger(risa.View):
    label: str
    hits: int = 0
    interfere: typing.ClassVar[bool] = False

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.race, label=str(self.hits)))

    @risa.handler
    async def race(self, ctx: risa.ComponentContext) -> None:
        self.hits += 1
        if Ledger.interfere:
            await _overwrite(ctx, Ledger(label="winner", hits=99))
        await ctx.rerender()


@risa.register(name="place-vanish", version=1, state=risa.InStore(ttl=None))
class Vanish(risa.View):
    label: str
    hits: int = 0
    expired: typing.ClassVar[int] = 0

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.go, label=str(self.hits)))

    @risa.handler
    async def go(self, ctx: risa.ComponentContext) -> None:
        self.hits += 1
        decoded = codec.CustomID.parse(ctx.custom_id)
        assert decoded is not None
        await ctx.client.stores["default"].delete(state_key_of(decoded))
        await ctx.rerender()

    @classmethod
    @typing.override
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        cls.expired += 1
        await ctx.edit(ui.TextDisplay("This poll has expired."))


@risa.register(name="place-nothing", version=1)
class Nothing(risa.View):
    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.press, label="x"))

    @risa.handler
    async def press(self, _: risa.ComponentContext) -> None: ...


POLL: typing.Final = meta_of(Poll)
LEDGER: typing.Final = meta_of(Ledger)
NOTHING: typing.Final = meta_of(Nothing)


async def _overwrite(ctx: risa.ComponentContext, winner: Ledger) -> None:
    """Commit a record over the one this dispatch read, as another process would."""
    decoded = codec.CustomID.parse(ctx.custom_id)
    assert decoded is not None
    await ctx.client.stores["default"].put(state_key_of(decoded), serde.dumps(winner, meta=LEDGER))


@pytest.fixture
def store() -> RecordingStore:
    return RecordingStore()


@pytest.fixture
def client(store: RecordingStore) -> StubClient:
    built = StubClient(unittest.mock.Mock(spec=hikari.api.RESTClient), stores={"default": store})
    for cls in (Poll, Wide, Spread, Ledger, Vanish, Nothing):
        built.add_view(cls)
    return built


def fragments_of(custom_ids: list[str]) -> list[tuple[int, str]]:
    """Return the anchor slice each rendered component carries."""
    decoded = [codec.CustomID.parse(custom_id) for custom_id in custom_ids]
    return [(one.fragment_index, one.fragment) for one in decoded if one is not None]


# --- distributing an anchor over what was rendered ---


async def test_state_too_big_for_one_component_is_split_across_several(client: StubClient) -> None:
    rendered = await all_custom_ids(client, Spread(blob="x" * 200))
    carried = fragments_of(rendered)

    assert len([fragment for _, fragment in carried if fragment]) > 1
    rejoined = anchor.join_fragments(carried)
    assert rejoined is not None
    assert isinstance(anchor.parse(rejoined), anchor.MessageAnchor)


async def test_components_the_state_does_not_reach_share_one_slot(client: StubClient) -> None:
    # Counting on past the end would spend indices the wire cannot express on
    # components carrying nothing.
    carried = fragments_of(await all_custom_ids(client, Spread(blob="short")))
    used = [index for index, fragment in carried if fragment]
    empty = {index for index, fragment in carried if not fragment}

    assert used == list(range(len(used)))
    assert empty == {len(used)}
    assert anchor.join_fragments(carried) is not None


async def test_a_components_bound_arguments_come_out_of_its_own_capacity(client: StubClient) -> None:
    # Every Spread button binds an index, so the anchor is cut to whatever each
    # component has left over after its own arguments.
    rendered = await all_custom_ids(client, Spread(blob="x" * 200))
    for custom_id in rendered:
        decoded = codec.CustomID.parse(custom_id)
        assert decoded is not None
        assert decoded.args
        assert len(custom_id) == codec.FRAGMENT_START + len(decoded.fragment) + len(decoded.args)
        assert len(custom_id) <= constants.MAX_CUSTOM_ID_LENGTH


async def test_a_view_whose_state_outgrows_its_tree_is_refused_at_build(client: StubClient) -> None:
    with pytest.raises(risa.StateOverflowError) as caught:
        await client.build(Wide(blob="x" * 400))

    assert caught.value.view_name == "place-wide"
    assert caught.value.required > caught.value.capacity


async def test_a_terminal_screen_may_drop_state_nothing_can_reach(client: StubClient) -> None:
    # No component means no click, so no copy of the state is being destroyed --
    # this is the documented way to end a view, not an overflow.
    inter = await clicked(client, Poll(question="Ship it?"), on=1)
    await client.dispatch(inter)

    assert answered_with(inter) == []
    mock_of(inter.create_initial_response).assert_awaited_once()


# --- a view that carries its own state ---


async def test_a_message_resident_view_round_trips_through_a_click(client: StubClient) -> None:
    Poll.seen.clear()
    inter = await clicked(client, Poll(question="Ship it?", votes=4))
    await client.dispatch(inter)

    assert Poll.seen == [4]
    assert answered_with(inter)


async def test_each_click_sees_what_the_last_one_committed(client: StubClient) -> None:
    Poll.seen.clear()
    inter = await clicked(client, Poll(question="Ship it?"))
    for _ in range(3):
        await client.dispatch(inter)
        inter = next_click(inter)

    assert Poll.seen == [0, 1, 2]


async def test_a_message_resident_view_never_touches_a_store(
    client: StubClient,
    store: RecordingStore,
) -> None:
    await client.dispatch(await clicked(client, Poll(question="Ship it?")))
    assert store.writes == []
    assert store.touches == []


async def test_a_handler_that_changes_state_without_rendering_is_redrawn_anyway(client: StubClient) -> None:
    # The message edit is the only way to write this placement, so risa issues
    # one rather than silently losing what the handler meant.
    rendered = await all_custom_ids(client, Poll(question="Ship it?"))
    quiet = codec.CustomID.parse(rendered[0])
    assert quiet is not None
    inter = interaction(
        codec.CustomID(
            raw_cookie=quiet.raw_cookie,
            handler=codec.make_handler_token("quietly", 1),
            fragment_index=quiet.fragment_index,
            fragment=quiet.fragment,
        ).encode(view_name="place-poll"),
        components=rendered,
    )
    await client.dispatch(inter)

    follow_on = next_click(inter)
    Poll.seen.clear()
    await client.dispatch(follow_on)
    assert Poll.seen == [10]


async def test_a_message_risa_did_not_write_is_outdated_rather_than_empty(
    client: StubClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    custom_id = await first_custom_id(client, Poll(question="Ship it?"))
    Poll.seen.clear()
    await client.dispatch(interaction(custom_id, components=[]))

    assert Poll.seen == []
    assert "could not rejoin its state" in caplog.text


# --- a view that keeps nothing ---


async def test_a_component_predating_the_removal_of_every_field_is_outdated(
    client: StubClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stale = codec.CustomID(
        raw_cookie=NOTHING.cookie,
        handler=codec.make_handler_token("press", 1),
        fragment="m00abc000leftover",
    ).encode(view_name="place-nothing")

    await client.dispatch(interaction(stale))
    assert "keeps no state, but the component carries some" in caplog.text


# --- losing the race to commit ---


async def test_a_lost_commit_converges_instead_of_raising(
    client: StubClient,
    store: RecordingStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    inter = await clicked(client, Ledger(label="votes"))
    Ledger.interfere = True
    try:
        await client.dispatch(inter)
    finally:
        Ledger.interfere = False

    assert "was changed by another dispatch" in caplog.text

    # The message ends up showing the state that won, and the losing handler's
    # own change is not committed on top of it.
    decoded = codec.CustomID.parse(inter.custom_id)
    assert decoded is not None
    raw = await store.get(state_key_of(decoded))
    assert raw is not None
    assert serde.loads(raw, meta=LEDGER) == Ledger(label="winner", hits=99)


async def test_a_lost_commit_still_answers_the_interaction(client: StubClient) -> None:
    inter = await clicked(client, Ledger(label="votes"))
    Ledger.interfere = True
    try:
        await client.dispatch(inter)
    finally:
        Ledger.interfere = False

    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.MESSAGE_UPDATE

    # And it shows the winner's count, not the one this handler computed.
    payload, _ = next(iter(initial.await_args.kwargs["components"])).build()
    assert payload["components"][0]["label"] == "99"


async def test_rebuilding_a_message_is_not_undone_by_what_the_last_click_wrote(client: StubClient) -> None:
    # A panel rebuilt out of band is the newest truth on that message; reverting
    # it to whatever this process last rendered would silently undo the rebuild.
    inter = await clicked(client, Poll(question="first round"))
    await client.dispatch(inter)

    rebuilt = await all_custom_ids(client, Poll(question="second round", votes=0))
    Poll.seen.clear()
    await client.dispatch(interaction(rebuilt[0], components=rebuilt, message_id=inter.message.id))

    assert Poll.seen == [0]


async def test_state_that_vanishes_mid_dispatch_reaches_the_hook_exactly_once(client: StubClient) -> None:
    # Converging has nothing to converge onto, so the transaction ends and the
    # hook answers -- once, with its own tree actually reaching the message.
    inter = await clicked(client, Vanish(label="votes"))
    before = Vanish.expired
    await client.dispatch(inter)

    assert Vanish.expired == before + 1
    initial = mock_of(inter.create_initial_response)
    initial.assert_awaited_once()
    assert initial.await_args is not None
    assert initial.await_args.args[0] == hikari.ResponseType.MESSAGE_UPDATE
    payload, _ = next(iter(initial.await_args.kwargs["components"])).build()
    assert payload["content"] == "This poll has expired."


# --- growing a schema ---


def older_shape() -> schema_.StateSchema:
    """Return the schema Poll had before ``votes`` was appended to it."""
    return schema_.StateSchema(POLL.schema.fields[:1])


async def test_a_message_written_before_a_defaulted_field_was_appended_still_reads(
    client: StubClient,
) -> None:
    was = older_shape()
    stale = anchor.MessageAnchor(fingerprint=was.fingerprint, seq=1, state=was.pack(["Ship it?"])).encode()
    rendered = codec.CustomID(
        raw_cookie=POLL.cookie,
        handler=codec.make_handler_token("vote", 1),
        fragment=stale,
    ).encode(view_name="place-poll")

    Poll.seen.clear()
    await client.dispatch(interaction(rendered, components=[rendered]))
    assert Poll.seen == [0]


def test_a_record_written_before_a_defaulted_field_was_appended_still_reads() -> None:
    was = schema_.StateSchema(LEDGER.schema.fields[:1])
    record = msgspec.json.encode({"v": 1, "n": "place-ledger", "f": was.fingerprint, "d": {"label": "votes"}})

    assert serde.loads(record, meta=LEDGER) == Ledger(label="votes", hits=0)


def test_a_shape_this_view_never_had_is_still_refused() -> None:
    record = msgspec.json.encode({"v": 1, "n": "place-ledger", "f": "!!!", "d": {"label": "votes"}})

    with pytest.raises(risa.StateDecodeError):
        serde.loads(record, meta=LEDGER)


# --- wiring stores onto the client ---


def test_a_view_naming_a_store_the_client_does_not_have_is_refused_at_startup() -> None:
    @risa.register(name="place-elsewhere", version=1, state=risa.InStore(store="redis", ttl=60.0))
    class Elsewhere(risa.View):
        count: int = 0

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.press, label="x"))

        @risa.handler
        async def press(self, _: risa.ComponentContext) -> None: ...

    built = StubClient(unittest.mock.Mock(spec=hikari.api.RESTClient))
    with pytest.raises(risa.ViewDeclarationError):
        built.add_view(Elsewhere)


def test_a_store_backed_view_on_the_improvised_default_store_warns(caplog: pytest.LogCaptureFixture) -> None:
    # It forfeits the restart-survival that is the entire reason to use a store.
    StubClient(unittest.mock.Mock(spec=hikari.api.RESTClient)).add_view(Ledger)
    assert "empties on restart" in caplog.text


def test_a_store_passed_in_is_not_warned_about(caplog: pytest.LogCaptureFixture) -> None:
    StubClient(
        unittest.mock.Mock(spec=hikari.api.RESTClient),
        stores={"default": RecordingStore()},
    ).add_view(Ledger)
    assert "empties on restart" not in caplog.text


def test_the_clients_stores_are_readable_but_not_writable(client: StubClient) -> None:
    assert sorted(client.stores) == ["default"]
    with pytest.raises(TypeError):
        client.stores["other"] = RecordingStore()  # type: ignore[index]
