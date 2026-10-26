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

import hikari
import msgspec
import pytest

import risa
from risa import codecs
from risa import ui
from risa.internal import codec
from risa.internal import reader
from risa.internal import registry
from risa.state import anchor
from risa.ui import build as build_


@risa.register(name="build-poll")
class Poll(risa.View):
    question: str = "Ship it?"
    votes: dict[str, int] = msgspec.field(default_factory=dict[str, int])

    @risa.handler
    async def vote(self, ctx: risa.ComponentContext, option: int) -> None: ...

    @risa.handler
    async def close(self, ctx: risa.ComponentContext) -> None: ...

    @risa.handler
    async def pick(self, ctx: risa.ComponentContext, label: str) -> None: ...


@risa.register(name="build-ledger", state=risa.InStore(ttl=60.0))
class Ledger(risa.View):
    entries: list[str] = msgspec.field(default_factory=list[str])

    @risa.handler
    async def add(self, ctx: risa.ComponentContext, text: str) -> None: ...


@risa.register(name="build-help")
class Help(risa.View):
    @risa.handler
    async def details(self, ctx: risa.ComponentContext) -> None: ...


POLL = registry.require_meta(Poll)
LEDGER = registry.require_meta(Ledger)
HELP = registry.require_meta(Help)


def poll_layout(poll: Poll, buttons: int = 3) -> ui.Layout:
    return ui.Container(
        ui.TextDisplay(poll.question),
        ui.Row(*(ui.Button(risa.bind(poll.vote, i), label=str(i)) for i in range(buttons))),
        ui.Section(ui.TextDisplay("done?"), accessory=ui.Button(poll.close, label="Close")),
    )


def custom_ids(builders: typing.Sequence[hikari.api.ComponentBuilder]) -> list[str]:
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

    for builder in builders:
        walk(builder.build()[0])
    return found


def parsed(raw: str) -> codec.CustomID:
    found = codec.parse_custom_id(raw)
    assert found is not None
    return found


def as_message(ids: list[str]) -> list[hikari.TopLevelComponentTypesT]:
    return [
        hikari.ActionRowComponent(
            type=hikari.ComponentType.ACTION_ROW,
            id=0,
            components=[
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
            ],
        ),
    ]


def state_of(poll: Poll) -> bytes:
    return anchor.MessageAnchor(POLL.schema.fingerprint, 0, POLL.schema.pack_view(poll)).pack()


def test_each_leaf_carries_as_much_state_as_its_own_arguments_leave_room_for() -> None:
    poll = Poll(votes={f"option {i}": i for i in range(12)})

    ids = custom_ids(build_.plan(poll_layout(poll), POLL).emit(state_of(poll)))

    fragments = [parsed(raw).fragment for raw in ids]
    assert all(len(raw) <= 100 for raw in ids)
    assert len(fragments[0]) + len(parsed(ids[0]).tail) > codec.MAX_FRAGMENT_LENGTH - 5
    assert all(
        len(f) + len(parsed(raw).tail) <= codec.MAX_FRAGMENT_LENGTH for f, raw in zip(fragments, ids, strict=True)
    )


def test_a_tree_without_interactive_leaves_is_not_interactive() -> None:
    plan = build_.plan(ui.Container(ui.TextDisplay("Poll closed.")), POLL)

    assert not plan.interactive
    assert len(plan.emit(state_of(Poll(votes={"Red": 3})))) == 1


def test_a_message_view_carves_its_state_across_the_leaves_and_reads_back() -> None:
    poll = Poll(question="Lunch?", votes={"Pizza": 3, "Sushi": 1, "Thai": 7})
    raw = state_of(poll)

    ids = custom_ids(build_.plan(poll_layout(poll), POLL).emit(raw))

    assert [parsed(i).fragment_index for i in ids] == [0, 1, 2, 3]
    found = reader.fragments(as_message(ids), cookie=POLL.cookie)
    assert isinstance(found, dict)
    assert anchor.gather(found) == raw


