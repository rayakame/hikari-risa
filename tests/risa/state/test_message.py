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
import importlib
import logging
import typing
import unittest.mock

import hikari
import msgspec
import pytest

import risa
from risa import codecs
from risa import ui
from risa.internal import codec
from risa.internal import registry
from risa.state import anchor
from risa.state import backend
from risa.state import message
from risa.state import schema as schema_

if typing.TYPE_CHECKING:
    import types


@risa.register(name="message-poll")
class Poll(risa.View):
    question: str = "Ship it?"
    votes: dict[str, int] = msgspec.field(default_factory=dict[str, int])

    def render(self) -> ui.Layout:
        return ui.TextDisplay(self.question)


@risa.register(name="message-ticket")
class Ticket(risa.View):
    owner: int

    def render(self) -> ui.Layout:
        return ui.TextDisplay(str(self.owner))


class PollBeforeVotes(msgspec.Struct):
    question: str = "Ship it?"


POLL = registry.require_meta(Poll)
TICKET = registry.require_meta(Ticket)


def encoded(index: int, fragment: str, *, meta: registry.ViewMeta = POLL) -> str:
    return codec.CustomID(
        cookie=meta.cookie,
        handler=codec.make_handler_token("vote", 1),
        fragment_index=index,
        fragment=fragment,
        tail="",
    ).encode()


def button(raw: str) -> hikari.ButtonComponent:
    return hikari.ButtonComponent(
        type=hikari.ComponentType.BUTTON,
        id=0,
        style=hikari.ButtonStyle.PRIMARY,
        label="vote",
        emoji=None,
        custom_id=raw,
        url=None,
        is_disabled=False,
    )


def row_of(ids: list[str]) -> list[hikari.TopLevelComponentTypesT]:
    return [hikari.ActionRowComponent(type=hikari.ComponentType.ACTION_ROW, id=0, components=[button(i) for i in ids])]


def carved(raw: bytes, *, meta: registry.ViewMeta = POLL, slots: int = 4) -> list[str]:
    pieces = anchor.carve(raw, [20] * slots, wire_codec=codecs.BASE85, view_name=meta.name)
    return [encoded(index, piece, meta=meta) for index, piece in enumerate(pieces)]


def state_of(view: Poll, *, seq: int = 0) -> bytes:
    schema = POLL.schema
    return anchor.MessageAnchor(schema.fingerprint, seq, schema.pack(schema.values(view))).pack()


def interaction(ids: list[str], *, message_id: int = 1) -> unittest.mock.Mock:
    sent = unittest.mock.Mock(spec=hikari.Message, id=hikari.Snowflake(message_id), components=row_of(ids))
    return unittest.mock.Mock(spec=hikari.ComponentInteraction, id=hikari.Snowflake(900), message=sent)


def session(
    owner: message.MessageBackend,
    ids: list[str],
    *,
    meta: registry.ViewMeta = POLL,
    message_id: int = 1,
) -> message.MessageSession:
    clicked = codec.CustomID(
        cookie=meta.cookie,
        handler=codec.make_handler_token("vote", 1),
        fragment_index=0,
        fragment="",
        tail="",
    )
    return owner.session(meta=meta, component=clicked, interaction=interaction(ids, message_id=message_id))


async def poll_from(opened: message.MessageSession) -> Poll:
    restored = await opened.restore()
    assert isinstance(restored, backend.Restored)
    assert isinstance(restored.view, Poll)
    return restored.view


async def vote(opened: message.MessageSession, option: str) -> bytes:
    poll = await poll_from(opened)
    poll.votes[option] = poll.votes.get(option, 0) + 1
    raw = await opened.stage(poll)
    await opened.published(poll)
    return raw


def records_at(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == level]


async def test_restore_hydrates_the_view_from_the_message() -> None:
    ids = carved(state_of(Poll(question="Lunch?", votes={"Pizza": 3, "Sushi": 1})))

    poll = await poll_from(session(message.MessageBackend(), ids))

    assert poll.question == "Lunch?"
    assert poll.votes == {"Pizza": 3, "Sushi": 1}


async def test_an_untouched_view_is_not_dirty() -> None:
    opened = session(message.MessageBackend(), carved(state_of(Poll(votes={"Red": 3}))))

    poll = await poll_from(opened)

    assert await opened.settle(poll) is False


async def test_a_mutated_view_is_dirty_until_it_is_published() -> None:
    opened = session(message.MessageBackend(), carved(state_of(Poll(votes={"Red": 3}))))
    poll = await poll_from(opened)

    poll.votes["Red"] += 1
    assert await opened.settle(poll) is True

    await opened.stage(poll)
    await opened.published(poll)
    assert await opened.settle(poll) is False


