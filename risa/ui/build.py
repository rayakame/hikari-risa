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
"""Building a layout into the components of a message."""

from __future__ import annotations

import collections.abc
import typing

import hikari

from risa import binding as binding_
from risa import codecs
from risa import errors
from risa import view as view_
from risa.internal import codec
from risa.state import anchor
from risa.state import placement
from risa.ui.nodes import Component

if typing.TYPE_CHECKING:
    from hikari import snowflakes
    from hikari.api import special_endpoints

    from risa.internal import registry
    from risa.ui.nodes import Layout

__all__ = (
    "BuildContext",
    "Plan",
    "Rendered",
    "plan",
    "render",
)


class _MessageOptions(typing.TypedDict, total=False):
    attachment: hikari.UndefinedOr[hikari.Resourceish]
    attachments: hikari.UndefinedOr[collections.abc.Sequence[hikari.Resourceish]]
    embed: hikari.UndefinedOr[hikari.Embed]
    embeds: hikari.UndefinedOr[collections.abc.Sequence[hikari.Embed]]
    poll: hikari.UndefinedOr[special_endpoints.PollBuilder]
    sticker: hikari.UndefinedOr[snowflakes.SnowflakeishOr[hikari.PartialSticker]]
    stickers: hikari.UndefinedOr[snowflakes.SnowflakeishSequence[hikari.PartialSticker]]
    tts: hikari.UndefinedOr[bool]
    nonce: hikari.UndefinedOr[str]
    reply: hikari.UndefinedOr[snowflakes.SnowflakeishOr[hikari.PartialMessage]]
    reply_must_exist: hikari.UndefinedOr[bool]
    mentions_everyone: hikari.UndefinedOr[bool]
    mentions_reply: hikari.UndefinedOr[bool]
    user_mentions: hikari.UndefinedOr[snowflakes.SnowflakeishSequence[hikari.PartialUser] | bool]
    role_mentions: hikari.UndefinedOr[snowflakes.SnowflakeishSequence[hikari.PartialRole] | bool]
    flags: hikari.UndefinedType | int | hikari.MessageFlag


class _ResponseOptions(typing.TypedDict, total=False):
    tts: hikari.UndefinedOr[bool]
    attachment: hikari.UndefinedNoneOr[hikari.Resourceish]
    attachments: hikari.UndefinedNoneOr[collections.abc.Sequence[hikari.Resourceish]]
    embed: hikari.UndefinedNoneOr[hikari.Embed]
    embeds: hikari.UndefinedNoneOr[collections.abc.Sequence[hikari.Embed]]
    poll: hikari.UndefinedOr[special_endpoints.PollBuilder]
    mentions_everyone: hikari.UndefinedOr[bool]
    user_mentions: hikari.UndefinedOr[snowflakes.SnowflakeishSequence[hikari.PartialUser] | bool]
    role_mentions: hikari.UndefinedOr[snowflakes.SnowflakeishSequence[hikari.PartialRole] | bool]


class BuildContext:
    """What a component gets while a layout is built: the view's routing identity and codec.

    risa makes one for each pass over a layout: one to measure it and one for each time it
    emits, so a component's `build` runs at least twice per `client.build`. `ui.render`
    builds a layout without a view in one pass, and its context's `routing_id` raises
    `LayoutError`.

    Attributes
    ----------
    cookie
        The cookie the view routes under.
    tokens
        The routing tokens of the view's handlers.
    cls
        The view class being built.
    codec
        The codec the view's state is written in.
    """

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
        self.cookie: str = cookie
        self.tokens: collections.abc.Set[str] = tokens
        self.cls: type[view_.View] = cls
        self.codec: codecs.Codec = wire_codec
        self._slots = slots
        self._reserve = reserve
        self._capacities: list[int] = []
        self._leaf = 0

    @property
    def capacities(self) -> collections.abc.Sequence[int]:
        """The spare characters of each interactive component built so far."""
        return self._capacities

    def routing_id(self, binding: binding_.Binding, path: str) -> str:
        """Return the `custom_id` for an interactive component bound to `binding`.

        Parameters
        ----------
        binding
            The handler the component routes to, with its wire arguments.
        path
            Where the component stands in the tree, for error messages.

        Returns
        -------
        :
            The `custom_id`, carrying the route, the wire arguments and the component's share
            of the state.

        Raises
        ------
        LayoutError
            If the handler belongs to another view, or the layout is built without a view.
        ArgBindError
            If a wire argument is too long to carry.
        CustomIdOverflowError
            If the `custom_id` would exceed Discord's limit.
        """
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


