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

import unittest.mock

import hikari
import pytest

import risa


def component_interaction() -> hikari.ComponentInteraction:
    return unittest.mock.Mock(spec=hikari.ComponentInteraction)


def test_the_context_exposes_the_interaction_it_was_built_from() -> None:
    interaction = component_interaction()

    ctx = risa.ComponentContext(interaction)

    assert ctx.interaction is interaction


def test_the_interaction_is_read_only() -> None:
    ctx = risa.ComponentContext(component_interaction())

    with pytest.raises(AttributeError):
        ctx.interaction = component_interaction()  # type: ignore[reportAttributeAccessIssue]


def test_the_context_holds_no_attributes_beyond_its_slots() -> None:
    ctx = risa.ComponentContext(component_interaction())

    with pytest.raises(AttributeError):
        ctx.message = None  # type: ignore[reportAttributeAccessIssue]
