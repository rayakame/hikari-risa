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
"""The nodes a view's ``render`` builds its message out of.

Every node is a plain value object describing what should be shown; nothing here
talks to Discord. A separate build pass walks the tree, checks it against the
nesting rules and turns it into hikari builders.

The tree mirrors Components V2 one for one, and V1 falls out of it: a message
whose top level holds only :class:`Row` is exactly a V1 message. Only three
things in the whole tree are interactive -- the children of a :class:`Row`, a
:class:`Section` accessory that is a button, and text inputs in a modal -- so
however deep a tree nests, dispatch only ever sees that sparse set of leaves.
"""

from __future__ import annotations

import abc
import typing

import hikari

from risa import errors
from risa import view as view_

if typing.TYPE_CHECKING:
    import collections.abc

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
    "SelectOption",
    "Separator",
    "TextDisplay",
    "TextSelect",
    "Thumbnail",
    "TopLevelComponent",
    "UserSelect",
)


def _resolve_handler(handler: view_.ZeroArgHandler | view_.BoundHandler) -> view_.BoundHandler:
    """Normalise what a component was given into a bound handler identity.

    A bare handler is bound with no arguments -- valid exactly when its wire
    parameters are all defaulted or absent, which the type of ``handler``
    guarantees statically and ``bind()`` re-checks at runtime.

    Returns
    -------
    view_.BoundHandler
        The identity the component routes through.

    Raises
    ------
    NotAHandlerError
        If ``handler`` is neither a handler nor a bound one.
    """
    if isinstance(handler, view_.BoundHandler):
        return handler
    if isinstance(handler, view_.ZeroArgHandler):  # type: ignore[reportUnnecessaryIsInstance]
        return handler.bind()
    raise errors.NotAHandlerError(getattr(handler, "__name__", repr(handler)))