class _Viewless(BuildContext):
    """The context of a layout built without a view, on which nothing can route a click."""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__("", frozenset(), view_.View, codecs.BASE85)

    @typing.override
    def routing_id(self, binding: binding_.Binding, path: str) -> str:
        reason = (
            f"handler {binding.handler_id!r} (version {binding.version}) routes through its view,"
            " and ui.render builds a layout without one; return the layout from the view's render()"
            " and send the view with client.build"
        )
        raise errors.LayoutError(path, reason)


class Plan:
    """A layout measured for one view, ready to be built with that view's state."""

    __slots__ = ("_capacities", "_layout", "_meta")

    def __init__(self, layout: Layout, meta: registry.ViewMeta) -> None:
        self._layout = _top_level(layout)
        self._meta = meta
        measured = _context(meta, reserve=_reserve(meta))
        _build_all(self._layout, measured)
        self._capacities = measured.capacities

    @property
    def interactive(self) -> bool:
        """Whether the layout has anything to click."""
        return bool(self._capacities)

    def emit(self, state: bytes) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
        """Build the layout with `state` spread across its interactive components."""
        return _build_all(self._layout, _context(self._meta, slots=self._slots(state)))

    def _slots(self, state: bytes) -> collections.abc.Sequence[anchor.Slot]:
        if not state or not self._capacities:
            return anchor.distribute([""] * len(self._capacities))
        wire_codec = self._meta.state.codec
        if isinstance(self._meta.state, placement.InStore):
            return [anchor.Slot(index=0, fragment=wire_codec.encode(state))] * len(self._capacities)
        try:
            fragments = anchor.carve(state, _parked(self._capacities), wire_codec=wire_codec, view_name=self._meta.name)
        except errors.StateOverflowError as exc:
            message = anchor.MessageAnchor.parse(state)
            raise errors.StateOverflowError(
                exc.view_name,
                needed=exc.needed,
                available=exc.available,
                slots=exc.slots,
                codec_name=exc.codec_name,
                fields=self._meta.schema.field_sizes(message.payload) if message is not None else None,
            ) from None
        return anchor.distribute(fragments)


