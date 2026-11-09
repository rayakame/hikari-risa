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
"""Where a view keeps its durable state: `InMessage` or `InStore`."""

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
"""The name of the client's `store=`, for `InStore(store=...)`."""

type StatePlacement = InMessage | InStore
"""Either placement a view can declare as its `state=`."""


class InMessage(msgspec.Struct, frozen=True):
    """Keep a view's durable state inside the message it renders.

    The default placement. The state is written into the spare characters of the
    rendered components' `custom_id`s and read back from the message on every click. It
    needs no infrastructure, survives restarts, and lives exactly as long as the message.
    It is bounded by what the components can carry: up to about 70 bytes per interactive
    component with the default codec.

    The state is readable by anyone who can see the message, so never keep secrets in
    durable fields. A click cannot change it: risa reads it from the message Discord
    delivers with the click.

    A rerender that changes the state changes the `custom_id` of the buttons that carry
    it. In tests Discord refused a click on a `custom_id` the message no longer has, so
    someone whose client still shows the old button gets a failed click and can click
    again. Discord does not document this and risa does not check it, so do not rely on
    the refusal. A view many people click at once fits `InStore` better: its `custom_id`s
    hold a key instead of the state, so they change only when a bound argument does.

    Attributes
    ----------
    codec
        How the state is written into `custom_id`s. `risa.codecs.BASE85` by default;
        `BASE32768` and `URO14` from the `[radixly]` extra carry more than twice as much.
    """

    codec: codecs.Codec = codecs.BASE85


class InStore(msgspec.Struct, frozen=True):
    """Keep a view's durable state in a store, under a key the message carries.

    The components carry only a random key, and the state is one record in the client's
    store of the name `store`. It has no size bound, every process of a bot shares it
    through a distributed store, and it expires.

    Attributes
    ----------
    ttl
        Seconds an untouched record lives. Every click restarts the clock, so only
        abandoned views expire. `None` keeps records forever, including the ones a deleted
        message or a failed send leaves behind. There is no default, since no lifetime
        suits both a confirmation dialog and a role menu.
    store
        Which of the client's stores holds the state: `"default"` for the client's
        `store=`, or a key of its `stores=`.
    adopt
        Take over the messages the view sent while it kept its state in the message. It
        needs the same `version`, and durable fields that can still read the old state. The
        first click on such a message moves its state into a record, which lives `ttl` like
        any other, and every later click on the message reads the record. Once the record
        has expired, the next click takes the state over again if the message still holds
        it. A message nobody clicks keeps its state. Without `adopt`, a click on a message
        nobody took over goes to `on_outdated`.
    """

    ttl: float | None
    store: str = DEFAULT_STORE
    adopt: bool = False

    @property
    def codec(self) -> codecs.Codec:
        """The codec the store key is written in, always base85."""
        return codecs.BASE85


DEFAULT_PLACEMENT: typing.Final[InMessage] = InMessage()
"""The placement of a view that declares none: `InMessage` with the default codec."""
