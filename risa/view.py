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
from __future__ import annotations

import typing

import msgspec

from risa import errors
from risa.internal import constants
from risa.internal import registry

__all__ = ("View", "register")


class View(msgspec.Struct):
    __risa_view_meta__: typing.ClassVar[registry.ViewMeta]


def register[T: View](*, name: str, version: int = 1) -> typing.Callable[[type[T]], type[T]]:
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
