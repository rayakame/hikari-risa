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

import collections.abc
import typing

import hikari

from risa import binding as binding_
from risa import errors
from risa.internal import codec
from risa.ui.nodes import Component

if typing.TYPE_CHECKING:
    from hikari import snowflakes
    from hikari.api import special_endpoints

    from risa import view as view_
    from risa.internal import registry
    from risa.ui.nodes import Layout

__all__ = (
    "BuildContext",
    "Rendered",
    "build",
)


class BuildContext:
    __slots__ = ("_index", "cls", "cookie", "tokens")

    def __init__(self, cookie: str, tokens: collections.abc.Set[str], cls: type[view_.View]) -> None:
        self.cookie = cookie
        self.tokens = tokens
        self.cls = cls
        self._index = 0

    def next_index(self) -> int:
        index = self._index
        self._index += 1
        return index

    def routing_id(self, binding: binding_.Binding, path: str) -> str:
        owner = binding.owner
        if owner is not None and not issubclass(self.cls, owner):
            reason = (
                f"handler {binding.handler_id!r} (version {binding.version})"
                f" belongs to view {owner.__name__!r}, not this one"
            )
            raise errors.LayoutError(path, reason)
        if binding.token not in self.tokens:
            reason = f"handler {binding.handler_id!r} (version {binding.version}) is not on this view"
            raise errors.LayoutError(path, reason)
        custom_id = codec.CustomID(
            cookie=self.cookie,
            handler=binding.token,
            fragment_index=self.next_index(),
            fragment="",
            tail=binding.payload,
        )
        try:
            return custom_id.encode()
        except errors.CustomIdOverflowError as exc:
            culprit = f"{self.cls.__name__} at {path}, handler {binding.handler_id!r}"
            raise errors.CustomIdOverflowError(culprit, exc.length) from exc


def build(layout: Layout, meta: registry.ViewMeta) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
    ctx = BuildContext(cookie=meta.cookie, tokens=meta.handlers.keys(), cls=meta.cls)
    if isinstance(layout, Component):
        layout = (layout,)
    return [node.build(ctx, f"{node.name}[{i}]") for i, node in enumerate(layout)]


class Rendered(collections.abc.Mapping[str, typing.Any]):
    __slots__ = ("_components",)

    def __init__(self, components: collections.abc.Sequence[special_endpoints.ComponentBuilder]) -> None:
        self._components = components

    @property
    def components(self) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
        return self._components

    @typing.override
    def __getitem__(self, key: str) -> typing.Any:
        if key == "components":
            return self._components
        raise KeyError(key)

    @typing.override
    def __iter__(self) -> collections.abc.Iterator[str]:
        yield "components"

    @typing.override
    def __len__(self) -> int:
        return 1

    async def send_to(
        self,
        rest: hikari.api.RESTClient,
        channel: snowflakes.SnowflakeishOr[hikari.TextableChannel],
        **forbidden: typing.Never,
    ) -> hikari.Message:
        _reject_forbidden(forbidden)
        return await rest.create_message(channel, components=self._components)

    async def respond_to(
        self,
        rest: hikari.api.RESTClient,
        interaction: hikari.ComponentInteraction | hikari.ModalInteraction | hikari.CommandInteraction,
        *,
        ephemeral: bool = False,
        **forbidden: typing.Never,
    ) -> None:
        _reject_forbidden(forbidden)
        await rest.create_interaction_response(
            interaction.id,
            interaction.token,
            hikari.ResponseType.MESSAGE_CREATE,
            components=self._components,
            flags=hikari.MessageFlag.EPHEMERAL if ephemeral else hikari.UNDEFINED,
        )


def _reject_forbidden(forbidden: collections.abc.Mapping[str, object]) -> None:
    if forbidden:
        msg = (
            f"a V2 view renders the whole message; {', '.join(sorted(forbidden))} cannot ride"
            " alongside it - put text in a ui.TextDisplay inside the view"
        )
        raise TypeError(msg)
