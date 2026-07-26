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
import msgspec
import pytest

import risa
from risa import ui
from risa.internal import anchor as anchor_
from risa.internal import codec
from risa.internal import reader
from risa.state import backend
from risa.state import message as message_
from risa.state import serde
from risa.state import stored
from tests.helpers import RecordingStore
from tests.helpers import component as button
from tests.helpers import interaction
from tests.helpers import meta_of

TOKEN = codec.make_handler_token("press", 1)


@risa.register(name="test-session-board", version=1)
class Board(risa.View):
    title: str
    count: int = 0
    rows: risa.Prop[list[str]] = msgspec.field(default_factory=list[str])

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.press, label=self.title))

    @risa.handler
    async def press(self, _: risa.ComponentContext) -> None:
        self.count += 1


@risa.register(name="test-session-tally", version=1, state=risa.InStore(ttl=None))
class Tally(risa.View):
    label: str
    hits: int = 0

    @typing.override
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.bump, label=self.label))

    @risa.handler
    async def bump(self, _: risa.ComponentContext) -> None:
        self.hits += 1


BOARD: typing.Final = meta_of(Board)
TALLY: typing.Final = meta_of(Tally)


class OutageStore(risa.MemoryStore):
    """A store that is reachable for nothing at all."""

    __slots__ = ()

    @typing.override
    async def get_versioned(self, key: str) -> tuple[bytes, int] | None:
        reason = "connection refused"
        raise risa.StoreUnavailableError(reason, key)


def row(*children: hikari.PartialComponent) -> hikari.PartialComponent:
    """Return a component holding others."""
    mock = unittest.mock.Mock(spec=hikari.ActionRowComponent)
    mock.components = list(children)
    return typing.cast("hikari.PartialComponent", mock)


def section(accessory: hikari.PartialComponent) -> hikari.PartialComponent:
    """Return a section whose only interactive child hangs off its accessory."""
    mock = unittest.mock.Mock(spec=hikari.SectionComponent)
    mock.components = []
    mock.accessory = accessory
    return typing.cast("hikari.PartialComponent", mock)


def clicked(*components: hikari.PartialComponent, message_id: int = 1) -> hikari.ComponentInteraction:
    """Return an interaction whose message carries ``components``."""
    return interaction("", components=components, message_id=message_id)


def anchor_for(view: Board, *, seq: int = 1, fingerprint: str | None = None) -> str:
    """Encode a message anchor holding ``view``'s durable fields."""
    return anchor_.MessageAnchor(
        fingerprint=BOARD.schema.fingerprint if fingerprint is None else fingerprint,
        seq=seq,
        state=BOARD.schema.pack(BOARD.schema.values(view)),
    ).encode()