class Rendered(collections.abc.Mapping[str, typing.Any]):
    """The components of a message, built and ready to send.

    It is a mapping, so it spreads into any call that takes `components=`:
    `await ctx.respond(**rendered)`. A Components V2 message has no content and no
    embeds, so there is nothing else to spread. `Client.build` returns one for a view,
    and `ui.render` returns one for a layout with nothing to click. `send_to` and
    `respond_to` send it through hikari and take the other arguments of hikari's call.
    """

    __slots__ = ("_components",)

    def __init__(self, components: collections.abc.Sequence[special_endpoints.ComponentBuilder]) -> None:
        self._components = components

    @property
    def components(self) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
        """The built components."""
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
        content: hikari.UndefinedOr[str] = hikari.UNDEFINED,
        **options: typing.Unpack[_MessageOptions],
    ) -> hikari.Message:
        """Send the components as a new message in `channel`.

        ```python
        await rendered.send_to(bot.rest, channel_id, user_mentions=True)
        ```

        Parameters
        ----------
        rest
            The REST client to send through.
        channel
            The channel, or its id, to send to.
        content
            Text above the components, which only rows alone can carry.
        **options
            The other keyword arguments of hikari's
            [`create_message`][hikari.api.rest.RESTClient.create_message], such as
            `user_mentions`, `reply` and `flags`, passed on as they are.

        Returns
        -------
        :
            The message sent.

        Raises
        ------
        TypeError
            If `content`, `embed` or `embeds` is given beside Components V2, which take their
            place. Put the text in a `ui.TextDisplay`.
        """
        _reject_beside_v2(
            self._components,
            content=content,
            embed=options.get("embed", hikari.UNDEFINED),
            embeds=options.get("embeds", hikari.UNDEFINED),
        )
        return await rest.create_message(channel, content, components=self._components, **options)

    async def respond_to(
        self,
        rest: hikari.api.RESTClient,
        interaction: hikari.ComponentInteraction | hikari.ModalInteraction | hikari.CommandInteraction,
        content: hikari.UndefinedOr[str] = hikari.UNDEFINED,
        *,
        ephemeral: bool = False,
        flags: hikari.UndefinedType | int | hikari.MessageFlag = hikari.UNDEFINED,
        **options: typing.Unpack[_ResponseOptions],
    ) -> hikari.InteractionCallbackResponse:
        """Answer `interaction` with the components, as its initial response.

        For interactions risa does not dispatch, such as slash commands. In a risa handler,
        answer with `ctx.respond(**rendered)` instead, which knows whether the interaction
        has been answered already.

        Parameters
        ----------
        rest
            The REST client to answer through.
        interaction
            The interaction to answer, which must not have an initial response yet.
        content
            Text above the components, which only rows alone can carry.
        ephemeral
            Show the message only to the user who used the command.
        flags
            The flags of the message. `ephemeral=True` adds `EPHEMERAL` to them.
        **options
            The other keyword arguments of hikari's
            [`create_interaction_response`][hikari.api.rest.RESTClient.create_interaction_response],
            such as `user_mentions` and `tts`, passed on as they are.

        Returns
        -------
        :
            What hikari returns for the response.

        Raises
        ------
        TypeError
            If `content`, `embed` or `embeds` is given beside Components V2, which take their
            place. Put the text in a `ui.TextDisplay`.
        """
        _reject_beside_v2(
            self._components,
            content=content,
            embed=options.get("embed", hikari.UNDEFINED),
            embeds=options.get("embeds", hikari.UNDEFINED),
        )
        if ephemeral:
            flags = hikari.MessageFlag.EPHEMERAL if flags is hikari.UNDEFINED else flags | hikari.MessageFlag.EPHEMERAL
        return await rest.create_interaction_response(
            interaction.id,
            interaction.token,
            hikari.ResponseType.MESSAGE_CREATE,
            content,
            components=self._components,
            flags=flags,
            **options,
        )


def _reject_beside_v2(
    components: collections.abc.Sequence[special_endpoints.ComponentBuilder],
    **beside: object,
) -> None:
    given = [name for name, value in beside.items() if value is not hikari.UNDEFINED]
    if given and any(component.type != hikari.ComponentType.ACTION_ROW for component in components):
        msg = (
            f"Components V2 take the place of content and embeds; {', '.join(given)} cannot ride"
            " alongside them - put text in a ui.TextDisplay"
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


def _top_level(layout: Layout) -> collections.abc.Sequence[Component]:
    return (layout,) if isinstance(layout, Component) else layout


def _build_all(
    layout: collections.abc.Sequence[Component],
    ctx: BuildContext,
) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
    return [node.build(ctx, f"{node.name}[{i}]") for i, node in enumerate(layout)]


def plan(layout: Layout, meta: registry.ViewMeta) -> Plan:
    """Measure `layout` for the view described by `meta`."""
    return Plan(layout, meta)


def render(layout: Layout, /) -> Rendered:
    """Build the components of a message from a layout with nothing to click, ready to send.

    ```python
    await ctx.respond(
        **ui.render(ui.Container(ui.TextDisplay("Saved."))), ephemeral=True
    )
    ```

    Such a layout needs no view, so there is no class to register and no client to build it
    with. The result spreads into any hikari or lightbulb call that takes `components=`, as
    the result of `Client.build` does. A `LinkButton` or a `PremiumButton` may be part of the
    layout, since no handler answers them.

    Parameters
    ----------
    layout
        One top-level component, or several in order.

    Returns
    -------
    :
        The components, as a mapping that spreads into a send.

    Raises
    ------
    LayoutError
        If the layout holds a component that runs a handler, such as a `Button` or a select
        menu, even a disabled one. Such a component routes through its view: return the
        layout from the view's `render` and send the view with `Client.build`.
    TypeError
        If `layout` is a view, which `Client.build` builds.
    """
    if isinstance(layout, view_.View):
        msg = f"ui.render takes a layout, and got the view {type(layout).__name__}; send a view with client.build"
        raise TypeError(msg)
    return Rendered(_build_all(_top_level(layout), _Viewless()))
