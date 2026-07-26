# Copyright (c) 2025-present Rayakame
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
r"""Where a view's durable state lives.

Declared once per view, and nothing else about the view changes with it: the
same ``render``, the same handlers, the same ``rerender`` mean the same thing
under either placement.

``risa.InMessage()``
    The default. State is serialised into the spare characters of the
    ``custom_id``\\ s of the components the view renders, and read back off the
    message on every click. No infrastructure at all, restart-proof by
    construction, and the state lives exactly as long as the message does --
    but it is bounded by what the rendered components can carry.
``risa.InStore(store="redis", ttl=...)``
    State is one record in a :class:`~risa.state.store.Store` the developer
    chooses and operates; the message carries only a random key. Unbounded,
    expirable, and correct across processes when the store is distributed.

The rule for choosing, in one sentence: **bounded and world-readable takes the
default; unbounded, expirable, or multi-process REST takes a store.**

Each policy carries only the knobs that mean something for it. ``ttl`` exists
on ``InStore`` alone, because message-resident state has no lifetime of its
own to configure -- so the invalid combination cannot be written down rather
than being caught at runtime.
"""

from __future__ import annotations

import typing

import msgspec

__all__ = ("DEFAULT_PLACEMENT", "InMessage", "InStore", "StatePlacement")

DEFAULT_STORE: typing.Final[str] = "default"
"""Store a view is filed under when :class:`InStore` does not name one."""


class InMessage(msgspec.Struct, frozen=True):
    """Keep the view's state in the message that carries its components.

    Costs nothing to run and cannot lose state to an expiry or an eviction,
    because there is no store to expire from: the state travels with the
    message and is read back out of it on every click.

    The cost is capacity. What fits is a property of what the view renders --
    roughly ninety characters per interactive component, minus whatever that
    component's own bound arguments take -- so a view whose state outgrows its
    tree fails loudly at render with
    :class:`~risa.errors.StateOverflowError` rather than silently truncating.
    The remedies it names are marking bulky fields :data:`~risa.Prop` or moving
    the view to :class:`InStore`.

    Two further things worth knowing: the state is **world-readable**, since
    anyone can read a ``custom_id`` off the message, so it must never hold a
    secret; and across *processes* concurrent clicks resolve last-write-wins,
    which for a gateway bot never arises because every click on a message
    reaches the same process.
    """


class InStore(msgspec.Struct, frozen=True):
    """Keep the view's state in a store, and only its key in the message.

    Unbounded in size, expirable, and -- with a store every process can reach
    -- correct when clicks on one message land on different processes, which is
    what a REST bot behind a load balancer does.

    The cost is that the state can now go missing: an entry expires, is
    evicted, or the store is flushed, and the click that follows has nothing to
    rebuild from. That is what ``on_state_missing`` is for, and it exists only
    for views placed here.

    Attributes
    ----------
    store
        Which of the client's stores to use, by name. The client is given its
        stores at construction; naming one it does not have is an error when
        the view is added, not when a component is clicked.
    ttl
        Seconds an entry survives without being interacted with, refreshed on
        every interaction, so a view in use never expires and an abandoned one
        eventually does. Required, and deliberately so: a message is its own
        garbage collector, a store is not, and an entry with no expiry is an
        entry nothing will ever remove. Pass ``None`` to say explicitly that
        these entries are kept until something deletes them.
    """

    ttl: float | None
    store: str = DEFAULT_STORE


type StatePlacement = InMessage | InStore
"""Where a view's durable state lives."""

DEFAULT_PLACEMENT: typing.Final[InMessage] = InMessage()
"""What a view gets when it does not say: the message carries its own state."""