def spread(text: str, *, parts: int = 1, cookie: str | None = None) -> list[hikari.PartialComponent]:
    """Split an anchor across ``parts`` components, as the build pass would."""
    capacity = -(-len(text) // parts)
    fragments = anchor_.split_across(text, [capacity] * parts)
    assert fragments is not None
    return [
        button(
            codec.CustomID(
                raw_cookie=BOARD.cookie if cookie is None else cookie,
                handler=TOKEN,
                fragment_index=index,
                fragment=fragment,
            ).encode(view_name="test-session-board"),
        )
        for index, fragment in enumerate(fragments)
    ]


def clicked_component(*, fragment: str = "", index: int = 0) -> codec.CustomID:
    """Return the decoded component a session is opened for."""
    return codec.CustomID(raw_cookie=BOARD.cookie, handler=TOKEN, fragment_index=index, fragment=fragment)


def message_session(
    inter: hikari.ComponentInteraction,
    *,
    cache: message_.MessageStateCache | None = None,
) -> message_.MessageSession:
    """Open a session against a message-resident view."""
    return message_.MessageBackend(cache).session(meta=BOARD, component=clicked_component(), interaction=inter)


def stored_session(
    *,
    key: str,
    store: risa.Store,
    ttl: float | None = None,
) -> stored.StoredSession:
    """Open a session against a stored view, with a ttl the test picks."""
    return stored.StoredSession(meta=TALLY, key=key, store=store, ttl=ttl, interaction=interaction(""))


async def store_holding(view: Tally, *, key: str) -> RecordingStore:
    """Return a store already holding ``view`` under ``key``."""
    store = RecordingStore()
    await store.put(key, serde.dumps(view, meta=TALLY))
    store.writes.clear()
    return store


# --- reading fragments off a message ---


def test_a_fragment_nested_any_number_of_levels_deep_is_found() -> None:
    parts = spread(anchor_for(Board(title="hi")))
    found = reader.fragments_of([row(row(*parts))], cookie=BOARD.cookie)
    assert len(found) == 1


def test_a_section_accessory_is_not_missed() -> None:
    parts = spread(anchor_for(Board(title="hi")))
    found = reader.fragments_of([section(parts[0])], cookie=BOARD.cookie)
    assert len(found) == 1


def test_another_views_components_contribute_nothing() -> None:
    mine = spread(anchor_for(Board(title="hi")))
    theirs = spread("m00xxx000stray", cookie=codec.make_cookie("someone-else", 1))
    assert reader.fragments_of([*theirs, *mine], cookie=BOARD.cookie) == reader.fragments_of(mine, cookie=BOARD.cookie)


def test_components_risa_did_not_write_contribute_nothing() -> None:
    assert reader.fragments_of([button("miru:whatever:1")], cookie=BOARD.cookie) == []


def test_fragments_are_returned_in_the_order_the_message_lists_them() -> None:
    parts = spread(anchor_for(Board(title="hello there")), parts=3)
    assert [index for index, _ in reader.fragments_of(parts, cookie=BOARD.cookie)] == [0, 1, 2]


# --- a message-resident session ---


async def test_state_rejoined_from_a_message_round_trips() -> None:
    view = Board(title="Standings", count=7)
    async with message_session(clicked(*spread(anchor_for(view), parts=4))) as session:
        outcome = await session.restore()

    assert isinstance(outcome, backend.Restored)
    assert outcome.view == Board(title="Standings", count=7)


async def test_a_prop_comes_back_at_its_default_rather_than_off_the_message() -> None:
    view = Board(title="Standings", rows=["written by load()"])
    async with message_session(clicked(*spread(anchor_for(view)))) as session:
        outcome = await session.restore()

    assert isinstance(outcome, backend.Restored)
    assert typing.cast("Board", outcome.view).rows == []


async def test_a_snapshot_this_process_has_already_replaced_is_overtaken() -> None:
    # Discord attaches the message as it was when the click happened, so a click
    # made before an edit landed carries state this process knows is stale.
    was = anchor_for(Board(title="Standings", count=1), seq=1)
    cache = message_.MessageStateCache()
    cache.note(1, BOARD.cookie, anchor=anchor_for(Board(title="Standings", count=99), seq=5), replaced=was)

    async with message_session(clicked(*spread(was)), cache=cache) as session:
        outcome = await session.restore()

    assert isinstance(outcome, backend.Restored)
    assert typing.cast("Board", outcome.view).count == 99


async def test_a_snapshot_this_process_never_replaced_is_believed() -> None:
    # Told apart by what was replaced rather than by the counter alone: a
    # message rebuilt from scratch counts from zero, and reverting it to
    # whatever this process last wrote would silently undo the rebuild.
    cache = message_.MessageStateCache()
    cache.note(
        1,
        BOARD.cookie,
        anchor=anchor_for(Board(title="Standings", count=9), seq=9),
        replaced=anchor_for(Board(title="Standings", count=8), seq=8),
    )

    rebuilt = anchor_for(Board(title="Reset", count=0), seq=0)
    async with message_session(clicked(*spread(rebuilt)), cache=cache) as session:
        outcome = await session.restore()

    assert isinstance(outcome, backend.Restored)
    assert outcome.view == Board(title="Reset", count=0)


async def test_what_another_view_wrote_to_the_same_message_is_never_consulted() -> None:
    # A message may carry more than one view, and two views with the same field
    # shape would otherwise read each other's state without anything noticing.
    was = anchor_for(Board(title="Standings", count=1), seq=1)
    cache = message_.MessageStateCache()
    cache.note(
        1,
        codec.make_cookie("someone-else", 1),
        anchor=anchor_for(Board(title="Theirs", count=99), seq=5),
        replaced=was,
    )

    async with message_session(clicked(*spread(was)), cache=cache) as session:
        outcome = await session.restore()

    assert isinstance(outcome, backend.Restored)
    assert outcome.view == Board(title="Standings", count=1)


async def test_a_message_missing_a_fragment_is_refused_rather_than_half_read() -> None:
    parts = spread(anchor_for(Board(title="a much longer title")), parts=3)
    async with message_session(clicked(parts[0], parts[2])) as session:
        assert await session.restore() is backend.Unreadably.OUTDATED


async def test_a_message_carrying_none_of_risas_components_is_outdated() -> None:
    async with message_session(clicked(button("miru:whatever:1"))) as session:
        assert await session.restore() is backend.Unreadably.OUTDATED


async def test_state_written_under_a_different_schema_is_refused() -> None:
    torn = anchor_for(Board(title="Standings"), fingerprint="zzz")
    async with message_session(clicked(*spread(torn))) as session:
        assert await session.restore() is backend.Unreadably.OUTDATED


async def test_a_message_session_never_reads_a_store() -> None:
    # The whole point of this placement: no infrastructure is consulted at all.
    async with message_session(clicked(*spread(anchor_for(Board(title="x"))))) as session:
        assert isinstance(await session.restore(), backend.Restored)


async def test_staging_advances_the_counter_only_once_an_edit_lands() -> None:
    async with message_session(clicked(*spread(anchor_for(Board(title="x"), seq=3)))) as session:
        assert isinstance(await session.restore(), backend.Restored)
        view = Board(title="x", count=1)

        first = anchor_.parse(await session.stage(view))
        assert isinstance(first, anchor_.MessageAnchor)
        assert first.seq == 4

        # An edit that never went out must not claim the counter.
        retried = anchor_.parse(await session.stage(view))
        assert isinstance(retried, anchor_.MessageAnchor)
        assert retried.seq == 4

        await session.published(view)
        again = anchor_.parse(await session.stage(view))
        assert isinstance(again, anchor_.MessageAnchor)
        assert again.seq == 5


async def test_a_published_edit_is_remembered_for_the_next_click() -> None:
    cache = message_.MessageStateCache()
    async with message_session(clicked(*spread(anchor_for(Board(title="x")))), cache=cache) as session:
        assert isinstance(await session.restore(), backend.Restored)
        view = Board(title="x", count=8)
        await session.stage(view)
        await session.published(view)

    written = cache.last_written(1, BOARD.cookie)
    assert written is not None
    parsed = anchor_.parse(written.anchor)
    assert isinstance(parsed, anchor_.MessageAnchor)
    assert parsed.seq == 2
    assert BOARD.schema.unpack(parsed.fingerprint, parsed.state) == ("x", 8)
    # And what it replaced, so the click already in flight is still recognised.
    assert written.superseded == (anchor_for(Board(title="x"), seq=1),)


async def test_a_handler_that_changed_nothing_owes_no_edit() -> None:
    async with message_session(clicked(*spread(anchor_for(Board(title="x", count=2))))) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        assert await session.settle(outcome.view) is False


async def test_a_handler_that_changed_state_without_rendering_owes_an_edit() -> None:
    async with message_session(clicked(*spread(anchor_for(Board(title="x", count=2))))) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        typing.cast("Board", outcome.view).count += 1
        assert await session.settle(outcome.view) is True


async def test_a_change_that_was_rendered_owes_nothing_further() -> None:
    async with message_session(clicked(*spread(anchor_for(Board(title="x", count=2))))) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        typing.cast("Board", outcome.view).count += 1
        await session.stage(outcome.view)
        await session.published(outcome.view)
        assert await session.settle(outcome.view) is False


async def test_only_a_prop_moving_owes_no_edit() -> None:
    # A prop is refilled every dispatch, so writing it back would be noise.
    async with message_session(clicked(*spread(anchor_for(Board(title="x"))))) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        typing.cast("Board", outcome.view).rows = ["fetched"]
        assert await session.settle(outcome.view) is False


async def test_two_clicks_on_one_message_do_not_interleave() -> None:
    cache = message_.MessageStateCache()
    order: list[str] = []

    async def click(name: str) -> None:
        async with message_session(clicked(*spread(anchor_for(Board(title="x")))), cache=cache):
            order.append(f"{name}-in")
            await asyncio.sleep(0)
            order.append(f"{name}-out")

    await asyncio.gather(click("a"), click("b"))
    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])


