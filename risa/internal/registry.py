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
"""Registries mapping a view cookie to the metadata needed to dispatch it.

There are two flavours of the same :class:`Registry` type. The process-wide one
returned by :func:`global_registry` is what ``@risa.register`` writes to at import
time -- decorating a view is enough to make it routable. Each
:class:`~risa.client.Client` also owns a private registry, and consults the
global one only when it is created with ``use_global=True``.
"""

from __future__ import annotations

import collections.abc
import typing

import msgspec

from risa import errors as errors_
from risa.state import placement as placement_

if typing.TYPE_CHECKING:
    from risa.internal import codec
    from risa.state import schema as schema_
    from risa.view import AutoDefer
    from risa.view import View

__all__ = (
    "Handler",
    "HandlerRecord",
    "Registry",
    "ViewMeta",
    "global_registry",
)

type Handler = collections.abc.Callable[..., collections.abc.Awaitable[None]]
"""A view callback. Its arguments vary per handler, hence the unbound signature."""


class HandlerRecord(msgspec.Struct, frozen=True):
    """Everything the dispatcher needs about one handler of a view.

    Attributes
    ----------
    callback
        The callback to run, taking the rebuilt view, the context and the
        decoded wire arguments positionally.
    handler_id
        The handler's durable id, for errors and logs.
    version
        The handler's version, for errors and logs.
    signature
        The handler's wire signature, which incoming payloads are decoded
        against.
    defer
        The handler's auto-defer override, or ``None`` to follow the view's
        own setting.
    """

    callback: Handler
    handler_id: str
    version: int
    signature: codec.HandlerSignature
    defer: AutoDefer | None


class ViewMeta(msgspec.Struct, frozen=True):
    """Everything the dispatcher needs about one registered view.

    Attributes
    ----------
    cls
        The view class itself, instantiated per interaction to rebuild state.
    name
        The view name, as declared on ``@risa.register``.
    version
        The schema version, as declared on ``@risa.register``.
    cookie
        The encoded cookie the view routes under, derived from name and version.
    handlers
        The view's handler records, keyed by their encoded handler token.
    schema
        The view's durable fields and the shapes they have had, or an empty
        schema when every field is a prop.
    placement
        Where this view's durable state lives.
    handles_outdated
        Whether the view overrides ``on_outdated``. An override acknowledges
        that components are retired deliberately, which is what downgrades the
        token-miss log from a warning to debug.
    defer
        What the auto-defer watchdog sends for this view's handlers, unless a
        handler overrides it.
    ttl
        Seconds a stored state entry lives for, refreshed on every interaction,
        or ``None`` to keep it until it is deleted or evicted.
    """

    cls: type[View]
    name: str
    version: int
    cookie: str
    handlers: dict[str, HandlerRecord]
    schema: schema_.StateSchema
    placement: placement_.StatePlacement
    handles_outdated: bool
    defer: AutoDefer

    @property
    def stateless(self) -> bool:
        """Whether nothing is persisted for this view."""
        return not self.schema.durable

    @property
    def ttl(self) -> float | None:
        """How long a stored entry survives, for a view that uses a store."""
        return self.placement.ttl if isinstance(self.placement, placement_.InStore) else None


class Registry:
    """A mapping of view cookie to :class:`ViewMeta`."""

    def __init__(self) -> None:
        self._views: dict[str, ViewMeta] = {}

    def register(self, meta: ViewMeta) -> None:
        """Add a view, rejecting a cookie collision with a different view.

        Parameters
        ----------
        meta
            The view metadata to register.

        Raises
        ------
        DuplicateViewError
            If a different view is already registered under the same cookie.
        """
        existing = self._views.get(meta.cookie)
        if existing is not None and existing.cls is not meta.cls:
            raise errors_.DuplicateViewError(meta.name, existing.name, meta.cookie)
        self._views[meta.cookie] = meta

    def get(self, cookie: str) -> ViewMeta | None:
        """Return the view registered under ``cookie``.

        Parameters
        ----------
        cookie
            The encoded cookie to look up.

        Returns
        -------
        ViewMeta | None
            The registered view, or ``None`` if no view uses that cookie.
        """
        return self._views.get(cookie)

    def clear(self) -> None:
        """Drop every registered view. Intended for test isolation."""
        self._views.clear()


_GLOBAL_REGISTRY: typing.Final = Registry()


def global_registry() -> Registry:
    """Return the process-wide registry that ``@risa.register`` populates.

    Returns
    -------
    Registry
        The single shared registry instance.
    """
    return _GLOBAL_REGISTRY
