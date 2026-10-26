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

import msgspec

from risa import codecs

__all__ = (
    "DEFAULT_PLACEMENT",
    "DEFAULT_STORE",
    "InMessage",
    "InStore",
    "StatePlacement",
)

DEFAULT_STORE: typing.Final[str] = "default"

type StatePlacement = InMessage | InStore


class InMessage(msgspec.Struct, frozen=True):
    codec: codecs.Codec = codecs.BASE85


class InStore(msgspec.Struct, frozen=True):
    ttl: float | None
    store: str = DEFAULT_STORE

    @property
    def codec(self) -> codecs.Codec:
        return codecs.BASE85


DEFAULT_PLACEMENT: typing.Final[InMessage] = InMessage()
