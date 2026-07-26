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
import linkd
import msgspec
import pytest

import risa
from risa import ui
from risa.internal import constants
from risa.internal import registry
from risa.state import schema
from tests.helpers import RecordingStore
from tests.helpers import StubClient
from tests.helpers import first_custom_id
from tests.helpers import interaction


class Row(msgspec.Struct):
    name: str
    score: int


def meta_of(cls: type[risa.View]) -> registry.ViewMeta:
    return typing.cast("registry.ViewMeta", getattr(cls, constants.VIEW_META))


# --- partitioning fields ---


def test_plain_fields_are_durable_and_props_are_not() -> None:
    class Mixed(msgspec.Struct):
        guild_id: int
        page: int = 0
        rows: schema.Prop[list[Row]] = msgspec.field(default_factory=list[Row])

    partitioned = schema.StateSchema.of(Mixed, view_name="mixed")
    assert [field.name for field in partitioned.fields] == ["guild_id", "page"]


def test_a_prop_annotation_unwraps_to_the_type_it_holds() -> None:
    class Wrapped(msgspec.Struct):
        page: schema.Prop[int] = 0
        rows: list[Row] = msgspec.field(default_factory=list[Row])

    partitioned = schema.StateSchema.of(Wrapped, view_name="wrapped")
    assert [field.annotation for field in partitioned.fields] == [list[Row]]


def test_a_view_of_only_props_has_nothing_durable() -> None:
    class AllProps(msgspec.Struct):
        rows: schema.Prop[list[int]] = msgspec.field(default_factory=list[int])

    assert schema.StateSchema.of(AllProps, view_name="props").durable is False


def test_a_view_of_only_durable_fields_is_durable() -> None:
    class AllState(msgspec.Struct):
        question: str

    assert schema.StateSchema.of(AllState, view_name="state").durable is True


def test_values_reads_the_durable_fields_in_declaration_order() -> None:
    class Mixed(msgspec.Struct):
        question: str
        votes: int = 0
        rows: schema.Prop[list[int]] = msgspec.field(default_factory=list[int])

    partitioned = schema.StateSchema.of(Mixed, view_name="mixed")
    assert partitioned.values(Mixed(question="Ship it?", votes=3, rows=[9])) == ["Ship it?", 3]


def test_a_prop_costs_nothing_against_what_is_packed() -> None:
    class Bare(msgspec.Struct):
        page: int = 0

    class Padded(msgspec.Struct):
        page: int = 0
        rows: schema.Prop[list[int]] = msgspec.field(default_factory=lambda: list(range(500)))

    bare = schema.StateSchema.of(Bare, view_name="bare")
    padded = schema.StateSchema.of(Padded, view_name="padded")
    assert bare.pack(bare.values(Bare(page=3))) == padded.pack(padded.values(Padded(page=3)))


def test_marking_a_field_a_prop_changes_the_schema_fingerprint() -> None:
    class Durable(msgspec.Struct):
        page: int = 0

    class Fresh(msgspec.Struct):
        page: schema.Prop[int] = 0

    assert (
        schema.StateSchema.of(Durable, view_name="a").fingerprint
        != schema.StateSchema.of(
            Fresh,
            view_name="b",
        ).fingerprint
    )


# --- declaration rules, all at import time ---


def test_a_prop_without_a_default_is_refused() -> None:
    class Bad(msgspec.Struct):
        rows: schema.Prop[list[int]]

    with pytest.raises(risa.ViewDeclarationError, match="no default"):
        schema.StateSchema.of(Bad, view_name="bad")


def test_a_marker_buried_in_a_union_is_refused() -> None:
    class Buried(msgspec.Struct):
        page: schema.Prop[int] | None = None

    with pytest.raises(risa.ViewDeclarationError, match="hides a risa"):
        schema.StateSchema.of(Buried, view_name="bad")


def test_a_marker_buried_in_a_collection_is_refused() -> None:
    class Buried(msgspec.Struct):
        rows: list[schema.Prop[int]] = msgspec.field(default_factory=list[int])

    with pytest.raises(risa.ViewDeclarationError, match="hides a risa"):
        schema.StateSchema.of(Buried, view_name="bad")


def test_a_prop_holding_an_optional_is_the_legal_spelling() -> None:
    class Optional(msgspec.Struct):
        page: schema.Prop[int | None] = None

    assert schema.StateSchema.of(Optional, view_name="ok").durable is False


def test_a_frozen_view_is_refused_because_handlers_mutate_it() -> None:
    class Frozen(risa.View, frozen=True):  # type: ignore[reportGeneralTypeIssues]
        count: int = 0

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

    with pytest.raises(risa.ViewDeclarationError, match="frozen"):
        risa.register(name="schema-frozen", version=1)(Frozen)