class Component(abc.ABC):
    """Base of every node in a component tree."""

    __slots__ = ()

    @property
    @abc.abstractmethod
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as.

        What the nesting rules are keyed on, so that they can be written as data
        rather than as a chain of type checks.
        """


class Interactive(Component):
    """A component whose clicks route back to a handler.

    What separates the leaves that carry routing information from the ones that
    are only there to be looked at, and therefore the set of components a view's
    state can be written into: a ``custom_id`` exists only on these, and the
    spare characters of those ids are the whole of what
    :class:`~risa.state.placement.InMessage` has to work with.
    """

    __slots__ = ()

    @property
    @abc.abstractmethod
    def bound(self) -> view_.BoundHandler:
        """The handler identity this component routes to, args included."""


class TextDisplay(Component):
    """Markdown shown in the message body.

    Where a view's text goes: a message carrying Components V2 may not have
    ``content``, so everything the user reads is a node like this one.

    Parameters
    ----------
    content
        The markdown to show.
    """

    __slots__ = ("_content",)

    def __init__(self, content: str) -> None:
        self._content = content

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.TEXT_DISPLAY

    @property
    def content(self) -> str:
        """The markdown to show."""
        return self._content


class Button(Interactive):
    """A button that routes back to a handler when pressed.

    Parameters
    ----------
    handler
        Where presses route to: a handler accessed on the view instance, or
        the result of ``bind()`` when the handler takes wire arguments. A
        handler with required wire arguments must be bound; passing it bare is
        rejected -- statically, and again eagerly at render time.
    label
        Text shown on the button. A button needs a label, an emoji, or both.
    emoji
        Emoji shown on the button.
    style
        How the button is drawn.
    disabled
        Whether the button is shown greyed out and cannot be pressed.

    Raises
    ------
    NotAHandlerError
        If ``handler`` is neither of those, and so has no durable identity to
        route under.
    ArgBindError
        If ``handler`` was passed bare but has required wire arguments.
    """

    __slots__ = ("_bound", "_disabled", "_emoji", "_label", "_style")

    def __init__(
        self,
        handler: view_.ZeroArgHandler | view_.BoundHandler,
        *,
        label: str | None = None,
        emoji: hikari.Snowflakeish | hikari.Emoji | str | None = None,
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
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.BUTTON

    @property
    @typing.override
    def bound(self) -> view_.BoundHandler:
        """The handler identity presses route to, args included."""
        return self._bound

    @property
    def label(self) -> str | None:
        """Text shown on the button."""
        return self._label

    @property
    def emoji(self) -> hikari.Snowflakeish | hikari.Emoji | str | None:
        """Emoji shown on the button."""
        return self._emoji

    @property
    def style(self) -> hikari.ButtonStyle:
        """How the button is drawn."""
        return self._style

    @property
    def disabled(self) -> bool:
        """Whether the button is shown greyed out and cannot be pressed."""
        return self._disabled


class LinkButton(Component):
    """A button that opens a URL instead of routing anywhere.

    Discord handles the press entirely client-side, so no interaction is ever
    dispatched and no handler is involved.

    Parameters
    ----------
    url
        The URL the button opens.
    label
        Text shown on the button. A button needs a label, an emoji, or both.
    emoji
        Emoji shown on the button.
    disabled
        Whether the button is shown greyed out and cannot be pressed.
    """

    __slots__ = ("_disabled", "_emoji", "_label", "_url")

    def __init__(
        self,
        url: str,
        *,
        label: str | None = None,
        emoji: hikari.Snowflakeish | hikari.Emoji | str | None = None,
        disabled: bool = False,
    ) -> None:
        self._url = url
        self._label = label
        self._emoji = emoji
        self._disabled = disabled

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.BUTTON

    @property
    def url(self) -> str:
        """The URL the button opens."""
        return self._url

    @property
    def label(self) -> str | None:
        """Text shown on the button."""
        return self._label

    @property
    def emoji(self) -> hikari.Snowflakeish | hikari.Emoji | str | None:
        """Emoji shown on the button."""
        return self._emoji

    @property
    def disabled(self) -> bool:
        """Whether the button is shown greyed out and cannot be pressed."""
        return self._disabled


class PremiumButton(Component):
    """A button offering one of the application's SKUs for purchase.

    Discord renders and handles it entirely itself -- label, price and the
    purchase flow -- so it takes no handler and dispatches nothing.

    Parameters
    ----------
    sku_id
        The SKU the button offers.
    """

    __slots__ = ("_sku_id",)

    def __init__(self, sku_id: hikari.Snowflakeish) -> None:
        self._sku_id = sku_id

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.BUTTON

    @property
    def sku_id(self) -> hikari.Snowflakeish:
        """The SKU the button offers."""
        return self._sku_id


class SelectOption:
    """One choice in a :class:`TextSelect`.

    Not a component of its own -- options exist only inside their menu.

    Parameters
    ----------
    label
        Text shown for the choice.
    value
        What arrives in ``ctx.values`` when the choice is picked. Defaults to
        the label.
    description
        Smaller text shown under the label.
    emoji
        Emoji shown beside the label.
    default
        Whether the choice starts out selected.
    """

    __slots__ = ("_default", "_description", "_emoji", "_label", "_value")

    def __init__(
        self,
        label: str,
        value: str | None = None,
        *,
        description: str | None = None,
        emoji: hikari.Snowflakeish | hikari.Emoji | str | None = None,
        default: bool = False,
    ) -> None:
        self._label = label
        self._value = value if value is not None else label
        self._description = description
        self._emoji = emoji
        self._default = default

    @property
    def label(self) -> str:
        """Text shown for the choice."""
        return self._label

    @property
    def value(self) -> str:
        """What arrives in ``ctx.values`` when the choice is picked."""
        return self._value

    @property
    def description(self) -> str | None:
        """Smaller text shown under the label."""
        return self._description

    @property
    def emoji(self) -> hikari.Snowflakeish | hikari.Emoji | str | None:
        """Emoji shown beside the label."""
        return self._emoji

    @property
    def default(self) -> bool:
        """Whether the choice starts out selected."""
        return self._default


class _Select(Interactive):
    """What every select menu shares: a handler and the selection bounds.

    What was selected is not carried in the ``custom_id`` -- Discord sends it
    on the interaction, and the handler reads it from ``ctx.values`` (or
    ``ctx.resolved`` for the entity menus).
    """

    __slots__ = ("_bound", "_disabled", "_max_values", "_min_values", "_placeholder")

    def __init__(
        self,
        handler: view_.ZeroArgHandler | view_.BoundHandler,
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
        """The handler identity selections route to, args included."""
        return self._bound

    @property
    def placeholder(self) -> str | None:
        """Text shown while nothing is selected."""
        return self._placeholder

    @property
    def min_values(self) -> int:
        """The fewest choices a selection may contain."""
        return self._min_values

    @property
    def max_values(self) -> int:
        """The most choices a selection may contain."""
        return self._max_values

    @property
    def disabled(self) -> bool:
        """Whether the menu is shown greyed out and cannot be opened."""
        return self._disabled


class TextSelect(_Select):
    """A menu of choices the view itself defines.

    The picked values arrive in ``ctx.values``.

    Parameters
    ----------
    handler
        Where selections route to: a handler accessed on the view instance,
        or the result of ``bind()`` when it takes wire arguments.
    *options
        The choices the menu offers.
    placeholder
        Text shown while nothing is selected.
    min_values
        The fewest choices a selection may contain.
    max_values
        The most choices a selection may contain.
    disabled
        Whether the menu is shown greyed out and cannot be opened.

    Raises
    ------
    NotAHandlerError
        If ``handler`` is neither a handler nor a bound one.
    ArgBindError
        If ``handler`` was passed bare but has required wire arguments.
    """

    __slots__ = ("_options",)

    def __init__(
        self,
        handler: view_.ZeroArgHandler | view_.BoundHandler,
        *options: SelectOption,
        placeholder: str | None = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            handler,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            disabled=disabled,
        )
        self._options = options

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.TEXT_SELECT_MENU

    @property
    def options(self) -> collections.abc.Sequence[SelectOption]:
        """The choices the menu offers."""
        return self._options


class UserSelect(_Select):
    """A menu of the guild's users, populated by Discord.

    The picked ids arrive in ``ctx.values``; the users behind them are in
    ``ctx.resolved``.
    """

    __slots__ = ()

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.USER_SELECT_MENU


class RoleSelect(_Select):
    """A menu of the guild's roles, populated by Discord.

    The picked ids arrive in ``ctx.values``; the roles behind them are in
    ``ctx.resolved``.
    """

    __slots__ = ()

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.ROLE_SELECT_MENU


class MentionableSelect(_Select):
    """A menu of the guild's users and roles together, populated by Discord.

    The picked ids arrive in ``ctx.values``; the entities behind them are in
    ``ctx.resolved``.
    """

    __slots__ = ()

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.MENTIONABLE_SELECT_MENU


class ChannelSelect(_Select):
    """A menu of the guild's channels, populated by Discord.

    The picked ids arrive in ``ctx.values``; the channels behind them are in
    ``ctx.resolved``.

    Parameters
    ----------
    handler
        Where selections route to: a handler accessed on the view instance,
        or the result of ``bind()`` when it takes wire arguments.
    channel_types
        The channel types offered, or ``None`` for all of them.
    placeholder
        Text shown while nothing is selected.
    min_values
        The fewest choices a selection may contain.
    max_values
        The most choices a selection may contain.
    disabled
        Whether the menu is shown greyed out and cannot be opened.
    """

    __slots__ = ("_channel_types",)

    def __init__(  # ruff:ignore[too-many-arguments]
        self,
        handler: view_.ZeroArgHandler | view_.BoundHandler,
        *,
        channel_types: collections.abc.Sequence[hikari.ChannelType] | None = None,
        placeholder: str | None = None,
        min_values: int = 1,
        max_values: int = 1,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            handler,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            disabled=disabled,
        )
        self._channel_types = None if channel_types is None else tuple(channel_types)

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.CHANNEL_SELECT_MENU

    @property
    def channel_types(self) -> collections.abc.Sequence[hikari.ChannelType] | None:
        """The channel types offered, or ``None`` for all of them."""
        return self._channel_types


class Row(Component):
    """A horizontal row of interactive components.

    The only place buttons and select menus may live, and the whole of a V1
    message's structure.

    Parameters
    ----------
    *children
        The components to lay out across the row.
    """

    __slots__ = ("_children",)

    def __init__(self, *children: RowChild) -> None:
        self._children = children

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.ACTION_ROW

    @property
    def children(self) -> collections.abc.Sequence[RowChild]:
        """The components laid out across the row."""
        return self._children


class Section(Component):
    """Text with a single component beside it.

    The one place other than a :class:`Row` where an interactive component may
    appear, which is easy to miss when walking a tree for interactive leaves.

    Parameters
    ----------
    *children
        The text shown in the section.
    accessory
        The component shown beside the text.
    """

    __slots__ = ("_accessory", "_children")

    def __init__(self, *children: TextDisplay, accessory: SectionAccessory) -> None:
        self._children = children
        self._accessory = accessory

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.SECTION

    @property
    def children(self) -> collections.abc.Sequence[TextDisplay]:
        """The text shown in the section."""
        return self._children

    @property
    def accessory(self) -> SectionAccessory:
        """The component shown beside the text."""
        return self._accessory


class Container(Component):
    """A visually grouped block of components.

    Drawn with a border, optionally coloured, around whatever it holds. A
    container may not hold another container.

    Parameters
    ----------
    *children
        The components to group.
    accent_color
        Colour of the container's border.
    spoiler
        Whether the container is blurred until clicked.
    """

    __slots__ = ("_accent_color", "_children", "_spoiler")

    def __init__(
        self,
        *children: ContainerChild,
        accent_color: hikari.Colorish | None = None,
        spoiler: bool = False,
    ) -> None:
        self._children = children
        self._accent_color = accent_color
        self._spoiler = spoiler

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.CONTAINER

    @property
    def children(self) -> collections.abc.Sequence[ContainerChild]:
        """The grouped components."""
        return self._children

    @property
    def accent_color(self) -> hikari.Colorish | None:
        """Colour of the container's border."""
        return self._accent_color

    @property
    def spoiler(self) -> bool:
        """Whether the container is blurred until clicked."""
        return self._spoiler


