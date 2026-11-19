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

from risa.internal import codec
from risa.state import backend


def test_a_clicks_origin_names_the_interaction_and_its_application() -> None:
    sent = unittest.mock.Mock(spec=hikari.Message, id=hikari.Snowflake(1))
    interaction = unittest.mock.Mock(
        spec=hikari.ComponentInteraction,
        id=hikari.Snowflake(900),
        application_id=hikari.Snowflake(55),
        message=sent,
    )
    clicked = codec.CustomID(
        cookie=codec.make_cookie("poll", 1),
        handler=codec.make_handler_token("vote", 1),
        fragment_index=0,
        fragment="",
        tail="",
    )

    origin = backend.Origin.from_click(interaction, clicked)

    assert origin.message is sent
    assert origin.component is clicked
    assert origin.asker == "interaction 900"
    assert origin.application_id == 55
