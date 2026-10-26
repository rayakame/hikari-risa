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

import hikari

from risa.internal import codec

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import errors

__all__ = ("fragments",)


def _leaves(
    components: collections.abc.Iterable[hikari.TopLevelComponentTypesT],
) -> collections.abc.Iterator[hikari.PartialComponent]:
    for component in components:
        if isinstance(component, hikari.ContainerComponent):
            yield from _leaves(component.components)
        elif isinstance(component, hikari.ActionRowComponent):
            yield from component.components
        elif isinstance(component, hikari.SectionComponent):
            yield component.accessory


def _custom_id(component: hikari.PartialComponent) -> str | None:
    if isinstance(component, hikari.ButtonComponent | hikari.SelectMenuComponent):
        return component.custom_id
    return None


def fragments(
    components: collections.abc.Sequence[hikari.TopLevelComponentTypesT],
    *,
    cookie: str,
) -> collections.abc.Mapping[int, bytes] | errors.CodecUnavailableError | None:
    found: dict[int, bytes] = {}
    for leaf in _leaves(components):
        raw = _custom_id(leaf)
        if raw is None:
            continue
        parsed = codec.parse_custom_id(raw)
        if parsed is None or parsed.cookie != cookie:
            continue
        data = codec.decode_text(parsed.fragment, parsed.codec)
        if not isinstance(data, bytes):
            return data
        if found.setdefault(parsed.fragment_index, data) != data:
            return None
    return found