async def test_stage_writes_the_next_sequence_number() -> None:
    opened = session(message.MessageBackend(), carved(state_of(Poll(), seq=5)))
    poll = await poll_from(opened)

    staged = anchor.parse(await opened.stage(poll))

    assert isinstance(staged, anchor.MessageAnchor)
    assert staged.seq == 6
    assert staged.schema_fp == POLL.schema.fingerprint


async def test_an_untouched_click_on_an_older_prefix_is_not_dirty() -> None:
    older = schema_.StateSchema.of(PollBeforeVotes, view_name="poll")
    raw = anchor.MessageAnchor(older.fingerprint, 0, older.pack(["Lunch?"])).pack()
    opened = session(message.MessageBackend(), carved(raw))

    poll = await poll_from(opened)

    assert poll.question == "Lunch?"
    assert poll.votes == {}
    assert await opened.settle(poll) is False


async def test_two_clicks_on_the_same_stale_message_both_count() -> None:
    owner = message.MessageBackend()
    stale = carved(state_of(Poll(votes={"Red": 3})))

    await vote(session(owner, stale), "Red")
    poll = await poll_from(session(owner, stale))

    assert poll.votes == {"Red": 4}


async def test_the_cache_is_kept_per_view_in_a_shared_message() -> None:
    owner = message.MessageBackend()
    await vote(session(owner, carved(state_of(Poll(votes={"Red": 3})))), "Red")

    poll = await poll_from(session(owner, carved(state_of(Poll(question="Other?")))))
    ticket_ids = carved(
        anchor.MessageAnchor(TICKET.schema.fingerprint, 0, TICKET.schema.pack([7])).pack(),
        meta=TICKET,
    )
    restored = await session(owner, ticket_ids, meta=TICKET).restore()

    assert poll.votes == {"Red": 4}
    assert isinstance(restored, backend.Restored)
    assert isinstance(restored.view, Ticket)
    assert restored.view.owner == 7


async def test_a_message_as_new_as_the_cache_wins() -> None:
    owner = message.MessageBackend()
    await vote(session(owner, carved(state_of(Poll()))), "Red")

    elsewhere = carved(state_of(Poll(votes={"Blue": 1}), seq=1))
    poll = await poll_from(session(owner, elsewhere))

    assert poll.votes == {"Blue": 1}


async def test_a_message_newer_than_the_cache_wins() -> None:
    owner = message.MessageBackend()
    await vote(session(owner, carved(state_of(Poll()))), "Red")

    elsewhere = carved(state_of(Poll(votes={"Blue": 2}), seq=2))
    poll = await poll_from(session(owner, elsewhere))

    assert poll.votes == {"Blue": 2}


async def test_the_sequence_number_wraps_and_still_orders() -> None:
    owner = message.MessageBackend()
    stale = carved(state_of(Poll(), seq=anchor.MAX_SEQ))

    raw = await vote(session(owner, stale), "Red")
    staged = anchor.parse(raw)
    poll = await poll_from(session(owner, stale))

    assert isinstance(staged, anchor.MessageAnchor)
    assert staged.seq == 0
    assert poll.votes == {"Red": 1}


async def test_the_cache_forgets_the_least_recent_message_past_its_bound() -> None:
    owner = message.MessageBackend(max_cached=1)
    stale = carved(state_of(Poll()))
    await vote(session(owner, stale, message_id=1), "Red")
    await vote(session(owner, stale, message_id=2), "Red")

    poll = await poll_from(session(owner, stale, message_id=1))

    assert poll.votes == {}


async def test_clicks_on_one_message_queue_behind_each_other() -> None:
    owner = message.MessageBackend()
    ids = carved(state_of(Poll()))
    first = session(owner, ids)
    second = session(owner, ids)

    await first.open()
    waiting = asyncio.create_task(second.open())
    await asyncio.sleep(0.01)
    assert not waiting.done()

    await first.close()
    await waiting
    await second.close()


async def test_clicks_on_different_messages_do_not_wait() -> None:
    owner = message.MessageBackend(lock_timeout=0.01)
    ids = carved(state_of(Poll()))

    async with session(owner, ids, message_id=1), session(owner, ids, message_id=2):
        pass


async def test_a_lock_held_too_long_times_out_with_the_message_id() -> None:
    owner = message.MessageBackend(lock_timeout=0.01)
    ids = carved(state_of(Poll()))

    async with session(owner, ids):
        with pytest.raises(risa.LockTimeoutError) as exc_info:
            await session(owner, ids).open()

    assert exc_info.value.key == "1"