class Separator(Component):
    """Vertical spacing between components, optionally drawn as a divider.

    Parameters
    ----------
    divider
        Whether a horizontal line is drawn in the gap. ``None`` leaves the
        choice to Discord.
    spacing
        How large the gap is. ``None`` leaves the choice to Discord.
    """

    __slots__ = ("_divider", "_spacing")

    def __init__(self, *, divider: bool | None = None, spacing: hikari.SpacingType | None = None) -> None:
        self._divider = divider
        self._spacing = spacing

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.SEPARATOR

    @property
    def divider(self) -> bool | None:
        """Whether a horizontal line is drawn in the gap."""
        return self._divider

    @property
    def spacing(self) -> hikari.SpacingType | None:
        """How large the gap is."""
        return self._spacing


class MediaGalleryItem:
    """One image or video in a :class:`MediaGallery`.

    Not a component of its own -- items exist only inside their gallery.

    Parameters
    ----------
    media
        The image or video to show: a URL, an attachment, a file.
    description
        Alt text for the media.
    spoiler
        Whether the media is blurred until clicked.
    """

    __slots__ = ("_description", "_media", "_spoiler")

    def __init__(self, media: hikari.Resourceish, *, description: str | None = None, spoiler: bool = False) -> None:
        self._media = media
        self._description = description
        self._spoiler = spoiler

    @property
    def media(self) -> hikari.Resourceish:
        """The image or video to show."""
        return self._media

    @property
    def description(self) -> str | None:
        """Alt text for the media."""
        return self._description

    @property
    def spoiler(self) -> bool:
        """Whether the media is blurred until clicked."""
        return self._spoiler


