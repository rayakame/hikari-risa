from __future__ import annotations

import abc
import collections.abc
import functools
import typing

import hikari
from hikari import impl

from risa import errors
from risa import view as view_
from risa.internal import codec

if typing.TYPE_CHECKING:
    from hikari import colors
    from hikari import emojis
    from hikari import files
    from hikari import snowflakes
    from hikari.api import special_endpoints

    from risa.internal import registry

    type HandlerT = (
        collections.abc.Callable[..., collections.abc.Awaitable[None]]
        | view_.BoundHandler
        | functools.partial[collections.abc.Awaitable[None]]
    )

__all__ = (
    "BuildContext",
    "Button",
    "ChannelSelect",
    "Component",
    "Container",
    "ContainerChild",
    "File",
    "Interactive",
    "Layout",
    "LinkButton",
    "MediaGallery",
    "MediaGalleryItem",
    "MentionableSelect",
    "PremiumButton",
    "Rendered",
    "RoleSelect",
    "Row",
    "RowChild",
    "Section",
    "SectionAccessory",
    "Select",
    "SelectOption",
    "Separator",
    "TextDisplay",
    "TextSelect",
    "Thumbnail",
    "TopLevelComponent",
    "UserSelect",
    "build",
)


def _or_undefined[T](value: T | None) -> T | hikari.UndefinedType:
    return hikari.UNDEFINED if value is None else value


def _resolve_handler(handler: HandlerT) -> view_.BoundHandler:
    if isinstance(handler, view_.BoundHandler):
        return handler
    if isinstance(handler, functools.partial):
        inner = handler.func
        if not isinstance(inner, view_.BoundHandlerMethod):
            raise errors.NotAHandlerError(type(inner).__name__)
        return inner.bind(*handler.args, **handler.keywords)
    if isinstance(handler, view_.BoundHandlerMethod):
        return handler.bind()
    raise errors.NotAHandlerError(type(handler).__name__)


class BuildContext:
    __slots__ = ("_index", "cookie", "tokens")

    cookie: str
    tokens: collections.abc.Set[str]

    def __init__(self, cookie: str, tokens: collections.abc.Set[str]) -> None:
        self.cookie = cookie
        self.tokens = tokens
        self._index = 0

    def next_index(self) -> int:
        index = self._index
        self._index += 1
        return index


class Component(abc.ABC):
    __slots__ = ()

    @abc.abstractmethod
    def build(self, ctx: BuildContext, path: str, /) -> special_endpoints.ComponentBuilder: ...

    @property
    def name(self) -> str:
        return type(self).__name__


class Interactive(Component):
    __slots__ = ()

    @property
    @abc.abstractmethod
    def bound(self) -> view_.BoundHandler: ...

    def _routing_id(self, ctx: BuildContext, path: str) -> str:
        if self.bound.token not in ctx.tokens:
            reason = f"handler {self.bound.handler_id!r} (version {self.bound.version}) is not on this view"
            raise errors.LayoutError(path, reason)
        return codec.CustomID(
            cookie=ctx.cookie,
            handler=self.bound.token,
            fragment_index=ctx.next_index(),
            fragment="",
            tail=self.bound.payload,
        ).encode()


class TextDisplay(Component):
    __slots__ = ("_content",)

    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def content(self) -> str:
        return self._content

    @typing.override
    def build(self, _ctx: BuildContext, _path: str) -> special_endpoints.TextDisplayComponentBuilder:
        return impl.TextDisplayComponentBuilder(content=self._content)


class Separator(Component):
    __slots__ = ("_divider", "_spacing")

    def __init__(
        self,
        *,
        divider: bool = True,
        spacing: hikari.SpacingType = hikari.SpacingType.SMALL,
    ) -> None:
        self._divider = divider
        self._spacing = spacing

    @property
    def divider(self) -> bool:
        return self._divider

    @property
    def spacing(self) -> hikari.SpacingType:
        return self._spacing

    @typing.override
    def build(self, _ctx: BuildContext, _path: str) -> special_endpoints.SeparatorComponentBuilder:
        return impl.SeparatorComponentBuilder(divider=self._divider, spacing=self._spacing)