def test_a_view_with_nothing_to_store_may_not_ask_for_a_store() -> None:
    class AllProps(risa.View):
        rows: risa.Prop[list[int]] = msgspec.field(default_factory=list[int])

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

    with pytest.raises(risa.ViewDeclarationError, match="nothing to store"):
        risa.register(name="schema-nostate", version=1, state=risa.InStore(ttl=60.0))(AllProps)


def test_on_state_missing_is_refused_where_state_cannot_go_missing() -> None:
    class Hooked(risa.View):
        count: int = 0

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

        @classmethod
        @typing.override
        async def on_state_missing(cls, ctx: risa.ComponentContext) -> None: ...

    with pytest.raises(risa.ViewDeclarationError, match="cannot go missing"):
        risa.register(name="schema-hooked", version=1)(Hooked)


def test_on_state_missing_is_allowed_on_a_stored_view() -> None:
    class Hooked(risa.View):
        count: int = 0

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

        @classmethod
        @typing.override
        async def on_state_missing(cls, ctx: risa.ComponentContext) -> None: ...

    assert risa.register(name="schema-stored", version=1, state=risa.InStore(ttl=60.0))(Hooked) is Hooked


# --- placement on the registered view ---


def test_a_view_defaults_to_carrying_its_state_in_the_message() -> None:
    @risa.register(name="schema-default", version=1)
    class Default(risa.View):
        count: int = 0

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

    assert meta_of(Default).placement == risa.InMessage()


def test_a_stored_view_records_its_store_and_ttl() -> None:
    @risa.register(name="schema-ttl", version=1, state=risa.InStore(store="redis", ttl=60.0))
    class Stored(risa.View):
        count: int = 0

        @typing.override
        def render(self) -> ui.Layout:
            return ui.Row()

    placement = meta_of(Stored).placement
    assert isinstance(placement, risa.InStore)
    assert placement.store == "redis"
    assert placement.ttl == pytest.approx(60.0)


def test_a_ttl_cannot_be_written_down_for_a_message_resident_view() -> None:
    # Not a runtime check: InMessage simply has no ttl to pass.
    assert not hasattr(risa.InMessage(), "ttl")


# --- the load() lifecycle ---


class Database:
    def __init__(self) -> None:
        self.calls = 0

    async def rows(self, page: int) -> list[int]:
        self.calls += 1
        return [page * 10 + n for n in range(3)]


@risa.register(name="schema-board", version=1)
class Board(risa.View):
    page: int = 0
    rows: risa.Prop[list[int]] = msgspec.field(default_factory=list[int])
    seen: typing.ClassVar[list[list[int]]] = []

    @typing.override
    async def load(self, db: Database = linkd.INJECTED) -> None:
        self.rows = await db.rows(self.page)

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.turn.bind(1), label=",".join(str(row) for row in self.rows) or "-"))

    @risa.handler
    async def turn(self, _: risa.ComponentContext, delta: int) -> None:
        self.page += delta
        type(self).seen.append(list(self.rows))


@risa.register(name="schema-quiet", version=1)
class NoHook(risa.View):
    count: int = 0

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.press, label="x"))

    @risa.handler
    async def press(self, _: risa.ComponentContext) -> None: ...


@pytest.fixture
def database() -> Database:
    return Database()


@pytest.fixture
def client(database: Database) -> StubClient:
    built = StubClient(unittest.mock.Mock(spec=hikari.api.RESTClient), store=RecordingStore())
    built.add_view(Board)
    built.add_view(NoHook)
    built.di.registry_for(risa.Contexts.DEFAULT).register_value(Database, database)
    return built


async def test_load_fills_props_before_the_initial_render(client: StubClient, database: Database) -> None:
    builders = await client.build(Board(page=2))
    payload, _ = builders[0].build()

    assert payload["components"][0]["label"] == "20,21,22"
    assert database.calls == 1


async def test_load_fills_props_before_every_dispatch(client: StubClient, database: Database) -> None:
    custom_id = await first_custom_id(client, Board(page=2))
    Board.seen.clear()
    await client.dispatch(interaction(custom_id))

    # The handler saw refilled rows, not the empty default it was rebuilt with.
    assert Board.seen == [[20, 21, 22]]
    assert database.calls == 2


async def test_load_is_resolved_against_the_clients_dependencies(client: StubClient) -> None:
    # The hook asked for a Database and got the one registered on the client,
    # rather than smuggling it through the view's own state.
    builders = await client.build(Board(page=0))
    payload, _ = builders[0].build()
    assert payload["components"][0]["label"] == "0,1,2"


async def test_a_view_without_the_hook_pays_nothing(client: StubClient) -> None:
    custom_id = await first_custom_id(client, NoHook())
    await client.dispatch(interaction(custom_id))
