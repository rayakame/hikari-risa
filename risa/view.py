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
"""What a view is, and how one becomes routable.

A view is the unit risa dispatches to: a class holding whatever state the
message needs and, in time, the handlers its components route back to. It is a
:class:`msgspec.Struct`, so its annotated attributes are its state -- declaring
a field is declaring something risa will have to carry between clicks.

``@risa.register`` is what makes a view routable. It records the name and
version the view answers to and files it in the process-wide registry, so that
importing the module is enough for a client built with ``use_global=True`` to
find it.
"""

from __future__ import annotations

import typing

import msgspec

from risa import errors
from risa.internal import constants
from risa.internal import registry

__all__ = ("View", "register")


class View(msgspec.Struct):
    """Base class every view inherits from.

    Subclassing declares the metadata attribute ``@risa.register`` later fills
    in, which is what lets the client be typed against views rather than
    against bare classes.

    Being a :class:`msgspec.Struct`, a subclass's annotated attributes are its
    state: what a handler mutates, and what risa has to get back to the next
    click on the same message.
    """

    __risa_view_meta__: typing.ClassVar[registry.ViewMeta]


def register[T: View](*, name: str, version: int = 1) -> typing.Callable[[type[T]], type[T]]:
    """Make a view routable under a durable name.

    The name is what a component rendered today still refers to next week, so
    it is declared rather than taken from the class: renaming the class is a
    refactor, while renaming the view retires every component that named the
    old one.

    Bumping ``version`` has the same effect deliberately. It is how a view whose
    shape changed stops answering for components that were rendered under the
    old one, instead of decoding their state into something it no longer fits.

    Parameters
    ----------
    name
        Durable identity for the view. Must be unique per version.
    version
        The view's version.

    Returns
    -------
    typing.Callable[[type[T]], type[T]]
        A decorator returning the class unchanged.

    Raises
    ------
    ViewDeclarationError
        If the view is declared in a way risa cannot honour.
    DuplicateViewError
        If a different view is already registered under the same name and
        version.
    """

    def decorate(cls: type[T]) -> type[T]:
        if not name.strip():
            reason = "a view needs a name to be routable, and it must not be blank"
            raise errors.ViewDeclarationError(cls.__name__, reason)
        if version < 1:
            reason = f"version must be 1 or greater, got {version}"
            raise errors.ViewDeclarationError(name, reason)

        meta = registry.ViewMeta(cls=cls, name=name, version=version)
        registry.global_registry().register(meta)
        setattr(cls, constants.VIEW_META, meta)
        return cls

    return decorate