class LinkButton(Component):
    __slots__ = ("_disabled", "_emoji", "_label", "_url")

    def __init__(
        self,
        url: str,
        *,
        label: str | None = None,
        emoji: snowflakes.Snowflakeish | emojis.Emoji | str | None = None,
        disabled: bool = False,
    ) -> None:
        self._url = url
        self._label = label
        self._emoji = emoji
        self._disabled = disabled

    @property
    def url(self) -> str:
        return self._url

    @property
    def label(self) -> str | None:
        return self._label

    @property
    def emoji(self) -> snowflakes.Snowflakeish | emojis.Emoji | str | None:
        return self._emoji

    @property
    def disabled(self) -> bool:
        return self._disabled

    @typing.override
    def build(self, _ctx: BuildContext, _path: str) -> special_endpoints.LinkButtonBuilder:
        return impl.LinkButtonBuilder(
            url=self._url,
            label=_or_undefined(self._label),
            emoji=_or_undefined(self._emoji),
            is_disabled=self._disabled,
        )


class PremiumButton(Component):
    __slots__ = ("_disabled", "_sku_id")

    def __init__(self, sku_id: snowflakes.Snowflakeish, *, disabled: bool = False) -> None:
        self._sku_id = sku_id
        self._disabled = disabled

    @property
    def sku_id(self) -> snowflakes.Snowflakeish:
        return self._sku_id

    @property
    def disabled(self) -> bool:
        return self._disabled

    @typing.override
    def build(self, _ctx: BuildContext, _path: str) -> special_endpoints.PremiumButtonBuilder:
        return impl.PremiumButtonBuilder(sku_id=int(self._sku_id), is_disabled=self._disabled)


class Thumbnail(Component):
    __slots__ = ("_description", "_media", "_spoiler")

    def __init__(
        self,
        media: files.Resourceish,
        *,
        description: str | None = None,
        spoiler: bool = False,
    ) -> None:
        self._media = media
        self._description = description
        self._spoiler = spoiler

    @property
    def media(self) -> files.Resourceish:
        return self._media

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def spoiler(self) -> bool:
        return self._spoiler

    @typing.override
    def build(self, _ctx: BuildContext, _path: str) -> special_endpoints.ThumbnailComponentBuilder:
        return impl.ThumbnailComponentBuilder(
            media=self._media,
            description=_or_undefined(self._description),
            spoiler=self._spoiler,
        )


class MediaGalleryItem:
    __slots__ = ("_description", "_media", "_spoiler")

    def __init__(
        self,
        media: files.Resourceish,
        *,
        description: str | None = None,
        spoiler: bool = False,
    ) -> None:
        self._media = media
        self._description = description
        self._spoiler = spoiler

    @property
    def media(self) -> files.Resourceish:
        return self._media

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def spoiler(self) -> bool:
        return self._spoiler

    def build(self) -> special_endpoints.MediaGalleryItemBuilder:
        return impl.MediaGalleryItemBuilder(
            media=self._media,
            description=_or_undefined(self._description),
            spoiler=self._spoiler,
        )


class MediaGallery(Component):
    __slots__ = ("_items",)

    def __init__(self, *items: MediaGalleryItem) -> None:
        self._items = items

    @property
    def items(self) -> collections.abc.Sequence[MediaGalleryItem]:
        return self._items

    @typing.override
    def build(self, _ctx: BuildContext, _path: str) -> special_endpoints.MediaGalleryComponentBuilder:
        return impl.MediaGalleryComponentBuilder(items=[item.build() for item in self._items])


class File(Component):
    __slots__ = ("_file", "_spoiler")

    def __init__(self, file: files.Resourceish, *, spoiler: bool = False) -> None:
        self._file = file
        self._spoiler = spoiler

    @property
    def file(self) -> files.Resourceish:
        return self._file

    @property
    def spoiler(self) -> bool:
        return self._spoiler

    @typing.override
    def build(self, _ctx: BuildContext, _path: str) -> special_endpoints.FileComponentBuilder:
        return impl.FileComponentBuilder(file=self._file, spoiler=self._spoiler)


class Row(Component):
    __slots__ = ("_components",)

    def __init__(self, *components: RowChild) -> None:
        self._components = components

    @property
    def components(self) -> collections.abc.Sequence[RowChild]:
        return self._components

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.MessageActionRowBuilder:
        children: list[special_endpoints.MessageActionRowBuilderComponentsT] = [
            child.build(ctx, f"{path} > {child.name}[{index}]") for index, child in enumerate(self._components)
        ]
        return impl.MessageActionRowBuilder(components=children)


