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
"""Turning a rendered tree into the builders hikari sends.

One walk does everything: it descends the tree, gives every interactive leaf a
``custom_id`` encoding the view and handler it routes to, and emits the matching
hikari builder. Depth costs nothing afterwards, because dispatch never walks the
tree again -- it resolves the flat identity packed into that id.

Discord's own limits are not checked here; see ``DESIGN.md`` 5.2. What is checked
is the one thing Discord cannot see: a component routing to a handler that
belongs to some other view produces a perfectly well-formed id that quietly
matches nothing.
"""

from __future__ import annotations

import typing

import hikari

from risa import errors
from risa.internal import codec
from risa.ui import nodes

if typing.TYPE_CHECKING:
    import collections.abc

    from hikari.api import special_endpoints

    from risa import view as view_
    from risa.internal import registry

__all__ = ("build",)


def build(
    layout: nodes.Layout,
    *,
    meta: registry.ViewMeta,
    anchor: str = "",
) -> collections.abc.Sequence[hikari.api.ComponentBuilder]:
    """Turn a rendered tree into the builders a message is sent with.

    Parameters
    ----------
    layout
        What the view's ``render`` returned.
    meta
        The view the tree belongs to, whose cookie and handlers every id is
        encoded against.
    anchor
        The view's state anchor, carried by every id so that a click can find
        the state again. Empty for a view that holds no durable state.

    Returns
    -------
    collections.abc.Sequence[hikari.api.ComponentBuilder]
        The top-level builders, ready to pass to ``components=``.

    Raises
    ------
    LayoutError
        If a component routes to a handler the view does not have.
    CustomIdOverflowError
        If an encoded ``custom_id`` would exceed Discord's length limit.
    """
    roots = [layout] if isinstance(layout, nodes.Component) else list(layout)
    builder = _Builder(meta, anchor)
    return [builder.top_level(root, f"{type(root).__name__}[{index}]") for index, root in enumerate(roots)]


class _Builder:
    """Carries what every node needs while the tree is walked."""

    __slots__ = ("_anchor", "_meta")

    def __init__(self, meta: registry.ViewMeta, anchor: str) -> None:
        self._meta = meta
        self._anchor = anchor

    def top_level(self, node: nodes.TopLevelComponent, path: str) -> hikari.api.ComponentBuilder:
        if isinstance(node, nodes.Container):
            return self.container(node, path)
        return self.container_child(node, path)

    def container(self, node: nodes.Container, path: str) -> hikari.impl.ContainerComponentBuilder:
        children: list[special_endpoints.ContainerBuilderComponentsT] = [
            self.container_child(child, f"{path} > {type(child).__name__}[{index}]")
            for index, child in enumerate(node.children)
        ]
        accent = hikari.UNDEFINED if node.accent_color is None else hikari.Color.of(node.accent_color)
        return hikari.impl.ContainerComponentBuilder(
            accent_color=accent,
            spoiler=node.spoiler,
            components=children,
        )

    def container_child(self, node: nodes.ContainerChild, path: str) -> special_endpoints.ContainerBuilderComponentsT:
        if isinstance(node, nodes.Row):
            return self.row(node, path)
        if isinstance(node, nodes.Section):
            return self.section(node, path)
        if isinstance(node, nodes.TextDisplay):
            return _text_display(node)
        if isinstance(node, nodes.Separator):
            return _separator(node)
        if isinstance(node, nodes.MediaGallery):
            return _media_gallery(node)
        return _file(node)

    def row(self, node: nodes.Row, path: str) -> hikari.impl.MessageActionRowBuilder:
        children: list[special_endpoints.MessageActionRowBuilderComponentsT] = [
            self.row_child(child, f"{path} > {type(child).__name__}[{index}]")
            for index, child in enumerate(node.children)
        ]
        return hikari.impl.MessageActionRowBuilder(components=children)

    def row_child(self, node: nodes.RowChild, path: str) -> special_endpoints.MessageActionRowBuilderComponentsT:
        if isinstance(node, nodes.Button):
            return self.button(node, path)
        if isinstance(node, nodes.LinkButton):
            return _link_button(node)
        if isinstance(node, nodes.PremiumButton):
            return _premium_button(node)
        return self.select(node, path)

    def section(self, node: nodes.Section, path: str) -> hikari.impl.SectionComponentBuilder:
        children: list[special_endpoints.SectionBuilderComponentsT] = [_text_display(child) for child in node.children]
        accessory_path = f"{path} > accessory"
        accessory: special_endpoints.SectionBuilderAccessoriesT
        if isinstance(node.accessory, nodes.Button):
            accessory = self.button(node.accessory, accessory_path)
        elif isinstance(node.accessory, nodes.LinkButton):
            accessory = _link_button(node.accessory)
        elif isinstance(node.accessory, nodes.PremiumButton):
            accessory = _premium_button(node.accessory)
        else:
            accessory = _thumbnail(node.accessory)
        return hikari.impl.SectionComponentBuilder(accessory=accessory, components=children)

    def button(self, node: nodes.Button, path: str) -> hikari.impl.InteractiveButtonBuilder:
        return hikari.impl.InteractiveButtonBuilder(
            style=node.style,
            custom_id=self.custom_id(node.bound, path),
            label=hikari.UNDEFINED if node.label is None else node.label,
            emoji=hikari.UNDEFINED if node.emoji is None else node.emoji,
            is_disabled=node.disabled,
        )

    def select(
        self,
        node: nodes.ChannelSelect | nodes.MentionableSelect | nodes.RoleSelect | nodes.TextSelect | nodes.UserSelect,
        path: str,
    ) -> special_endpoints.SelectMenuBuilder:
        custom_id = self.custom_id(node.bound, path)
        placeholder = hikari.UNDEFINED if node.placeholder is None else node.placeholder

        if isinstance(node, nodes.TextSelect):
            return hikari.impl.TextSelectMenuBuilder(
                custom_id=custom_id,
                options=[_select_option(option) for option in node.options],
                placeholder=placeholder,
                min_values=node.min_values,
                max_values=node.max_values,
                is_disabled=node.disabled,
            )

        if isinstance(node, nodes.ChannelSelect):
            if node.channel_types is None:
                return hikari.impl.ChannelSelectMenuBuilder(
                    custom_id=custom_id,
                    placeholder=placeholder,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    is_disabled=node.disabled,
                )
            return hikari.impl.ChannelSelectMenuBuilder(
                custom_id=custom_id,
                channel_types=list(node.channel_types),
                placeholder=placeholder,
                min_values=node.min_values,
                max_values=node.max_values,
                is_disabled=node.disabled,
            )

        return hikari.impl.SelectMenuBuilder(
            type=node.component_type,
            custom_id=custom_id,
            placeholder=placeholder,
            min_values=node.min_values,
            max_values=node.max_values,
            is_disabled=node.disabled,
        )

    def custom_id(self, bound: view_.BoundHandler, path: str) -> str:
        if bound.token not in self._meta.handlers:
            reason = f"handler {bound.handler_id!r} (version {bound.version}) is not on view {self._meta.name!r}"
            raise errors.LayoutError(path, reason)

        return codec.CustomID(
            raw_cookie=self._meta.cookie,
            handler=bound.token,
            fragment=self._anchor,
            args=bound.payload,
        ).encode(view_name=self._meta.name)


