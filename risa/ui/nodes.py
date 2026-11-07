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
"""The components a view's layout is built from, Discord's Components V2.

A layout is one top-level component or a sequence of them. Interactive components
take the handler a click routes to, bare as `self.save` or with wire arguments as
`risa.bind(self.vote, 2)`. risa checks only what Discord cannot: that each
`custom_id` fits, and that each handler belongs to the view. Discord's own limits,
such as five buttons to a row, are left to Discord.
"""

from __future__ import annotations

import abc
import collections.abc
import typing

import hikari
from hikari import impl

from risa import binding as binding_

if typing.TYPE_CHECKING:
    from hikari import colors
    from hikari import emojis
    from hikari import files
    from hikari import snowflakes
    from hikari.api import special_endpoints

    from risa.ui.build import BuildContext


__all__ = (
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
)


type RowChild = (
    Button | ChannelSelect | LinkButton | MentionableSelect | PremiumButton | RoleSelect | TextSelect | UserSelect
)
"""A component that fits in a `Row`."""

type SectionAccessory = Button | LinkButton | PremiumButton | Thumbnail
"""A component that fits beside the text of a `Section`."""

type ContainerChild = File | MediaGallery | Row | Section | Separator | TextDisplay
"""A component that fits in a `Container`."""

type TopLevelComponent = Container | ContainerChild
"""A component that can stand at the top of a message."""

type Layout = TopLevelComponent | collections.abc.Sequence[TopLevelComponent]
"""What `View.render` returns: one top-level component, or several in order."""


def _or_undefined[T](value: T | None) -> T | hikari.UndefinedType:
    return hikari.UNDEFINED if value is None else value


class Component(abc.ABC):
    """Base class of every component a layout can hold."""

    __slots__ = ()

    @abc.abstractmethod
    def build(self, ctx: BuildContext, path: str, /) -> special_endpoints.ComponentBuilder:
        """Turn the component into hikari's builder for it.

        risa calls this while building a view, once to measure the layout and once to emit
        it; `path` names the component's place in the tree for error messages.

        Parameters
        ----------
        ctx
            The build in progress, which hands out the `custom_id` of an interactive component.
        path
            Where the component stands in the tree, for example `Row[0] > Button[1]`.
        """

    @property
    def name(self) -> str:
        """The component's type name, used in tree paths."""
        return type(self).__name__


class Interactive(Component):
    """Base class of the components a click routes from to a handler.

    Parameters
    ----------
    handler
        The handler a click routes to, bare or through `risa.bind`.
    """

    __slots__ = ("_binding",)

    def __init__(self, handler: binding_.Handlerish) -> None:
        self._binding = binding_.resolve(handler)

    @property
    def binding(self) -> binding_.Binding:
        """The handler a click routes to, with its wire arguments."""
        return self._binding


class TextDisplay(Component):
    """Markdown text.

    Parameters
    ----------
    content
        The text, in Discord's markdown.
    """

    __slots__ = ("_content",)

    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def content(self) -> str:
        """The text, in Discord's markdown."""
        return self._content

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.TextDisplayComponentBuilder:
        return impl.TextDisplayComponentBuilder(content=self._content)


class Separator(Component):
    """Space between components, with or without a dividing line.

    Parameters
    ----------
    divider
        Draw a line.
    spacing
        How much space to leave.
    """

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
        """Whether a line is drawn."""
        return self._divider

    @property
    def spacing(self) -> hikari.SpacingType:
        """How much space is left."""
        return self._spacing

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.SeparatorComponentBuilder:
        return impl.SeparatorComponentBuilder(divider=self._divider, spacing=self._spacing)


class Thumbnail(Component):
    """A small image, as the accessory of a `Section`.

    Parameters
    ----------
    media
        The image: a URL, a file, or anything else hikari accepts as a resource.
    description
        Alternative text for the image.
    spoiler
        Blur the image until it is clicked.
    """

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
        """The image."""
        return self._media

    @property
    def description(self) -> str | None:
        """The alternative text, if any."""
        return self._description

    @property
    def spoiler(self) -> bool:
        """Whether the image is blurred until clicked."""
        return self._spoiler

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.ThumbnailComponentBuilder:
        return impl.ThumbnailComponentBuilder(
            media=self._media,
            description=_or_undefined(self._description),
            spoiler=self._spoiler,
        )