class Section(Component):
    __slots__ = ("_accessory", "_text_displays")

    def __init__(self, *text_displays: TextDisplay, accessory: SectionAccessory) -> None:
        self._text_displays = text_displays
        self._accessory = accessory

    @property
    def text_displays(self) -> collections.abc.Sequence[TextDisplay]:
        return self._text_displays

    @property
    def accessory(self) -> SectionAccessory:
        return self._accessory

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.SectionComponentBuilder:
        children: list[special_endpoints.SectionBuilderComponentsT] = [
            text.build(ctx, f"{path} > {text.name}[{index}]") for index, text in enumerate(self._text_displays)
        ]
        return impl.SectionComponentBuilder(
            components=children,
            accessory=self._accessory.build(ctx, f"{path} > accessory"),
        )


class Container(Component):
    __slots__ = ("_accent_color", "_children", "_spoiler")

    def __init__(
        self,
        *children: ContainerChild,
        accent_color: colors.Colorish | None = None,
        spoiler: bool = False,
    ) -> None:
        self._children = children
        self._accent_color = accent_color
        self._spoiler = spoiler

    @property
    def children(self) -> collections.abc.Sequence[ContainerChild]:
        return self._children

    @property
    def accent_color(self) -> colors.Colorish | None:
        return self._accent_color

    @property
    def spoiler(self) -> bool:
        return self._spoiler

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.ContainerComponentBuilder:
        children: list[special_endpoints.ContainerBuilderComponentsT] = [
            child.build(ctx, f"{path} > {child.name}[{index}]") for index, child in enumerate(self._children)
        ]
        return impl.ContainerComponentBuilder(
            components=children,
            accent_color=hikari.UNDEFINED if self._accent_color is None else hikari.Color.of(self._accent_color),
            spoiler=self._spoiler,
        )


class Button(Interactive):
    __slots__ = ("_bound", "_disabled", "_emoji", "_label", "_style")

    def __init__(
        self,
        handler: HandlerT,
        *,
        label: str | None = None,
        emoji: snowflakes.Snowflakeish | emojis.Emoji | str | None = None,
        style: hikari.ButtonStyle = hikari.ButtonStyle.PRIMARY,
        disabled: bool = False,
    ) -> None:
        self._bound = _resolve_handler(handler)
        self._label = label
        self._emoji = emoji
        self._style = style
        self._disabled = disabled

    @property
    @typing.override
    def bound(self) -> view_.BoundHandler:
        return self._bound

    @property
    def label(self) -> str | None:
        return self._label

    @property
    def emoji(self) -> snowflakes.Snowflakeish | emojis.Emoji | str | None:
        return self._emoji

    @property
    def style(self) -> hikari.ButtonStyle:
        return self._style

    @property
    def disabled(self) -> bool:
        return self._disabled

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.InteractiveButtonBuilder:
        return impl.InteractiveButtonBuilder(
            style=self._style,
            custom_id=self._routing_id(ctx, path),
            label=_or_undefined(self._label),
            emoji=_or_undefined(self._emoji),
            is_disabled=self._disabled,
        )


class SelectOption:
    __slots__ = ("_default", "_description", "_emoji", "_label", "_value")

    def __init__(
        self,
        label: str,
        value: str | None = None,
        *,
        description: str | None = None,
        emoji: snowflakes.Snowflakeish | emojis.Emoji | str | None = None,
        default: bool = False,
    ) -> None:
        self._label = label
        self._value = value if value is not None else label
        self._description = description
        self._emoji = emoji
        self._default = default

    @property
    def label(self) -> str:
        return self._label

    @property
    def value(self) -> str:
        return self._value

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def emoji(self) -> snowflakes.Snowflakeish | emojis.Emoji | str | None:
        return self._emoji

    @property
    def default(self) -> bool:
        return self._default

    def build(self) -> special_endpoints.SelectOptionBuilder:
        return impl.SelectOptionBuilder(
            label=self._label,
            value=self._value,
            description=_or_undefined(self._description),
            emoji=_or_undefined(self._emoji),
            is_default=self._default,
        )


