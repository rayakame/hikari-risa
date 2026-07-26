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
"""Reading risa's own components back off a message Discord delivered.

A view that carries its state in its components has to find those components
again, and Discord hands the whole message to every interaction it sends. This
walks that message and collects the fragments risa wrote, so the state can be
rejoined from them.

Only components risa recognises are collected, and only those belonging to the
view being dispatched: a message may carry another library's components, or a
second risa view, and neither may contribute a fragment to this view's state.
"""

from __future__ import annotations

import collections.abc
import typing

import hikari

from risa.internal import codec

__all__ = ("fragments_of",)


def _interactive(component: hikari.PartialComponent) -> collections.abc.Iterator[hikari.PartialComponent]:
    """Walk one component and everything it contains.

    Containers, rows and sections hold other components, and a section's
    accessory is reachable only through its own attribute -- the easiest leaf
    to miss, and one that carries a ``custom_id`` like any other.

    Yields
    ------
    hikari.PartialComponent
        The component itself, then everything nested inside it.
    """
    yield component

    children = getattr(component, "components", None)
    if isinstance(children, collections.abc.Sequence):
        for child in typing.cast("collections.abc.Sequence[hikari.PartialComponent]", children):
            yield from _interactive(child)

    accessory = getattr(component, "accessory", None)
    if isinstance(accessory, hikari.PartialComponent):
        yield from _interactive(accessory)


def fragments_of(
    components: collections.abc.Sequence[hikari.PartialComponent],
    *,
    cookie: str,
) -> list[tuple[int, str]]:
    """Collect the state fragments a message carries for one view.

    Parameters
    ----------
    components
        The message's components, as Discord delivered them.
    cookie
        The cookie of the view whose fragments to collect. Anything routing
        elsewhere is skipped rather than mixed in, so a second view on the same
        message cannot corrupt this one's state.

    Returns
    -------
    list[tuple[int, str]]
        ``(index, fragment)`` for every matching component, in the order the
        message lists them.
    """
    found: list[tuple[int, str]] = []
    for component in components:
        for leaf in _interactive(component):
            custom_id = getattr(leaf, "custom_id", None)
            if not isinstance(custom_id, str):
                continue
            decoded = codec.CustomID.parse(custom_id)
            if decoded is not None and decoded.raw_cookie == cookie:
                found.append((decoded.fragment_index, decoded.fragment))
    return found
