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
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry

if typing.TYPE_CHECKING:
    import contextlib


class Write(msgspec.Struct, frozen=True):
    """One call to :meth:`RecordingStore.put`."""

    key: str
    ttl: float | None


class RecordingStore(risa.Store):
    """A store that keeps a log of what was written, and defers to a real one.

    Lets a test assert on what the client asked the store to do without reading
    the store's internals, which is the only part of this a test has any business
    knowing about.
    """

    __slots__ = ("_inner", "writes")

    def __init__(self) -> None:
        self._inner = risa.MemoryStore()
        self.writes: list[Write] = []

    @typing.override
    async def get(self, key: str) -> bytes | None:
        return await self._inner.get(key)

    @typing.override
    async def get_versioned(self, key: str) -> tuple[bytes, int] | None:
        return await self._inner.get_versioned(key)

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None = None) -> None:
        self.writes.append(Write(key=key, ttl=ttl))
        await self._inner.put(key, value, ttl=ttl)

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None = None) -> bool:
        self.writes.append(Write(key=key, ttl=ttl))
        return await self._inner.put_if_version(key, value, expected=expected, ttl=ttl)

    @typing.override
    async def delete(self, key: str) -> None:
        await self._inner.delete(key)

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        await self._inner.touch(key, ttl=ttl)

    @typing.override
    def lock(self, key: str) -> contextlib.AbstractAsyncContextManager[None]:
        return self._inner.lock(key)


class StubClient(risa.Client):
    """A client detached from any bot, so dispatch can be driven directly."""

    __slots__ = ()

    @property
    @typing.override
    def app(self) -> hikari.RESTAware:
        raise NotImplementedError

    async def dispatch(self, interaction: hikari.ComponentInteraction) -> None:
        """Feed an interaction in, standing in for a gateway event or a request."""
        await self._process_component_interaction(interaction)


@risa.register(name="test-poll", version=1)
class Poll(risa.View):
    question: str
    votes: int = 0
    missing_seen: typing.ClassVar[int] = 0

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(content=f"{self.question} -- {self.votes}"),
            ui.Row(ui.Button(self.vote, label="Vote")),
        )

    @risa.handler
    async def vote(self, _: risa.ComponentContext) -> None:
        self.votes += 1

    @classmethod
    @typing.override
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        cls.missing_seen += 1


@risa.register(name="test-ping", version=1)
class Ping(risa.View):
    pressed: typing.ClassVar[int] = 0

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.pong, label="Pong"))

    @risa.handler
    async def pong(self, _: risa.ComponentContext) -> None:
        type(self).pressed += 1


@risa.register(name="test-chooser", version=1)
class Chooser(risa.View):
    picked: list[str] = msgspec.field(default_factory=list[str])

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(
            ui.Button(self.pick.bind("a", 1), label="A"),
            ui.Button(self.pick.bind("b", 2), label="B"),
        )

    @risa.handler
    async def pick(self, _: risa.ComponentContext, name: str, count: int) -> None:
        self.picked.append(name * count)

    @risa.handler
    async def opts(self, _: risa.ComponentContext, first: int = 0, second: int = 0) -> None: ...


@risa.register(name="test-echo", version=1)
class Echo(risa.View):
    heard: typing.ClassVar[list[int]] = []

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.hear.bind(-5), label="x"))

    @risa.handler
    async def hear(self, _: risa.ComponentContext, value: int) -> None:
        type(self).heard.append(value)


@risa.register(name="test-versioned", version=1)
class Versioned(risa.View):
    calls: typing.ClassVar[list[str]] = []

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.act, label="Act"))

    @risa.handler(handler_id="act", version=1)
    async def act_old(self, _: risa.ComponentContext) -> None:
        type(self).calls.append("v1")

    @risa.handler(handler_id="act", version=2)
    async def act(self, _: risa.ComponentContext) -> None:
        type(self).calls.append("v2")


@risa.register(name="test-board", version=1, persist=False)
class Board(risa.View):
    rows: list[int] = msgspec.field(default_factory=list[int])
    seen: typing.ClassVar[list[tuple[int, list[int]]]] = []

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(" ".join(str(row) for row in self.rows)),
            ui.Row(ui.Button(self.show.bind(2), label="Next")),
        )

    @risa.handler
    async def show(self, _: risa.ComponentContext, page: int) -> None:
        type(self).seen.append((page, list(self.rows)))