class MediaGallery(Component):
    """A grid of images and videos.

    Parameters
    ----------
    *items
        The media to show.
    """

    __slots__ = ("_items",)

    def __init__(self, *items: MediaGalleryItem) -> None:
        self._items = items

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.MEDIA_GALLERY

    @property
    def items(self) -> collections.abc.Sequence[MediaGalleryItem]:
        """The media to show."""
        return self._items


class File(Component):
    """A file shown as a download card in the message body.

    Parameters
    ----------
    file
        The file to attach and show.
    spoiler
        Whether the file is hidden until clicked.
    """

    __slots__ = ("_file", "_spoiler")

    def __init__(self, file: hikari.Resourceish, *, spoiler: bool = False) -> None:
        self._file = file
        self._spoiler = spoiler

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.FILE

    @property
    def file(self) -> hikari.Resourceish:
        """The file to attach and show."""
        return self._file

    @property
    def spoiler(self) -> bool:
        """Whether the file is hidden until clicked."""
        return self._spoiler


class Thumbnail(Component):
    """A small image beside a :class:`Section`'s text.

    Only ever a section accessory; it may not appear anywhere else.

    Parameters
    ----------
    media
        The image to show: a URL, an attachment, a file.
    description
        Alt text for the image.
    spoiler
        Whether the image is blurred until clicked.
    """

    __slots__ = ("_description", "_media", "_spoiler")

    def __init__(self, media: hikari.Resourceish, *, description: str | None = None, spoiler: bool = False) -> None:
        self._media = media
        self._description = description
        self._spoiler = spoiler

    @property
    @typing.override
    def component_type(self) -> hikari.ComponentType:
        """The Discord component type this node renders as."""
        return hikari.ComponentType.THUMBNAIL

    @property
    def media(self) -> hikari.Resourceish:
        """The image to show."""
        return self._media

    @property
    def description(self) -> str | None:
        """Alt text for the image."""
        return self._description

    @property
    def spoiler(self) -> bool:
        """Whether the image is blurred until clicked."""
        return self._spoiler


type RowChild = (
    Button | ChannelSelect | LinkButton | MentionableSelect | PremiumButton | RoleSelect | TextSelect | UserSelect
)
"""What may be laid out across a :class:`Row`."""

type SectionAccessory = Button | LinkButton | PremiumButton | Thumbnail
"""What may sit beside a :class:`Section`'s text."""

type ContainerChild = File | MediaGallery | Row | Section | Separator | TextDisplay
"""What a :class:`Container` may group."""

type TopLevelComponent = Container | File | MediaGallery | Row | Section | Separator | TextDisplay
"""What may sit at the top level of a message."""

type Layout = TopLevelComponent | collections.abc.Sequence[TopLevelComponent]
"""What a view's ``render`` returns: one top-level component, or several."""
