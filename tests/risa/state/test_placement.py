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

import pytest

import risa
from risa.state import placement


def test_a_view_that_says_nothing_keeps_its_state_in_the_message() -> None:
    assert isinstance(placement.DEFAULT_PLACEMENT, placement.InMessage)


def test_the_placements_are_the_public_surface() -> None:
    assert risa.InMessage is placement.InMessage
    assert risa.InStore is placement.InStore


def test_a_stored_view_must_say_what_its_entries_cost_in_time() -> None:
    with pytest.raises(TypeError):
        placement.InStore()  # type: ignore[call-arg]


def test_keeping_entries_forever_is_spelled_out_rather_than_omitted() -> None:
    assert placement.InStore(ttl=None).ttl is None


def test_a_stored_view_falls_back_to_the_default_store() -> None:
    assert placement.InStore(ttl=60).store == placement.DEFAULT_STORE


def test_a_named_store_is_carried_as_given() -> None:
    assert placement.InStore(ttl=60, store="redis").store == "redis"


def test_a_message_placement_has_no_lifetime_to_configure() -> None:
    assert not hasattr(placement.InMessage(), "ttl")


def test_a_placement_cannot_be_edited_after_a_view_declares_it() -> None:
    stored = placement.InStore(ttl=60)

    with pytest.raises(AttributeError):
        stored.ttl = 30  # type: ignore[misc]


def test_two_placements_of_the_same_shape_are_equal() -> None:
    assert placement.InMessage() == placement.InMessage()
    assert placement.InStore(ttl=60) == placement.InStore(ttl=60)
    assert placement.InStore(ttl=60) != placement.InStore(ttl=30)
