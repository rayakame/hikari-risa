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
    "Component",
    "Container",
    "ContainerChild",
    "Layout",
    "Row",
    "RowChild",
    "Section",
    "SectionAccessory",
    "TextDisplay",
    "TopLevelComponent",
)


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


class Button(Component):
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
        if isinstance(handler, view_.BoundHandler):
            self._bound = handler
        elif isinstance(handler, view_.ZeroArgHandler):  # type: ignore[reportUnnecessaryIsInstance]
            self._bound = handler.bind()
        else:
            raise errors.NotAHandlerError(getattr(handler, "__name__", repr(handler)))

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


type RowChild = Button
"""What may be laid out across a :class:`Row`."""

type SectionAccessory = Button
"""What may sit beside a :class:`Section`'s text."""

type ContainerChild = Row | Section | TextDisplay
"""What a :class:`Container` may group."""

type TopLevelComponent = Container | Row | Section | TextDisplay
"""What may sit at the top level of a message."""

type Layout = TopLevelComponent | collections.abc.Sequence[TopLevelComponent]
"""What a view's ``render`` returns: one top-level component, or several."""
