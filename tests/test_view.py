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

import pytest

import risa
from risa.internal import constants
from risa.internal import registry


@risa.register(name="view-poll")
class Poll(risa.View):
    question: str
    votes: int = 0


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


def test_a_version_is_part_of_what_a_view_is_looked_up_under() -> None:
    class Second(risa.View):
        pass

    risa.register(name="view-versioned", version=2)(Second)

    assert meta_of(Second).key != "view-versioned:1"
    assert registry.global_registry().get("view-versioned:1") is None


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