@risa.register(name="test-outdated", version=1)
class Outdated(risa.View):
    outdated_seen: typing.ClassVar[int] = 0

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.current, label="x"))

    @risa.handler
    async def current(self, _: risa.ComponentContext) -> None: ...

    @risa.handler
    async def argful(self, _: risa.ComponentContext, count: int) -> None: ...

    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        cls.outdated_seen += 1


@pytest.fixture
def store() -> RecordingStore:
    return RecordingStore()


@pytest.fixture
def client(store: RecordingStore) -> StubClient:
    built = StubClient(unittest.mock.Mock(spec=hikari.api.RESTClient), store=store)
    built.add_view(Poll)
    built.add_view(Ping)
    built.add_view(Chooser)
    built.add_view(Echo)
    built.add_view(Versioned)
    built.add_view(Outdated)
    built.add_view(Board)
    return built


def interaction(custom_id: str) -> hikari.ComponentInteraction:
    mock = unittest.mock.Mock(spec=hikari.ComponentInteraction)
    mock.custom_id = custom_id
    mock.id = 1
    return typing.cast("hikari.ComponentInteraction", mock)


def find_custom_ids(node: object) -> list[str]:
    """Return every ``custom_id`` anywhere in a built payload, in order."""
    found: list[str] = []
    if isinstance(node, dict):
        value = typing.cast("dict[str, object]", node).get("custom_id")
        if isinstance(value, str):
            found.append(value)
        for nested in typing.cast("dict[str, object]", node).values():
            found.extend(find_custom_ids(nested))
    elif isinstance(node, list):
        for item in typing.cast("list[object]", node):
            found.extend(find_custom_ids(item))
    return found


async def all_custom_ids(client: StubClient, view: risa.View) -> list[str]:
    builders = await client.build(view)
    found: list[str] = []
    for builder in builders:
        payload, _ = builder.build()
        found.extend(find_custom_ids(payload))
    return found


async def first_custom_id(client: StubClient, view: risa.View) -> str:
    found = await all_custom_ids(client, view)
    assert found
    return found[0]


def test_a_view_with_fields_is_not_stateless() -> None:
    meta = typing.cast("registry.ViewMeta", getattr(Poll, constants.VIEW_META))
    assert meta.stateless is False


def test_a_view_without_fields_is_stateless() -> None:
    meta = typing.cast("registry.ViewMeta", getattr(Ping, constants.VIEW_META))
    assert meta.stateless is True


async def test_building_a_stateful_view_writes_its_state(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Poll(question="Ship it?"))
    decoded = codec.decode_custom_id(custom_id)
    assert decoded is not None
    assert await client.store.get(decoded.payload) is not None


async def test_a_stateful_component_carries_a_state_key(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Poll(question="Ship it?"))
    decoded = codec.decode_custom_id(custom_id)
    assert decoded is not None
    assert len(decoded.payload) == codec.STATE_KEY_LENGTH


async def test_a_stateless_component_carries_no_state_key(client: StubClient) -> None:
    decoded = codec.decode_custom_id(await first_custom_id(client, Ping()))
    assert decoded is not None
    assert not decoded.payload


async def test_a_failed_build_leaves_nothing_in_the_store(client: StubClient, store: RecordingStore) -> None:
    @risa.register(name="test-stray", version=1)
    class Stray(risa.View):
        question: str

        @typing.override
        def render(self) -> ui.Layout:
            # Routes to a handler belonging to another view, so the build fails.
            return ui.Row(ui.Button(Poll(question="?").vote, label="Nope"))

    client.add_view(Stray)
    with pytest.raises(risa.LayoutError):
        await client.build(Stray(question="?"))

    assert store.writes == []


async def test_state_survives_a_click_and_is_written_back(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Poll(question="Ship it?"))
    decoded = codec.decode_custom_id(custom_id)
    assert decoded is not None

    for expected in (1, 2, 3):
        await client.dispatch(interaction(custom_id))
        raw = await client.store.get(decoded.payload)
        assert raw is not None
        assert f'"votes":{expected}' in raw.decode()


async def test_a_stateless_view_dispatches_without_touching_the_store(
    client: StubClient,
    store: RecordingStore,
) -> None:
    custom_id = await first_custom_id(client, Ping())
    before = Ping.pressed
    await client.dispatch(interaction(custom_id))

    assert Ping.pressed == before + 1
    assert store.writes == []


async def test_a_click_on_evicted_state_reaches_on_state_missing(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Poll(question="Ship it?"))
    decoded = codec.decode_custom_id(custom_id)
    assert decoded is not None
    await client.store.delete(decoded.payload)

    before = Poll.missing_seen
    await client.dispatch(interaction(custom_id))
    assert Poll.missing_seen == before + 1


