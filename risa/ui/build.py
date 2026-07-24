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

    from risa.internal import registry

__all__ = ("build",)


def build(
    layout: nodes.Layout,
    *,
    meta: registry.ViewMeta,
    state_key: str | None = None,
) -> collections.abc.Sequence[hikari.api.ComponentBuilder]:
    """Turn a rendered tree into the builders a message is sent with.

    Parameters
    ----------
    layout
        What the view's ``render`` returned.
    meta
        The view the tree belongs to, whose cookie and handlers every id is
        encoded against.
    state_key
        Key the view's state is stored under, carried by every id so that a
        click can find it again. Omitted for a view that holds no state.

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
    builder = _Builder(meta, state_key)
    return [builder.top_level(root, f"{type(root).__name__}[{index}]") for index, root in enumerate(roots)]


class _Builder:
    """Carries what every node needs while the tree is walked."""

    __slots__ = ("_meta", "_state_key")

    def __init__(self, meta: registry.ViewMeta, state_key: str | None) -> None:
        self._meta = meta
        self._state_key = state_key

    def top_level(self, node: nodes.TopLevelComponent, path: str) -> hikari.api.ComponentBuilder:
        if isinstance(node, nodes.Container):
            return self.container(node, path)
        if isinstance(node, nodes.Row):
            return self.row(node, path)
        if isinstance(node, nodes.Section):
            return self.section(node, path)
        return self.text_display(node)

    def container(self, node: nodes.Container, path: str) -> hikari.impl.ContainerComponentBuilder:
        children: list[special_endpoints.ContainerBuilderComponentsT] = []
        for index, child in enumerate(node.children):
            child_path = f"{path} > {type(child).__name__}[{index}]"
            if isinstance(child, nodes.Row):
                children.append(self.row(child, child_path))
            elif isinstance(child, nodes.Section):
                children.append(self.section(child, child_path))
            else:
                children.append(self.text_display(child))

        accent = hikari.UNDEFINED if node.accent_color is None else hikari.Color.of(node.accent_color)
        return hikari.impl.ContainerComponentBuilder(
            accent_color=accent,
            spoiler=node.spoiler,
            components=children,
        )

    def row(self, node: nodes.Row, path: str) -> hikari.impl.MessageActionRowBuilder:
        children: list[special_endpoints.MessageActionRowBuilderComponentsT] = [
            self.button(child, f"{path} > {type(child).__name__}[{index}]") for index, child in enumerate(node.children)
        ]
        return hikari.impl.MessageActionRowBuilder(components=children)

    def section(self, node: nodes.Section, path: str) -> hikari.impl.SectionComponentBuilder:
        children: list[special_endpoints.SectionBuilderComponentsT] = [
            self.text_display(child) for child in node.children
        ]
        accessory = self.button(node.accessory, f"{path} > accessory")
        return hikari.impl.SectionComponentBuilder(accessory=accessory, components=children)

    @staticmethod
    def text_display(node: nodes.TextDisplay) -> hikari.impl.TextDisplayComponentBuilder:
        return hikari.impl.TextDisplayComponentBuilder(content=node.content)

    def button(self, node: nodes.Button, path: str) -> hikari.impl.InteractiveButtonBuilder:
        return hikari.impl.InteractiveButtonBuilder(
            style=node.style,
            custom_id=self.custom_id(node, path),
            label=hikari.UNDEFINED if node.label is None else node.label,
            emoji=hikari.UNDEFINED if node.emoji is None else node.emoji,
            is_disabled=node.disabled,
        )

    def custom_id(self, node: nodes.Button, path: str) -> str:
        bound = node.bound
        if bound.token not in self._meta.handlers:
            reason = f"handler {bound.handler_id!r} (version {bound.version}) is not on view {self._meta.name!r}"
            raise errors.LayoutError(path, reason)

        return codec.encode_custom_id(
            codec.CustomID(
                version=codec.CODEC_VERSION,
                raw_cookie=self._meta.cookie,
                handler=bound.token,
                payload=(self._state_key or "") + bound.payload,
            ),
            view_name=self._meta.name,
        )
