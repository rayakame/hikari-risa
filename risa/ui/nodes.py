from __future__ import annotations

import abc
import typing

import hikari
from hikari import impl

if typing.TYPE_CHECKING:
    import collections.abc

    from hikari import colors
    from hikari import emojis
    from hikari import files
    from hikari import snowflakes
    from hikari.api import special_endpoints

__all__ = (
    "Component",
    "Container",
    "ContainerChild",
    "File",
    "Interactive",
    "Layout",
    "LinkButton",
    "MediaGallery",
    "MediaGalleryItem",
    "PremiumButton",
    "Row",
    "RowChild",
    "Section",
    "SectionAccessory",
    "Separator",
    "TextDisplay",
    "Thumbnail",
    "TopLevelComponent",
    "build",
)


def _or_undefined[T](value: T | None) -> T | hikari.UndefinedType:
    return hikari.UNDEFINED if value is None else value


class Component(abc.ABC):
    __slots__ = ()

    @abc.abstractmethod
    def build(self) -> special_endpoints.ComponentBuilder: ...


class Interactive(Component):
    __slots__ = ()


class TextDisplay(Component):
    __slots__ = ("_content",)

    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def content(self) -> str:
        return self._content

    @typing.override
    def build(self) -> special_endpoints.TextDisplayComponentBuilder:
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
    def build(self) -> special_endpoints.SeparatorComponentBuilder:
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
    def build(self) -> special_endpoints.LinkButtonBuilder:
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
    def build(self) -> special_endpoints.PremiumButtonBuilder:
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
    def build(self) -> special_endpoints.ThumbnailComponentBuilder:
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
    def build(self) -> special_endpoints.MediaGalleryComponentBuilder:
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
    def build(self) -> special_endpoints.FileComponentBuilder:
        return impl.FileComponentBuilder(file=self._file, spoiler=self._spoiler)


class Row(Component):
    __slots__ = ("_components",)

    def __init__(self, *components: RowChild) -> None:
        self._components = components

    @property
    def components(self) -> collections.abc.Sequence[RowChild]:
        return self._components

    @typing.override
    def build(self) -> special_endpoints.MessageActionRowBuilder:
        children: list[special_endpoints.MessageActionRowBuilderComponentsT] = [
            child.build() for child in self._components
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
    def build(self) -> special_endpoints.SectionComponentBuilder:
        children: list[special_endpoints.SectionBuilderComponentsT] = [text.build() for text in self._text_displays]
        return impl.SectionComponentBuilder(components=children, accessory=self._accessory.build())


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
    def build(self) -> special_endpoints.ContainerComponentBuilder:
        children: list[special_endpoints.ContainerBuilderComponentsT] = [child.build() for child in self._children]
        return impl.ContainerComponentBuilder(
            components=children,
            accent_color=hikari.UNDEFINED if self._accent_color is None else hikari.Color.of(self._accent_color),
            spoiler=self._spoiler,
        )


type RowChild = LinkButton | PremiumButton
type SectionAccessory = LinkButton | PremiumButton | Thumbnail
type ContainerChild = File | MediaGallery | Row | Section | Separator | TextDisplay
type TopLevelComponent = Container | ContainerChild
type Layout = TopLevelComponent | collections.abc.Sequence[TopLevelComponent]


def build(layout: Layout) -> collections.abc.Sequence[special_endpoints.ComponentBuilder]:
    if isinstance(layout, Component):
        return [layout.build()]
    return [node.build() for node in layout]
