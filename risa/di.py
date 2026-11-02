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
"""Dependency injection for handlers, through linkd."""

from __future__ import annotations

import typing

import linkd
from linkd import INJECTED

__all__ = ("INJECTED", "ComponentContainer", "Contexts", "ModalContainer")


@typing.final
class ComponentContainer(linkd.Container):
    """The linkd container risa opens for each component interaction it dispatches."""

    __slots__ = ()


@typing.final
class ModalContainer(linkd.Container):
    """The linkd container reserved for modal submissions, which risa does not dispatch yet."""

    __slots__ = ()


@typing.final
class Contexts:
    """The linkd contexts risa dispatches in.

    A handler runs inside `DEFAULT` with `COMPONENT` entered on top, so it can be
    injected with anything registered in either. risa adds the `ComponentContext` and the
    `hikari.ComponentInteraction` to `COMPONENT` for every click.

    Attributes
    ----------
    DEFAULT
        linkd's root context, the one a lightbulb client shares.
    COMPONENT
        Entered for every component interaction.
    MODAL
        Reserved for modal submissions.
    """

    __slots__ = ()

    DEFAULT: typing.Final = linkd.Contexts.ROOT
    COMPONENT: typing.Final = linkd.global_context_registry.register(
        "risa.contexts.component",
        ComponentContainer,
    )
    MODAL: typing.Final = linkd.global_context_registry.register(
        "risa.contexts.modal",
        ModalContainer,
    )
