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
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry


@risa.register(name="view-poll")
class Poll(risa.View):
    question: str
    votes: int = 0


@risa.register(name="view-machine")
class Machine(risa.View):
    count: int = 0

    @risa.handler
    async def press(self, _ctx: risa.ComponentContext) -> None:
        self.count += 1


@risa.register(name="view-submachine")
class SubMachine(Machine):
    @risa.handler
    async def reset(self, _ctx: risa.ComponentContext) -> None:
        self.count = 0


def component_context() -> risa.ComponentContext:
    return risa.ComponentContext(unittest.mock.Mock(spec=hikari.ComponentInteraction))


def meta_of(cls: type[risa.View]) -> registry.ViewMeta:
    return typing.cast("registry.ViewMeta", getattr(cls, constants.VIEW_META))


def test_a_registered_view_carries_what_it_was_declared_with() -> None:
    meta = meta_of(Poll)
    assert meta.cls is Poll
    assert meta.name == "view-poll"
    assert meta.version == 1


def test_a_view_is_still_an_ordinary_struct() -> None:
    poll = Poll(question="Ship it?")
    assert poll.votes == 0
    poll.votes += 1
    assert poll == Poll(question="Ship it?", votes=1)


def test_registering_files_the_view_process_wide() -> None:
    assert registry.global_registry().get(meta_of(Poll).key) is meta_of(Poll)


def test_a_registered_view_is_keyed_by_its_cookie() -> None:
    assert meta_of(Poll).key == codec.make_cookie("view-poll", 1)


def test_a_version_is_part_of_what_a_view_is_looked_up_under() -> None:
    class Second(risa.View):
        pass

    risa.register(name="view-versioned", version=2)(Second)

    assert meta_of(Second).key == codec.make_cookie("view-versioned", 2)
    assert registry.global_registry().get(codec.make_cookie("view-versioned", 1)) is None


def test_a_view_needs_a_name() -> None:
    class Blank(risa.View):
        pass

    with pytest.raises(risa.ViewDeclarationError):
        risa.register(name="   ")(Blank)


def test_a_version_below_one_is_refused() -> None:
    class Zeroth(risa.View):
        pass

    with pytest.raises(risa.ViewDeclarationError):
        risa.register(name="view-zeroth", version=0)(Zeroth)


def test_two_views_cannot_answer_to_one_name() -> None:
    class First(risa.View):
        pass

    class Second(risa.View):
        pass

    risa.register(name="view-taken")(First)
    with pytest.raises(risa.DuplicateViewError):
        risa.register(name="view-taken")(Second)


def test_registering_the_same_view_twice_is_not_a_collision() -> None:
    meta = meta_of(Poll)
    registry.global_registry().register(meta)
    assert registry.global_registry().get(meta.key) is meta


def test_class_access_yields_the_descriptor_itself() -> None:
    assert isinstance(Machine.press, risa.HandlerMethod)
    assert Machine.press is Machine.press


def test_instance_access_yields_a_bound_form() -> None:
    assert isinstance(Machine().press, risa.BoundHandlerMethod)


def test_the_descriptor_carries_what_it_was_declared_with() -> None:
    assert Machine.press.handler_id == "press"
    assert Machine.press.version == 1


def test_the_token_is_the_codec_token_for_id_and_version() -> None:
    assert Machine.press.token == codec.make_handler_token("press", 1)


def test_a_version_bump_retires_the_token() -> None:
    async def press(view: Machine, ctx: risa.ComponentContext) -> None:
        pass

    bumped = risa.HandlerMethod(press, handler_id="press", version=2)
    assert bumped.token != Machine.press.token


def test_a_pinned_id_keeps_the_token_across_a_rename() -> None:
    async def renamed(view: Machine, ctx: risa.ComponentContext) -> None:
        pass

    pinned = risa.HandlerMethod(renamed, handler_id="press", version=1)
    assert pinned.token == Machine.press.token


def test_bind_packages_identity_with_an_empty_payload() -> None:
    bound = Machine().press.bind()
    assert bound == risa.BoundHandler(handler_id="press", version=1, token=Machine.press.token)
    assert not bound.payload


