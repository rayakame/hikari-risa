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

import logging
import typing

import msgspec

from risa import errors
from risa.internal import codec
from risa.internal import constants

if typing.TYPE_CHECKING:
    import collections.abc

    from risa import context
    from risa.view import AutoDefer
    from risa.view import View

__all__ = (
    "DispatchCallback",
    "HandlerRecord",
    "OutdatedCallback",
    "Registry",
    "ViewMeta",
    "global_registry",
    "meta_of",
    "require_meta",
    "stamp",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.internal.registry")

type DispatchCallback = collections.abc.Callable[
    typing.Concatenate[View, context.ComponentContext, ...],
    collections.abc.Awaitable[None],
]
type OutdatedCallback = collections.abc.Callable[
    typing.Concatenate[context.ComponentContext, ...],
    collections.abc.Awaitable[None],
]


class HandlerRecord(msgspec.Struct, frozen=True):
    callback: DispatchCallback
    handler_id: str
    version: int
    defer: AutoDefer | None
    signature: codec.HandlerSignature


class ViewMeta(msgspec.Struct, frozen=True):
    cls: type[View]
    name: str
    version: int
    handlers: collections.abc.Mapping[str, HandlerRecord] = msgspec.field(default_factory=dict[str, HandlerRecord])
    outdated: OutdatedCallback | None = None

    @property
    def handles_outdated(self) -> bool:
        return self.outdated is not None

    @property
    def cookie(self) -> str:
        return codec.make_cookie(self.name, self.version)


class Registry:
    __slots__ = ("_views",)

    def __init__(self) -> None:
        self._views: dict[str, ViewMeta] = {}

    def register(self, meta: ViewMeta) -> None:
        existing = self._views.get(meta.cookie)
        if existing is not None and existing.cls is not meta.cls:
            raise errors.DuplicateViewError(meta.name, existing.name, meta.cookie)
        _LOGGER.debug("registering view %s", meta.name)
        self._views[meta.cookie] = meta

    def get(self, key: str) -> ViewMeta | None:
        return self._views.get(key)

    def clear(self) -> None:
        self._views.clear()


_GLOBAL_REGISTRY: typing.Final = Registry()


def global_registry() -> Registry:
    return _GLOBAL_REGISTRY


def stamp(cls: type[View], meta: ViewMeta) -> None:
    setattr(cls, constants.VIEW_META, meta)


def meta_of(cls: type[View]) -> ViewMeta | None:
    meta = cls.__dict__.get(constants.VIEW_META)
    return meta if isinstance(meta, ViewMeta) else None


def require_meta(obj: type[View] | View) -> ViewMeta:
    cls = obj if isinstance(obj, type) else type(obj)
    meta = meta_of(cls)
    if meta is None:
        raise errors.NotAViewError(cls.__name__)
    return meta