class MediaGalleryItem:
    """One image or video of a `MediaGallery`.

    Parameters
    ----------
    media
        The image or video: a URL, a file, or anything else hikari accepts as a resource.
    description
        Alternative text for it.
    spoiler
        Blur it until it is clicked.
    """

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
        """The image or video."""
        return self._media

    @property
    def description(self) -> str | None:
        """The alternative text, if any."""
        return self._description

    @property
    def spoiler(self) -> bool:
        """Whether it is blurred until clicked."""
        return self._spoiler

    def build(self) -> special_endpoints.MediaGalleryItemBuilder:
        """Return hikari's builder for the item."""
        return impl.MediaGalleryItemBuilder(
            media=self._media,
            description=_or_undefined(self._description),
            spoiler=self._spoiler,
        )


class MediaGallery(Component):
    """A grid of images and videos.

    Parameters
    ----------
    *items
        The images and videos, in order.
    """

    __slots__ = ("_items",)

    def __init__(self, *items: MediaGalleryItem) -> None:
        self._items = items

    @property
    def items(self) -> collections.abc.Sequence[MediaGalleryItem]:
        """The images and videos, in order."""
        return self._items

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.MediaGalleryComponentBuilder:
        return impl.MediaGalleryComponentBuilder(items=[item.build() for item in self._items])


class File(Component):
    """An attached file, shown as a download.

    Parameters
    ----------
    file
        The file: a path, bytes, or data named through `hikari.Bytes`. Discord's documentation allows only
        `attachment://` references for a file, so a URL will not do.
    spoiler
        Hide it behind a spoiler.
    """

    __slots__ = ("_file", "_spoiler")

    def __init__(self, file: files.Resourceish, *, spoiler: bool = False) -> None:
        self._file = file
        self._spoiler = spoiler

    @property
    def file(self) -> files.Resourceish:
        """The file."""
        return self._file

    @property
    def spoiler(self) -> bool:
        """Whether it is hidden behind a spoiler."""
        return self._spoiler

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.FileComponentBuilder:
        return impl.FileComponentBuilder(file=self._file, spoiler=self._spoiler)


class Row(Component):
    """A row of buttons, or a single select menu.

    Parameters
    ----------
    *components
        The buttons or the select menu, in order.
    """

    __slots__ = ("_components",)

    def __init__(self, *components: RowChild) -> None:
        self._components = components

    @property
    def components(self) -> collections.abc.Sequence[RowChild]:
        """The components in the row."""
        return self._components

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.MessageActionRowBuilder:
        children: list[special_endpoints.MessageActionRowBuilderComponentsT] = [
            child.build(ctx, f"{path} > {child.name}[{index}]") for index, child in enumerate(self._components)
        ]
        return impl.MessageActionRowBuilder(components=children)


class Section(Component):
    """Text with a button or a thumbnail beside it.

    Parameters
    ----------
    *text_displays
        The text, as one or more `TextDisplay`s.
    accessory
        The button or thumbnail beside the text.
    """

    __slots__ = ("_accessory", "_text_displays")

    def __init__(self, *text_displays: TextDisplay, accessory: SectionAccessory) -> None:
        self._text_displays = text_displays
        self._accessory = accessory

    @property
    def text_displays(self) -> collections.abc.Sequence[TextDisplay]:
        """The text displays."""
        return self._text_displays

    @property
    def accessory(self) -> SectionAccessory:
        """The button or thumbnail beside the text."""
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
    """A box around components, like an embed, with an optional accent color.

    Parameters
    ----------
    *children
        The components inside.
    accent_color
        The color of the stripe down the left side.
    spoiler
        Hide the contents behind a spoiler.
    """

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
        """The components inside."""
        return self._children

    @property
    def accent_color(self) -> colors.Colorish | None:
        """The color of the stripe down the left side, if any."""
        return self._accent_color

    @property
    def spoiler(self) -> bool:
        """Whether the contents are hidden behind a spoiler."""
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
    """A button whose clicks route to a handler.

    Parameters
    ----------
    handler
        The handler a click routes to, bare or through `risa.bind`.
    label
        The text on the button.
    emoji
        An emoji on the button.
    style
        The button's color.
    disabled
        Grey the button out, so it cannot be clicked.
    """

    __slots__ = ("_disabled", "_emoji", "_label", "_style")

    def __init__(
        self,
        handler: binding_.Handlerish,
        *,
        label: str | None = None,
        emoji: snowflakes.Snowflakeish | emojis.Emoji | str | None = None,
        style: hikari.ButtonStyle = hikari.ButtonStyle.PRIMARY,
        disabled: bool = False,
    ) -> None:
        super().__init__(handler)
        self._label = label
        self._emoji = emoji
        self._style = style
        self._disabled = disabled

    @property
    def label(self) -> str | None:
        """The text on the button, if any."""
        return self._label

    @property
    def emoji(self) -> snowflakes.Snowflakeish | emojis.Emoji | str | None:
        """The emoji on the button, if any."""
        return self._emoji

    @property
    def style(self) -> hikari.ButtonStyle:
        """The button's color."""
        return self._style

    @property
    def disabled(self) -> bool:
        """Whether the button is greyed out."""
        return self._disabled

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.InteractiveButtonBuilder:
        return impl.InteractiveButtonBuilder(
            style=self._style,
            custom_id=ctx.routing_id(self.binding, path),
            label=_or_undefined(self._label),
            emoji=_or_undefined(self._emoji),
            is_disabled=self._disabled,
        )