def test_a_stored_view_replicates_its_key_into_every_leaf_at_index_zero() -> None:
    key = anchor.StoreAnchor.mint().pack()
    ledger = Ledger()
    layout = ui.Row(ui.Button(risa.bind(ledger.add, "milk")), ui.Button(risa.bind(ledger.add, "eggs")))

    ids = custom_ids(build_.plan(layout, LEDGER).emit(key))

    assert {parsed(i).fragment_index for i in ids} == {0}
    assert {codecs.BASE85.decode(parsed(i).fragment) for i in ids} == {key}


def test_a_view_without_state_writes_empty_fragments_at_contiguous_indices() -> None:
    help_menu = Help()
    layout = ui.Row(*(ui.Button(help_menu.details, label=str(i)) for i in range(3)))

    ids = custom_ids(build_.plan(layout, HELP).emit(b""))

    assert [parsed(i).fragment_index for i in ids] == [0, 1, 2]
    assert {parsed(i).fragment for i in ids} == {""}


def test_leaves_past_the_addressable_range_park_on_the_last_index() -> None:
    help_menu = Help()
    layout = ui.Row(*(ui.Button(help_menu.details, label=str(i)) for i in range(codec.MAX_FRAGMENTS + 3)))

    ids = custom_ids(build_.plan(layout, HELP).emit(b""))

    indices = [parsed(i).fragment_index for i in ids]
    assert indices[: codec.MAX_FRAGMENTS] == list(range(codec.MAX_FRAGMENTS))
    assert indices[codec.MAX_FRAGMENTS :] == [codec.MAX_FRAGMENTS - 1] * 3


def test_state_too_large_for_the_tree_overflows_when_emitted() -> None:
    poll = Poll(votes={f"option {i}": i for i in range(40)})

    plan = build_.plan(ui.Row(ui.Button(poll.close)), POLL)

    with pytest.raises(risa.StateOverflowError):
        plan.emit(state_of(poll))


def test_a_leaf_too_full_for_the_store_key_fails_at_planning_time() -> None:
    ledger = Ledger()

    with pytest.raises(risa.CustomIdOverflowError):
        build_.plan(ui.Row(ui.Button(risa.bind(ledger.add, "x" * 60))), LEDGER)


def test_distribute_copies_the_last_slot_onto_the_parked_leaves() -> None:
    fragments = [f"f{i}" for i in range(codec.MAX_FRAGMENTS + 2)]

    slots = anchor.distribute(fragments)

    last = codec.MAX_FRAGMENTS - 1
    assert slots[last] == anchor.Slot(index=last, fragment=f"f{last}")
    assert slots[-2:] == [anchor.Slot(index=last, fragment=f"f{last}")] * 2


def parked_layout(poll: Poll) -> ui.Layout:
    leaves = [ui.Button(poll.close) for _ in range(codec.MAX_FRAGMENTS + 2)]
    leaves.append(ui.Button(risa.bind(poll.pick, "a long label that takes room")))
    return ui.Row(*leaves)


def test_the_last_slot_holds_only_what_every_parked_leaf_can_carry_a_copy_of() -> None:
    poll = Poll(question="x" * 6376)
    raw = state_of(poll)

    ids = custom_ids(build_.plan(parked_layout(poll), POLL).emit(raw))

    assert all(len(i) <= 100 for i in ids)
    last_slot = parsed(ids[codec.MAX_FRAGMENTS - 1]).fragment
    assert last_slot
    assert parsed(ids[-1]).fragment == last_slot
    found = reader.fragments(as_message(ids), cookie=POLL.cookie)
    assert isinstance(found, dict)
    assert anchor.gather(found) == raw


def test_state_that_would_crowd_out_a_parked_leaf_is_a_state_overflow() -> None:
    poll = Poll(question="x" * 6416)

    plan = build_.plan(parked_layout(poll), POLL)

    with pytest.raises(risa.StateOverflowError):
        plan.emit(state_of(poll))