async def test_components_written_before_the_view_had_state_read_as_defaults() -> None:
    poll = await poll_from(session(message.MessageBackend(), [encoded(i, "") for i in range(3)]))

    assert poll == Poll()


async def test_missing_state_for_a_field_without_a_default_is_a_schema_edit(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")

    restored = await session(
        message.MessageBackend(), [encoded(i, "", meta=TICKET) for i in range(2)], meta=TICKET
    ).restore()

    assert restored is backend.Unreadably.OUTDATED
    assert any("version=1" in line for line in records_at(caplog, logging.ERROR))


async def test_empty_fragments_with_a_gap_read_as_torn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")

    restored = await session(message.MessageBackend(), [encoded(0, ""), encoded(2, "")]).restore()

    assert restored is backend.Unreadably.OUTDATED
    assert any("could not be reassembled" in line for line in records_at(caplog, logging.WARNING))


async def test_a_message_missing_its_first_component_reads_as_torn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    ids = carved(state_of(Poll(votes={"Red": 3, "Blue": 1})))

    restored = await session(message.MessageBackend(), ids[1:]).restore()

    assert restored is backend.Unreadably.OUTDATED
    assert any("could not be reassembled" in line for line in records_at(caplog, logging.WARNING))


async def test_a_message_missing_its_trailing_components_reads_as_torn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    ids = carved(state_of(Poll(votes={"Red": 3, "Blue": 1})))

    restored = await session(message.MessageBackend(), ids[:1]).restore()

    assert restored is backend.Unreadably.OUTDATED
    assert any("could not be reassembled" in line for line in records_at(caplog, logging.WARNING))


async def test_a_store_key_in_a_message_view_is_a_placement_switch(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")

    restored = await session(message.MessageBackend(), carved(anchor.StoreAnchor.mint().pack())).restore()

    assert restored is backend.Unreadably.OUTDATED
    assert any("placement was switched" in line for line in records_at(caplog, logging.ERROR))


async def test_an_unknown_schema_fingerprint_is_a_schema_edit(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    assert not POLL.schema.accepts("zzz")

    restored = await session(message.MessageBackend(), carved(anchor.MessageAnchor("zzz", 0, b"\x90").pack())).restore()

    assert restored is backend.Unreadably.OUTDATED
    assert any("no longer accepts" in line for line in records_at(caplog, logging.ERROR))


async def test_a_payload_that_does_not_decode_reads_as_forged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    raw = anchor.MessageAnchor(POLL.schema.fingerprint, 0, b"\xc1").pack()

    restored = await session(message.MessageBackend(), carved(raw)).restore()

    assert restored is backend.Unreadably.OUTDATED
    assert any("does not decode" in line for line in records_at(caplog, logging.WARNING))


async def test_a_codec_this_host_cannot_read_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = state_of(Poll(votes={"Red": 3}))
    pieces = anchor.carve(raw, [20, 20], wire_codec=codecs.BASE32768, view_name=POLL.name)
    ids = [
        codec.CustomID(
            cookie=POLL.cookie,
            handler=codec.make_handler_token("vote", 1),
            fragment_index=index,
            fragment=piece,
            tail="",
            codec=codecs.BASE32768,
        ).encode()
        for index, piece in enumerate(pieces)
    ]
    real = importlib.import_module

    def missing(name: str, package: str | None = None) -> types.ModuleType:
        if name.startswith("radixly"):
            raise ImportError(name)
        return real(name, package)

    monkeypatch.setattr(importlib, "import_module", missing)

    with pytest.raises(risa.CodecUnavailableError):
        await session(message.MessageBackend(), ids).restore()


async def test_published_without_a_stage_is_a_bug() -> None:
    opened = session(message.MessageBackend(), carved(state_of(Poll())))
    poll = await poll_from(opened)

    with pytest.raises(RuntimeError, match="stage"):
        await opened.published(poll)


async def test_settle_without_a_restore_is_a_bug() -> None:
    opened = session(message.MessageBackend(), carved(state_of(Poll())))

    with pytest.raises(RuntimeError, match="restore"):
        await opened.settle(Poll())


def test_the_first_anchor_starts_the_sequence_at_zero() -> None:
    raw = message.MessageBackend().first_anchor(meta=POLL, view=Poll(votes={"Red": 1}))

    parsed = anchor.parse(raw)

    assert isinstance(parsed, anchor.MessageAnchor)
    assert parsed.seq == 0
    assert POLL.schema.unpack(parsed.schema_fp, parsed.payload) == ("Ship it?", {"Red": 1})