class LinkButton(Component):
    """A button that opens a URL. Clicks on it never reach the bot.

    Parameters
    ----------
    url
        The URL to open.
    label
        The text on the button.
    emoji
        An emoji on the button.
    disabled
        Grey the button out.
    """

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
        """The URL the button opens."""
        return self._url

    @property
    def label(self) -> str | None:
        """The text on the button, if any."""
        return self._label

    @property
    def emoji(self) -> snowflakes.Snowflakeish | emojis.Emoji | str | None:
        """The emoji on the button, if any."""
        return self._emoji

    @property
    def disabled(self) -> bool:
        """Whether the button is greyed out."""
        return self._disabled

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.LinkButtonBuilder:
        return impl.LinkButtonBuilder(
            url=self._url,
            label=_or_undefined(self._label),
            emoji=_or_undefined(self._emoji),
            is_disabled=self._disabled,
        )


class PremiumButton(Component):
    """A button that offers one of the app's SKUs for purchase.

    Parameters
    ----------
    sku_id
        The SKU to offer.
    disabled
        Grey the button out.
    """

    __slots__ = ("_disabled", "_sku_id")

    def __init__(self, sku_id: snowflakes.Snowflakeish, *, disabled: bool = False) -> None:
        self._sku_id = sku_id
        self._disabled = disabled

    @property
    def sku_id(self) -> snowflakes.Snowflakeish:
        """The SKU the button offers."""
        return self._sku_id

    @property
    def disabled(self) -> bool:
        """Whether the button is greyed out."""
        return self._disabled

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.PremiumButtonBuilder:
        return impl.PremiumButtonBuilder(sku_id=int(self._sku_id), is_disabled=self._disabled)


class SelectOption:
    """One option of a `TextSelect`.

    Parameters
    ----------
    label
        The text the user sees.
    value
        What the handler receives in `ctx.values` when it is picked; the label by default.
    description
        A line of text under the label.
    emoji
        An emoji beside the label.
    default
        Pick it in advance.
    """

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
        """The text the user sees."""
        return self._label

    @property
    def value(self) -> str:
        """What the handler receives when the option is picked."""
        return self._value

    @property
    def description(self) -> str | None:
        """The line under the label, if any."""
        return self._description

    @property
    def emoji(self) -> snowflakes.Snowflakeish | emojis.Emoji | str | None:
        """The emoji beside the label, if any."""
        return self._emoji

    @property
    def default(self) -> bool:
        """Whether the option is picked in advance."""
        return self._default

    def build(self) -> special_endpoints.SelectOptionBuilder:
        """Return hikari's builder for the option."""
        return impl.SelectOptionBuilder(
            label=self._label,
            value=self._value,
            description=_or_undefined(self._description),
            emoji=_or_undefined(self._emoji),
            is_default=self._default,
        )