def test_each_message_gets_its_own_lock() -> None:
    cache = message_.MessageStateCache()
    assert cache.lock(1) is cache.lock(1)
    assert cache.lock(1) is not cache.lock(2)


def test_the_cache_forgets_the_oldest_message_rather_than_growing() -> None:
    cache = message_.MessageStateCache(max_entries=2)
    for message_id in (1, 2, 3):
        cache.note(message_id, BOARD.cookie, anchor="m", replaced="")

    assert cache.last_written(1, BOARD.cookie) is None
    assert cache.last_written(3, BOARD.cookie) is not None


# --- a stored session ---


async def test_state_read_from_a_store_round_trips() -> None:
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes", hits=3), key=key)

    async with stored_session(key=key, store=store) as session:
        outcome = await session.restore()

    assert isinstance(outcome, backend.Restored)
    assert outcome.view == Tally(label="Votes", hits=3)


async def test_a_record_that_is_gone_reads_as_missing() -> None:
    async with stored_session(key=anchor_.make_state_key(), store=risa.MemoryStore()) as session:
        assert await session.restore() is backend.Unreadably.MISSING


async def test_a_store_that_cannot_be_reached_is_not_reported_as_expiry() -> None:
    # An outage is not a state condition: there is no honest thing to tell the
    # user, so it is raised rather than dressed up as an expired view.
    async with stored_session(key=anchor_.make_state_key(), store=OutageStore()) as session:
        with pytest.raises(risa.StoreUnavailableError):
            await session.restore()