class Select(Interactive):
    __slots__ = ("_bound", "_disabled", "_max_values", "_min_values", "_placeholder")

    def __init__(
        self,
        handler: HandlerT,
        *,
        placeholder: str | None = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ) -> None:
        self._bound = _resolve_handler(handler)
        self._placeholder = placeholder
        self._min_values = min_values
        self._max_values = max_values
        self._disabled = disabled

    @property
    @typing.override
    def bound(self) -> view_.BoundHandler:
        return self._bound

    @property
    def placeholder(self) -> str | None:
        return self._placeholder

    @property
    def min_values(self) -> int:
        return self._min_values

    @property
    def max_values(self) -> int:
        return self._max_values

    @property
    def disabled(self) -> bool:
        return self._disabled


class TextSelect(Select):
    __slots__ = ("_options",)

    def __init__(
        self,
        handler: HandlerT,
        /,
        *options: SelectOption,
        placeholder: str | None = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            handler, placeholder=placeholder, min_values=min_values, max_values=max_values, disabled=disabled
        )
        self._options = options

    @property
    def options(self) -> collections.abc.Sequence[SelectOption]:
        return self._options

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.TextSelectMenuBuilder[typing.NoReturn]:
        return impl.TextSelectMenuBuilder(
            custom_id=self._routing_id(ctx, path),
            options=[option.build() for option in self._options],
            placeholder=_or_undefined(self._placeholder),
            min_values=self._min_values,
            max_values=self._max_values,
            is_disabled=self._disabled,
        )


class UserSelect(Select):
    __slots__ = ()

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.SelectMenuBuilder:
        return impl.SelectMenuBuilder(
            type=hikari.ComponentType.USER_SELECT_MENU,
            custom_id=self._routing_id(ctx, path),
            placeholder=_or_undefined(self._placeholder),
            min_values=self._min_values,
            max_values=self._max_values,
            is_disabled=self._disabled,
        )


class RoleSelect(Select):
    __slots__ = ()

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.SelectMenuBuilder:
        return impl.SelectMenuBuilder(
            type=hikari.ComponentType.ROLE_SELECT_MENU,
            custom_id=self._routing_id(ctx, path),
            placeholder=_or_undefined(self._placeholder),
            min_values=self._min_values,
            max_values=self._max_values,
            is_disabled=self._disabled,
        )


class MentionableSelect(Select):
    __slots__ = ()

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.SelectMenuBuilder:
        return impl.SelectMenuBuilder(
            type=hikari.ComponentType.MENTIONABLE_SELECT_MENU,
            custom_id=self._routing_id(ctx, path),
            placeholder=_or_undefined(self._placeholder),
            min_values=self._min_values,
            max_values=self._max_values,
            is_disabled=self._disabled,
        )


class ChannelSelect(Select):
    __slots__ = ("_channel_types",)

    def __init__(
        self,
        handler: HandlerT,
        *,
        channel_types: collections.abc.Sequence[hikari.ChannelType] = (),
        placeholder: str | None = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            handler, placeholder=placeholder, min_values=min_values, max_values=max_values, disabled=disabled
        )
        self._channel_types = tuple(channel_types)

    @property
    def channel_types(self) -> collections.abc.Sequence[hikari.ChannelType]:
        return self._channel_types

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.ChannelSelectMenuBuilder:
        return impl.ChannelSelectMenuBuilder(
            custom_id=self._routing_id(ctx, path),
            channel_types=list(self._channel_types),
            placeholder=_or_undefined(self._placeholder),
            min_values=self._min_values,
            max_values=self._max_values,
            is_disabled=self._disabled,
        )


type RowChild = (
    Button | ChannelSelect | LinkButton | MentionableSelect | PremiumButton | RoleSelect | TextSelect | UserSelect
)
type SectionAccessory = Button | LinkButton | PremiumButton | Thumbnail
type ContainerChild = File | MediaGallery | Row | Section | Separator | TextDisplay
type TopLevelComponent = Container | ContainerChild
type Layout = TopLevelComponent | collections.abc.Sequence[TopLevelComponent]


def build(layout: Layout, meta: registry.ViewMeta) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
    ctx = BuildContext(cookie=meta.key, tokens=meta.handlers.keys())
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

    async def send_to(self, channel: hikari.TextableChannel, **forbidden: typing.Never) -> hikari.Message:
        _reject_forbidden(forbidden)
        return await channel.send(components=self._components)

    async def respond_to(
        self,
        interaction: hikari.ComponentInteraction | hikari.ModalInteraction | hikari.CommandInteraction,
        *,
        ephemeral: bool = False,
        **forbidden: typing.Never,
    ) -> None:
        _reject_forbidden(forbidden)
        await interaction.create_initial_response(
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
