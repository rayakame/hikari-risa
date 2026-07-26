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
"""The dependency injection scopes risa dispatches inside.

linkd resolves a dependency against whichever container is open when an injected
function runs, so risa opens one per invocation. :class:`Contexts` names those
scopes.

:attr:`Contexts.DEFAULT` is the outermost one and is shared by every risa flow;
register long-lived dependencies against it::

    client.di.registry_for(risa.Contexts.DEFAULT).register_value(Database, db)

Each kind of callback then runs in its own scope nested inside that default one,
holding only what belongs to a single invocation. Nesting is what makes the
default scope's dependencies visible from the inner ones, so risa always enters
both.

A callback declares what it needs by annotating a parameter risa does not supply
itself: anything left over after the arguments risa passes is resolved from the
open containers by its annotation. :data:`INJECTED` may be used as the
parameter's default to say so explicitly, which reads better when a signature
mixes injected and ordinary defaulted parameters.
"""

from __future__ import annotations

import typing

import linkd
from linkd import INJECTED

__all__ = ("INJECTED", "ComponentContainer", "Contexts", "ModalContainer")


@typing.final
class ComponentContainer(linkd.Container):
    """Injectable type naming the container opened for a component interaction."""

    __slots__ = ()


@typing.final
class ModalContainer(linkd.Container):
    """Injectable type naming the container opened for a modal submission."""

    __slots__ = ()


@typing.final
class Contexts:
    """The dependency injection contexts risa dispatches inside.

    Attributes
    ----------
    DEFAULT
        The outermost scope, shared by every risa flow. Aliases linkd's root
        context, so dependencies registered here are also visible to any other
        framework sharing the same manager.
    COMPONENT
        Scope opened for one component interaction, nested inside
        :attr:`DEFAULT`.
    MODAL
        Scope opened for one modal submission, nested inside :attr:`DEFAULT`.
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
