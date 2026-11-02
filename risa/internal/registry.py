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
"""What risa knows about each registered view, and the lookup from cookie to view."""

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
    from risa.state import placement as placement_
    from risa.state import schema as schema_
    from risa.view import AutoDefer
    from risa.view import View


__all__ = (
    "DispatchCallback",
    "HandlerRecord",
    "HookCallback",
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
type HookCallback = collections.abc.Callable[
    typing.Concatenate[context.ComponentContext, ...],
    collections.abc.Awaitable[None],
]
type LoadCallback = collections.abc.Callable[
    typing.Concatenate[View, ...],
    collections.abc.Awaitable[None],
]


class HandlerRecord(msgspec.Struct, frozen=True):
    """A handler as dispatch calls it.

    Attributes
    ----------
    callback
        The handler function, wrapped by `linkd.inject`.
    handler_id
        The id it routes under.
    version
        Its version.
    defer
        Its own `AutoDefer`, already resolved against the view's.
    signature
        Its wire parameters.
    """

    callback: DispatchCallback
    handler_id: str
    version: int
    defer: AutoDefer | None
    signature: codec.HandlerSignature


class ViewMeta(msgspec.Struct, frozen=True):
    """Everything `@risa.register` resolved about a view, stamped on the class.

    `handlers` maps routing tokens to handlers. `outdated`, `state_missing` and `loader`
    are `None` when the view does not override them; an overridden `on_outdated` or
    `on_state_missing` also quiets the warning the click that reaches it would log.
    """

    cls: type[View]
    name: str
    version: int
    schema: schema_.StateSchema
    state: placement_.StatePlacement
    handlers: collections.abc.Mapping[str, HandlerRecord] = msgspec.field(default_factory=dict[str, HandlerRecord])
    outdated: HookCallback | None = None
    state_missing: HookCallback | None = None
    loader: LoadCallback | None = None

    @property
    def handles_outdated(self) -> bool:
        return self.outdated is not None

    @property
    def handles_state_missing(self) -> bool:
        return self.state_missing is not None

    @property
    def cookie(self) -> str:
        """The cookie the view routes under, from its name and schema version."""
        return codec.make_cookie(self.name, self.version)

    @property
    def handles_load(self) -> bool:
        return self.loader is not None


class Registry:
    """Registered views by cookie.

    `@risa.register` adds to the global one; each client keeps its own for the views
    passed to `add_view`.
    """

    __slots__ = ("_views",)

    def __init__(self) -> None:
        self._views: dict[str, ViewMeta] = {}

    def register(self, meta: ViewMeta) -> None:
        """Add `meta` under its cookie. Registering the same class again replaces it.

        Raises
        ------
        DuplicateViewError
            If a different class already routes under that cookie.
        """
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
    """Attach `meta` to `cls`, where `meta_of` finds it."""
    setattr(cls, constants.VIEW_META, meta)


def meta_of(cls: type[View]) -> ViewMeta | None:
    """Return the meta stamped on `cls` itself, or `None`.

    It reads the class's own `__dict__`, so a subclass of a registered view is not taken
    for that view until it is registered itself.

    Returns
    -------
    ViewMeta | None
        The meta, or `None` for a class that was never registered.
    """
    meta = cls.__dict__.get(constants.VIEW_META)
    return meta if isinstance(meta, ViewMeta) else None


def require_meta(obj: type[View] | View) -> ViewMeta:
    """Return the meta of a view class or instance.

    Returns
    -------
    ViewMeta
        The meta `@risa.register` stamped on the class.

    Raises
    ------
    NotAViewError
        If the class was never registered.
    """
    cls = obj if isinstance(obj, type) else type(obj)
    meta = meta_of(cls)
    if meta is None:
        raise errors.NotAViewError(cls.__name__)
    return meta
