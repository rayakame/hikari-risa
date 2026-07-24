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

from risa.internal import codec


def test_cookie_has_the_width_the_codec_slices() -> None:
    assert len(codec.make_cookie("poll", 1)) == codec.COOKIE_LENGTH


def test_cookie_is_deterministic() -> None:
    assert codec.make_cookie("poll", 1) == codec.make_cookie("poll", 1)


def test_version_bump_changes_the_cookie() -> None:
    assert codec.make_cookie("poll", 1) != codec.make_cookie("poll", 2)


def test_different_names_produce_different_cookies() -> None:
    assert codec.make_cookie("poll", 1) != codec.make_cookie("survey", 1)


def test_handler_token_has_the_width_the_codec_slices() -> None:
    assert len(codec.make_handler_token("vote", 1)) == codec.HANDLER_LENGTH


def test_handler_token_is_deterministic() -> None:
    assert codec.make_handler_token("vote", 1) == codec.make_handler_token("vote", 1)


def test_different_handler_ids_produce_different_tokens() -> None:
    assert codec.make_handler_token("vote", 1) != codec.make_handler_token("close", 1)


def test_handler_version_bump_changes_the_token() -> None:
    assert codec.make_handler_token("vote", 1) != codec.make_handler_token("vote", 2)