async def test_the_bound_form_runs_the_handler_against_its_instance() -> None:
    machine = Machine()
    other = Machine()

    await machine.press(component_context())

    assert machine.count == 1
    assert other.count == 0


async def test_the_bound_form_can_be_called_repeatedly() -> None:
    machine = Machine()
    press = machine.press

    await press(component_context())
    await press(component_context())

    assert machine.count == 2


def test_the_bound_form_satisfies_the_zero_arg_protocol() -> None:
    assert isinstance(Machine().press, risa.ZeroArgHandler)


def test_the_descriptor_itself_does_not_satisfy_the_protocol() -> None:
    assert not isinstance(Machine.press, risa.ZeroArgHandler)


def test_the_bare_and_called_decorator_forms_are_equivalent() -> None:
    async def go(_view: Machine, _ctx: risa.ComponentContext) -> None:
        pass

    bare = risa.handler(go)
    called = risa.handler()(go)

    assert isinstance(bare, risa.HandlerMethod)
    assert bare.handler_id == called.handler_id == "go"
    assert bare.token == called.token


def test_the_decorator_pins_an_id_across_a_rename() -> None:
    async def renamed(_view: Machine, _ctx: risa.ComponentContext) -> None:
        pass

    pinned = risa.handler(handler_id="press")(renamed)

    assert pinned.token == Machine.press.token


def test_the_decorator_versions_the_token() -> None:
    async def press(_view: Machine, _ctx: risa.ComponentContext) -> None:
        pass

    bumped = risa.handler(version=2)(press)

    assert bumped.version == 2
    assert bumped.token != Machine.press.token


def test_registration_files_each_handler_under_its_token() -> None:
    record = meta_of(Machine).handlers[codec.make_handler_token("press", 1)]

    assert record.handler_id == "press"
    assert record.version == 1
    assert record.callback is Machine.press.func


def test_a_view_without_handlers_registers_an_empty_table() -> None:
    assert meta_of(Poll).handlers == {}


def test_inherited_handlers_are_collected() -> None:
    handlers = meta_of(SubMachine).handlers

    assert codec.make_handler_token("press", 1) in handlers
    assert codec.make_handler_token("reset", 1) in handlers


def test_the_same_id_at_different_versions_coexists() -> None:
    class Migrating(risa.View):
        @risa.handler(handler_id="act", version=1)
        async def act(self, _ctx: risa.ComponentContext) -> None:
            pass

        @risa.handler(handler_id="act", version=2)
        async def act_v2(self, _ctx: risa.ComponentContext) -> None:
            pass

    risa.register(name="view-migrating")(Migrating)

    handlers = meta_of(Migrating).handlers
    assert codec.make_handler_token("act", 1) in handlers
    assert codec.make_handler_token("act", 2) in handlers


def test_two_ids_hashing_to_the_same_token_collide_loudly() -> None:
    assert codec.make_handler_token("forced-13", 1) == codec.make_handler_token("forced-26", 1)

    class Colliding(risa.View):
        @risa.handler(handler_id="forced-13")
        async def first(self, _ctx: risa.ComponentContext) -> None:
            pass

        @risa.handler(handler_id="forced-26")
        async def second(self, _ctx: risa.ComponentContext) -> None:
            pass

    with pytest.raises(risa.DuplicateHandlerError) as exc_info:
        risa.register(name="view-colliding")(Colliding)

    assert {exc_info.value.first_id, exc_info.value.second_id} == {"forced-13", "forced-26"}


def test_declaring_the_same_identity_twice_raises() -> None:
    class Clashing(risa.View):
        @risa.handler(handler_id="act")
        async def one(self, _ctx: risa.ComponentContext) -> None:
            pass

        @risa.handler(handler_id="act")
        async def two(self, _ctx: risa.ComponentContext) -> None:
            pass

    with pytest.raises(risa.DuplicateHandlerError) as exc_info:
        risa.register(name="view-clashing")(Clashing)

    assert exc_info.value.view_name == "view-clashing"
    assert exc_info.value.first_id == "act"
    assert exc_info.value.second_id == "act"
    assert exc_info.value.first_version == exc_info.value.second_version == 1
