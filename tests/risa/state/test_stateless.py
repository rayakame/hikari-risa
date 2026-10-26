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
import msgspec

import risa
from risa import ui
from risa.internal import codec
from risa.internal import registry
from risa.state import backend
from risa.state import stateless


@risa.register(name="stateless-help")
class HelpMenu(risa.View):
    topics: risa.Prop[list[str]] = msgspec.field(default_factory=list[str])

    def render(self) -> ui.Layout:
        return ui.TextDisplay(", ".join(self.topics))


HELP = registry.require_meta(HelpMenu)


def session() -> stateless.StatelessSession:
    clicked = codec.CustomID(
        cookie=HELP.cookie,
        handler=codec.make_handler_token("details", 1),
        fragment_index=0,
        fragment="",
        tail="",
    )
    return stateless.StatelessBackend().session(
        meta=HELP,
        component=clicked,
        interaction=unittest.mock.Mock(spec=hikari.ComponentInteraction),
    )


async def test_restore_builds_the_view_from_its_defaults() -> None:
    restored = await session().restore()

    assert isinstance(restored, backend.Restored)
    assert restored.view == HelpMenu()


async def test_a_stateless_view_writes_no_anchor() -> None:
    opened = session()
    view = (await opened.restore()).view

    assert await opened.stage(view) == b""
    assert stateless.StatelessBackend().first_anchor(meta=HELP, view=view) == b""


async def test_a_stateless_view_is_never_dirty() -> None:
    opened = session()
    view = (await opened.restore()).view
    assert isinstance(view, HelpMenu)

    view.topics.append("changed")

    assert await opened.settle(view) is False


async def test_clicks_on_a_stateless_view_never_wait_for_each_other() -> None:
    async with session(), session():
        pass