class Select(Interactive):
    """Base class of the select menus. The handler reads the picks from `ctx.values`.

    Parameters
    ----------
    handler
        The handler a pick routes to, bare or through `risa.bind`.
    placeholder
        The text shown while nothing is picked.
    min_values
        The fewest picks the user must make.
    max_values
        The most picks the user may make.
    disabled
        Grey the menu out.
    """

    __slots__ = ("_disabled", "_max_values", "_min_values", "_placeholder")

    def __init__(
        self,
        handler: binding_.Handlerish,
        *,
        placeholder: str | None = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ) -> None:
        super().__init__(handler)
        self._placeholder = placeholder
        self._min_values = min_values
        self._max_values = max_values
        self._disabled = disabled

    @property
    def placeholder(self) -> str | None:
        """The text shown while nothing is picked, if any."""
        return self._placeholder

    @property
    def min_values(self) -> int:
        """The fewest options the user must pick."""
        return self._min_values

    @property
    def max_values(self) -> int:
        """The most options the user may pick."""
        return self._max_values

    @property
    def disabled(self) -> bool:
        """Whether the menu is greyed out."""
        return self._disabled


class TextSelect(Select):
    """A select menu of the options the bot lists, as `SelectOption`s.

    Parameters
    ----------
    handler
        The handler a pick routes to, bare or through `risa.bind`.
    *options
        The options, in order.
    placeholder
        The text shown while nothing is picked.
    min_values
        The fewest options the user must pick.
    max_values
        The most options the user may pick.
    disabled
        Grey the menu out.
    """

    __slots__ = ("_options",)

    def __init__(
        self,
        handler: binding_.Handlerish,
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
        """The options, in order."""
        return self._options

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.TextSelectMenuBuilder[typing.NoReturn]:
        return impl.TextSelectMenuBuilder(
            custom_id=ctx.routing_id(self.binding, path),
            options=[option.build() for option in self._options],
            placeholder=_or_undefined(self._placeholder),
            min_values=self._min_values,
            max_values=self._max_values,
            is_disabled=self._disabled,
        )


class _EntitySelect(Select):
    __slots__ = ()

    _MENU_TYPE: typing.ClassVar[hikari.ComponentType]

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.SelectMenuBuilder:
        return impl.SelectMenuBuilder(
            type=self._MENU_TYPE,
            custom_id=ctx.routing_id(self.binding, path),
            placeholder=_or_undefined(self._placeholder),
            min_values=self._min_values,
            max_values=self._max_values,
            is_disabled=self._disabled,
        )


class UserSelect(_EntitySelect):
    """A select menu of users. The handler finds them in `ctx.resolved`.

    It takes the parameters of `Select`.
    """

    __slots__ = ()

    _MENU_TYPE: typing.ClassVar[hikari.ComponentType] = hikari.ComponentType.USER_SELECT_MENU


class RoleSelect(_EntitySelect):
    """A select menu of roles. The handler finds them in `ctx.resolved`.

    It takes the parameters of `Select`.
    """

    __slots__ = ()

    _MENU_TYPE: typing.ClassVar[hikari.ComponentType] = hikari.ComponentType.ROLE_SELECT_MENU


class MentionableSelect(_EntitySelect):
    """A select menu of users and roles. The handler finds them in `ctx.resolved`.

    It takes the parameters of `Select`.
    """

    __slots__ = ()

    _MENU_TYPE: typing.ClassVar[hikari.ComponentType] = hikari.ComponentType.MENTIONABLE_SELECT_MENU


class ChannelSelect(Select):
    """A select menu of channels. The handler finds them in `ctx.resolved`.

    Parameters
    ----------
    handler
        The handler a pick routes to, bare or through `risa.bind`.
    channel_types
        The kinds of channel to offer; every kind by default.
    placeholder
        The text shown while nothing is picked.
    min_values
        The fewest channels the user must pick.
    max_values
        The most channels the user may pick.
    disabled
        Grey the menu out.
    """

    __slots__ = ("_channel_types",)

    def __init__(
        self,
        handler: binding_.Handlerish,
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
        """The kinds of channel offered; empty for every kind."""
        return self._channel_types

    @typing.override
    def build(self, ctx: BuildContext, path: str) -> special_endpoints.ChannelSelectMenuBuilder:
        return impl.ChannelSelectMenuBuilder(
            custom_id=ctx.routing_id(self.binding, path),
            channel_types=list(self._channel_types),
            placeholder=_or_undefined(self._placeholder),
            min_values=self._min_values,
            max_values=self._max_values,
            is_disabled=self._disabled,
        )