def _text_display(node: nodes.TextDisplay) -> hikari.impl.TextDisplayComponentBuilder:
    return hikari.impl.TextDisplayComponentBuilder(content=node.content)


def _link_button(node: nodes.LinkButton) -> hikari.impl.LinkButtonBuilder:
    return hikari.impl.LinkButtonBuilder(
        url=node.url,
        label=hikari.UNDEFINED if node.label is None else node.label,
        emoji=hikari.UNDEFINED if node.emoji is None else node.emoji,
        is_disabled=node.disabled,
    )


def _premium_button(node: nodes.PremiumButton) -> hikari.impl.PremiumButtonBuilder:
    return hikari.impl.PremiumButtonBuilder(sku_id=int(node.sku_id))


def _select_option(option: nodes.SelectOption) -> hikari.impl.SelectOptionBuilder:
    return hikari.impl.SelectOptionBuilder(
        option.label,
        option.value,
        description=hikari.UNDEFINED if option.description is None else option.description,
        emoji=hikari.UNDEFINED if option.emoji is None else option.emoji,
        is_default=option.default,
    )


def _separator(node: nodes.Separator) -> hikari.impl.SeparatorComponentBuilder:
    return hikari.impl.SeparatorComponentBuilder(
        spacing=hikari.UNDEFINED if node.spacing is None else node.spacing,
        divider=hikari.UNDEFINED if node.divider is None else node.divider,
    )


def _media_gallery(node: nodes.MediaGallery) -> hikari.impl.MediaGalleryComponentBuilder:
    return hikari.impl.MediaGalleryComponentBuilder(
        items=[
            hikari.impl.MediaGalleryItemBuilder(
                media=item.media,
                description=hikari.UNDEFINED if item.description is None else item.description,
                spoiler=item.spoiler,
            )
            for item in node.items
        ],
    )


def _file(node: nodes.File) -> hikari.impl.FileComponentBuilder:
    return hikari.impl.FileComponentBuilder(file=node.file, spoiler=node.spoiler)


def _thumbnail(node: nodes.Thumbnail) -> hikari.impl.ThumbnailComponentBuilder:
    return hikari.impl.ThumbnailComponentBuilder(
        media=node.media,
        description=hikari.UNDEFINED if node.description is None else node.description,
        spoiler=node.spoiler,
    )