async def test_undecodable_state_reaches_on_state_missing(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Poll(question="Ship it?"))
    decoded = codec.decode_custom_id(custom_id)
    assert decoded is not None
    await client.store.put(decoded.payload, b'{"v":1,"n":"test-poll","d":{"question":42}}')

    before = Poll.missing_seen
    await client.dispatch(interaction(custom_id))
    assert Poll.missing_seen == before + 1


async def test_a_custom_id_from_another_library_is_ignored(client: StubClient, store: RecordingStore) -> None:
    await client.dispatch(interaction("other:button"))
    assert store.writes == []


async def test_an_unregistered_cookie_is_ignored(client: StubClient, store: RecordingStore) -> None:
    unknown = codec.encode_custom_id(
        codec.CustomID(
            version=codec.CODEC_VERSION,
            raw_cookie=codec.make_cookie("nobody", 1),
            handler=codec.make_handler_token("vote", 1),
            payload=codec.make_state_key(),
        ),
        view_name="nobody",
    )
    await client.dispatch(interaction(unknown))
    assert store.writes == []


async def test_a_view_ttl_reaches_every_write_it_causes() -> None:
    @risa.register(name="test-ttl", version=1, ttl=60.0)
    class Ephemeral(risa.View):
        question: str

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.noop, label="x"))

        @risa.handler
        async def noop(self, _: risa.ComponentContext) -> None: ...

    meta = typing.cast("registry.ViewMeta", getattr(Ephemeral, constants.VIEW_META))
    assert meta.ttl == pytest.approx(60.0)

    store = RecordingStore()
    built = StubClient(unittest.mock.Mock(spec=hikari.api.RESTClient), store=store)
    built.add_view(Ephemeral)
    custom_id = await first_custom_id(built, Ephemeral(question="?"))
    await built.dispatch(interaction(custom_id))

    # The write on build and the write-back after the handler: the clock is reset
    # on both, so a view still being clicked never expires.
    assert [write.ttl for write in store.writes] == [60.0, 60.0]


async def test_a_view_without_a_ttl_stores_state_that_does_not_expire(
    client: StubClient,
    store: RecordingStore,
) -> None:
    custom_id = await first_custom_id(client, Poll(question="Ship it?"))
    await client.dispatch(interaction(custom_id))

    assert [write.ttl for write in store.writes] == [None, None]


async def test_bound_args_reach_a_stateful_handler_beside_its_state(client: StubClient) -> None:
    ids = await all_custom_ids(client, Chooser())
    assert len(ids) == 2

    await client.dispatch(interaction(ids[0]))
    await client.dispatch(interaction(ids[1]))

    decoded = codec.decode_custom_id(ids[0])
    assert decoded is not None
    raw = await client.store.get(decoded.payload[: codec.STATE_KEY_LENGTH])
    assert raw is not None
    assert '"picked":["a","bb"]' in raw.decode()


async def test_bound_args_reach_a_stateless_handler(client: StubClient) -> None:
    custom_id = await first_custom_id(client, Echo())
    before = list(Echo.heard)
    await client.dispatch(interaction(custom_id))
    assert Echo.heard == [*before, -5]


async def test_handler_versions_coexist_and_route_to_their_own_code(client: StubClient) -> None:
    current = await first_custom_id(client, Versioned())
    old = codec.encode_custom_id(
        codec.CustomID(
            version=codec.CODEC_VERSION,
            raw_cookie=codec.make_cookie("test-versioned", 1),
            handler=codec.make_handler_token("act", 1),
        ),
        view_name="test-versioned",
    )

    Versioned.calls.clear()
    await client.dispatch(interaction(current))
    await client.dispatch(interaction(old))
    assert Versioned.calls == ["v2", "v1"]


async def test_a_discarded_handler_routes_to_on_outdated(client: StubClient) -> None:
    gone = codec.encode_custom_id(
        codec.CustomID(
            version=codec.CODEC_VERSION,
            raw_cookie=codec.make_cookie("test-outdated", 1),
            handler=codec.make_handler_token("gone", 1),
        ),
        view_name="test-outdated",
    )

    before = Outdated.outdated_seen
    await client.dispatch(interaction(gone))
    assert Outdated.outdated_seen == before + 1


