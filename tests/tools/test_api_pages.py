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
"""Keeps the hand-written member list of the ``risa`` API page in step with the modules it documents."""

from __future__ import annotations

import pathlib
import re

from risa import binding
from risa import client
from risa import context
from risa import view

_PAGE = pathlib.Path(__file__).parents[2] / "docs" / "api" / "risa.md"


def test_the_risa_api_page_lists_every_name_of_the_modules_it_documents() -> None:
    block = _PAGE.read_text(encoding="utf-8").split("members:", 1)[1]
    listed = set(re.findall(r"^\s+- (\w+)\s*$", block, flags=re.MULTILINE))

    exported = {*binding.__all__, *client.__all__, *context.__all__, *view.__all__}

    assert listed == exported