async def test_a_component_carrying_no_key_at_all_is_outdated() -> None:
    # What a view whose placement changed under live components looks like.
    async with stored_session(key="", store=risa.MemoryStore()) as session:
        assert await session.restore() is backend.Unreadably.OUTDATED


async def test_a_record_that_no_longer_fits_the_view_is_outdated() -> None:
    key = anchor_.make_state_key()
    store = risa.MemoryStore()
    await store.put(key, b'{"v":1,"n":"test-session-tally","f":"nope","d":{"label":"Votes"}}')

    async with stored_session(key=key, store=store) as session:
        assert await session.restore() is backend.Unreadably.OUTDATED


async def test_the_store_is_written_before_the_anchor_is_handed_back() -> None:
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes"), key=key)

    async with stored_session(key=key, store=store) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        typing.cast("Tally", outcome.view).hits += 1
        anchor = await session.stage(outcome.view)

    assert [write.key for write in store.writes] == [key]
    assert anchor_.parse(anchor) == anchor_.StoreAnchor(key=key)


async def test_a_re_render_keeps_every_custom_id_stable() -> None:
    # A click already in flight must stay routable across a re-render.
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes"), key=key)

    async with stored_session(key=key, store=store) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        first = await session.stage(outcome.view)
        typing.cast("Tally", outcome.view).hits += 1
        assert await session.stage(outcome.view) == first


async def test_a_handler_that_changed_state_without_rendering_still_commits() -> None:
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes"), key=key)

    async with stored_session(key=key, store=store) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        typing.cast("Tally", outcome.view).hits += 1
        assert await session.settle(outcome.view) is False

    assert store.writes
    assert serde.loads(await store.get(key) or b"", meta=TALLY) == Tally(label="Votes", hits=1)