async def test_an_in_place_signature_change_routes_to_on_outdated(client: StubClient) -> None:
    changed = codec.encode_custom_id(
        codec.CustomID(
            version=codec.CODEC_VERSION,
            raw_cookie=codec.make_cookie("test-outdated", 1),
            handler=codec.make_handler_token("argful", 1),
            payload=codec.make_signature_fingerprint(["s"]) + chr(1) + "a",
        ),
        view_name="test-outdated",
    )

    before = Outdated.outdated_seen
    await client.dispatch(interaction(changed))
    assert Outdated.outdated_seen == before + 1


async def test_a_component_missing_its_args_routes_to_on_outdated(client: StubClient) -> None:
    argless = codec.encode_custom_id(
        codec.CustomID(
            version=codec.CODEC_VERSION,
            raw_cookie=codec.make_cookie("test-outdated", 1),
            handler=codec.make_handler_token("argful", 1),
        ),
        view_name="test-outdated",
    )

    before = Outdated.outdated_seen
    await client.dispatch(interaction(argless))
    assert Outdated.outdated_seen == before + 1


def test_overriding_on_outdated_is_recorded_on_the_meta() -> None:
    assert typing.cast("registry.ViewMeta", getattr(Outdated, constants.VIEW_META)).handles_outdated is True
    assert typing.cast("registry.ViewMeta", getattr(Poll, constants.VIEW_META)).handles_outdated is False


def test_bind_normalises_keywords_to_positions() -> None:
    view = Chooser()
    positional = view.pick.bind("a", 1).payload
    assert view.pick.bind("a", count=1).payload == positional
    assert view.pick.bind(name="a", count=1).payload == positional


def test_binding_an_unknown_keyword_fails() -> None:
    with pytest.raises(risa.ArgBindError):
        Chooser().pick.bind("a", 1, nope=2)  # type: ignore[call-arg]


def test_binding_with_a_gap_fails() -> None:
    with pytest.raises(risa.ArgBindError):
        Chooser().opts.bind(second=1)


def test_a_handler_with_required_args_cannot_be_placed_bare() -> None:
    with pytest.raises(risa.ArgBindError):
        ui.Button(Chooser().pick, label="x")  # type: ignore[arg-type]


def test_a_plain_callable_is_not_a_handler() -> None:
    async def loose(_: risa.ComponentContext) -> None: ...

    with pytest.raises(risa.NotAHandlerError):
        ui.Button(loose, label="x")  # type: ignore[arg-type]


def test_a_props_only_view_is_stateless() -> None:
    meta = typing.cast("registry.ViewMeta", getattr(Board, constants.VIEW_META))
    assert meta.stateless is True


async def test_building_a_props_only_view_writes_nothing(client: StubClient, store: RecordingStore) -> None:
    custom_id = await first_custom_id(client, Board(rows=[5, 6]))
    assert store.writes == []

    decoded = codec.decode_custom_id(custom_id)
    assert decoded is not None
    # No state key: the payload is the fingerprint plus one small int frame.
    assert len(decoded.payload) == codec.FINGERPRINT_LENGTH + 2


async def test_a_props_only_view_dispatches_from_defaults(client: StubClient, store: RecordingStore) -> None:
    custom_id = await first_custom_id(client, Board(rows=[5, 6]))

    Board.seen.clear()
    await client.dispatch(interaction(custom_id))
    # The wire arg arrives; the props reset to their defaults rather than
    # replaying what was rendered.
    assert Board.seen == [(2, [])]
    assert store.writes == []


def test_persist_false_requires_a_default_on_every_field() -> None:
    class Bare(risa.View):
        rows: list[int]

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

    with pytest.raises(risa.ViewDeclarationError):
        risa.register(name="test-board-bad", version=1, persist=False)(Bare)


def test_persist_false_rejects_a_ttl() -> None:
    class Timed(risa.View):
        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

    with pytest.raises(risa.ViewDeclarationError):
        risa.register(name="test-board-ttl", version=1, ttl=60.0, persist=False)(Timed)


def test_two_handlers_sharing_id_and_version_collide() -> None:
    class Dup(risa.View):
        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

        @risa.handler(handler_id="same")
        async def one(self, _: risa.ComponentContext) -> None: ...

        @risa.handler(handler_id="same")
        async def two(self, _: risa.ComponentContext) -> None: ...

    with pytest.raises(risa.DuplicateHandlerError):
        risa.register(name="test-dup", version=1)(Dup)


def test_a_wire_parameter_after_an_injected_one_is_rejected() -> None:
    class BadSig(risa.View):
        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

        @risa.handler
        async def bad(self, _: risa.ComponentContext, dependency: object, count: int) -> None: ...

    with pytest.raises(risa.HandlerSignatureError):
        risa.register(name="test-bad-sig", version=1)(BadSig)
