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
from risa.state import anchor
from risa.state import placement
from risa.ui.nodes import Component

if typing.TYPE_CHECKING:
    from hikari import snowflakes
    from hikari.api import special_endpoints

    from risa import codecs
    from risa import view as view_
    from risa.internal import registry
    from risa.ui.nodes import Layout

__all__ = (
    "BuildContext",
    "Plan",
    "Rendered",
    "plan",
)


class BuildContext:
    __slots__ = ("_capacities", "_leaf", "_reserve", "_slots", "cls", "codec", "cookie", "tokens")

    def __init__(
        self,
        cookie: str,
        tokens: collections.abc.Set[str],
        cls: type[view_.View],
        wire_codec: codecs.Codec,
        *,
        slots: collections.abc.Sequence[anchor.Slot] | None = None,
        reserve: int = 0,
    ) -> None:
        self.cookie = cookie
        self.tokens = tokens
        self.cls = cls
        self.codec = wire_codec
        self._slots = slots
        self._reserve = reserve
        self._capacities: list[int] = []
        self._leaf = 0

    @property
    def capacities(self) -> collections.abc.Sequence[int]:
        return self._capacities

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
        tail = self._tail(binding)
        slot = self._next_slot(tail)
        custom_id = codec.CustomID(
            cookie=self.cookie,
            handler=binding.token,
            fragment_index=slot.index,
            fragment=slot.fragment,
            tail=tail,
            codec=self.codec,
        )
        try:
            return custom_id.encode()
        except errors.CustomIdOverflowError as exc:
            culprit = f"{self.cls.__name__} at {path}, handler {binding.handler_id!r}"
            raise errors.CustomIdOverflowError(culprit, exc.length) from exc

    def _tail(self, binding: binding_.Binding) -> str:
        if not binding.fingerprint:
            return ""
        parts: list[str] = []
        for frame in binding.frames:
            text = self.codec.encode(frame.data)
            if len(text) > codec.MAX_FRAME_LENGTH:
                reason = (
                    f"encodes to {len(text)} characters with codec {self.codec.name!r},"
                    f" which exceeds the {codec.MAX_FRAME_LENGTH}-character frame limit"
                )
                raise errors.ArgBindError(binding.func_name, frame.name, reason)
            parts.append(text)
        return binding.fingerprint + codec.pack_frames(parts)

    def _next_slot(self, tail: str) -> anchor.Slot:
        leaf = self._leaf
        self._leaf += 1
        if self._slots is not None:
            return self._slots[leaf]
        self._capacities.append(codec.MAX_FRAGMENT_LENGTH - len(tail))
        return anchor.Slot(index=min(leaf, codec.MAX_FRAGMENTS - 1), fragment="!" * self._reserve)


class Plan:
    __slots__ = ("_capacities", "_layout", "_meta")

    def __init__(self, layout: Layout, meta: registry.ViewMeta) -> None:
        self._layout: collections.abc.Sequence[Component] = (layout,) if isinstance(layout, Component) else layout
        self._meta = meta
        measured = _context(meta, reserve=_reserve(meta))
        _build_all(self._layout, measured)
        self._capacities = measured.capacities

    @property
    def interactive(self) -> bool:
        return bool(self._capacities)

    def emit(self, state: bytes) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
        return _build_all(self._layout, _context(self._meta, slots=self._slots(state)))

    def _slots(self, state: bytes) -> collections.abc.Sequence[anchor.Slot]:
        if not state or not self._capacities:
            return anchor.distribute([""] * len(self._capacities))
        wire_codec = self._meta.state.codec
        if isinstance(self._meta.state, placement.InStore):
            return [anchor.Slot(index=0, fragment=wire_codec.encode(state))] * len(self._capacities)
        fragments = anchor.carve(state, _parked(self._capacities), wire_codec=wire_codec, view_name=self._meta.name)
        return anchor.distribute(fragments)


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


def _parked(capacities: collections.abc.Sequence[int]) -> collections.abc.Sequence[int]:
    last = codec.MAX_FRAGMENTS - 1
    if len(capacities) <= codec.MAX_FRAGMENTS:
        return capacities
    return [*capacities[:last], min(capacities[last:]), *capacities[last + 1 :]]


def _reserve(meta: registry.ViewMeta) -> int:
    if not isinstance(meta.state, placement.InStore):
        return 0
    return len(meta.state.codec.encode(bytes(anchor.STORE_ANCHOR_LENGTH)))


def _context(
    meta: registry.ViewMeta,
    *,
    slots: collections.abc.Sequence[anchor.Slot] | None = None,
    reserve: int = 0,
) -> BuildContext:
    return BuildContext(
        meta.cookie,
        meta.handlers.keys(),
        meta.cls,
        meta.state.codec,
        slots=slots,
        reserve=reserve,
    )


def _build_all(
    layout: collections.abc.Sequence[Component],
    ctx: BuildContext,
) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
    return [node.build(ctx, f"{node.name}[{i}]") for i, node in enumerate(layout)]


def plan(layout: Layout, meta: registry.ViewMeta) -> Plan:
    return Plan(layout, meta)