async def test_a_dispatch_that_changed_nothing_costs_no_write() -> None:
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes"), key=key)

    async with stored_session(key=key, store=store, ttl=60.0) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        await session.settle(outcome.view)

    assert store.writes == []
    assert [touch.key for touch in store.touches] == [key]


async def test_a_dispatch_that_already_wrote_does_not_renew_the_expiry_twice() -> None:
    # Every write sets the entry's expiry, so the settle that follows a
    # re-render owes the store nothing -- which for a network-backed store is a
    # whole round trip inside the window Discord allows for a response.
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes"), key=key)

    async with stored_session(key=key, store=store, ttl=60.0) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        typing.cast("Tally", outcome.view).hits += 1
        await session.stage(outcome.view)
        await session.settle(outcome.view)

    assert [write.key for write in store.writes] == [key]
    assert store.touches == []


async def test_a_view_that_never_expires_is_not_even_touched() -> None:
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes"), key=key)

    async with stored_session(key=key, store=store) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        await session.settle(outcome.view)

    assert store.touches == []


async def test_losing_the_version_check_is_refused_rather_than_overwriting() -> None:
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes"), key=key)

    async with stored_session(key=key, store=store) as session:
        outcome = await session.restore()
        assert isinstance(outcome, backend.Restored)
        await store.put(key, serde.dumps(Tally(label="Votes", hits=100), meta=TALLY))

        typing.cast("Tally", outcome.view).hits += 1
        with pytest.raises(risa.StateConflictError):
            await session.settle(outcome.view)

    assert serde.loads(await store.get(key) or b"", meta=TALLY) == Tally(label="Votes", hits=100)


async def test_the_lock_is_released_however_the_dispatch_ends() -> None:
    key = anchor_.make_state_key()
    store = risa.MemoryStore()

    with pytest.raises(ZeroDivisionError):
        async with stored_session(key=key, store=store):
            raise ZeroDivisionError

    # Nothing is still holding it, so a second dispatch gets in without waiting.
    async with asyncio.timeout(1), stored_session(key=key, store=store):
        pass


# --- picking a store ---


async def test_a_session_reads_the_key_the_component_carried() -> None:
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes", hits=2), key=key)
    stored_backend = stored.StoredBackend({"default": store})

    async with stored_backend.session(
        meta=TALLY,
        component=codec.CustomID(
            raw_cookie=TALLY.cookie,
            handler=TOKEN,
            fragment=anchor_.StoreAnchor(key=key).encode(),
        ),
        interaction=interaction(""),
    ) as session:
        outcome = await session.restore()

    assert isinstance(outcome, backend.Restored)
    assert outcome.view == Tally(label="Votes", hits=2)


async def test_a_key_that_is_not_the_first_slice_of_an_anchor_is_not_believed() -> None:
    # A chunk of message-resident state is opaque text and can look exactly like
    # a store key by chance; only the first slice ever really holds one.
    key = anchor_.make_state_key()
    store = await store_holding(Tally(label="Votes"), key=key)
    stored_backend = stored.StoredBackend({"default": store})

    async with stored_backend.session(
        meta=TALLY,
        component=codec.CustomID(
            raw_cookie=TALLY.cookie,
            handler=TOKEN,
            fragment_index=2,
            fragment=anchor_.StoreAnchor(key=key).encode(),
        ),
        interaction=interaction(""),
    ) as session:
        assert await session.restore() is backend.Unreadably.OUTDATED


def test_a_view_naming_a_store_the_client_was_never_given_is_refused() -> None:
    with pytest.raises(risa.ViewDeclarationError):
        stored.StoredBackend({"sessions": risa.MemoryStore()}).session(
            meta=TALLY,
            component=codec.CustomID(raw_cookie=TALLY.cookie, handler=TOKEN),
            interaction=interaction(""),
        )
