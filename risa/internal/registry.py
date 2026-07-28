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
"""What risa knows about a view, and where it looks the view up.

There are two flavours of the same :class:`Registry`. The process-wide one
returned by :func:`global_registry` is what ``@risa.register`` writes to at
import time, so decorating a view is enough to make it known. Each
:class:`~risa.client.Client` also owns a private registry and consults the
global one only when it was built with ``use_global=True``, which is what keeps
two clients in one process from answering for each other's views.
"""

from __future__ import annotations

import logging
import typing

import msgspec

from risa import errors

if typing.TYPE_CHECKING:
    from risa.view import View

__all__ = ("Registry", "ViewMeta", "global_registry")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.internal.registry")


class ViewMeta(msgspec.Struct, frozen=True):
    """Everything risa knows about one registered view.

    Attributes
    ----------
    cls
        The view class itself, which is what a dispatch instantiates.
    name
        The durable name the view is known by, as declared on
        ``@risa.register``. Durable in the sense that it outlives a rename of
        the Python class: it is what a component sent last week still refers to.
    version
        The view's version. Bumping it makes the view a different view as far as
        routing is concerned, which is how components rendered under an older
        shape are retired rather than being answered by code that no longer
        fits them.
    """

    cls: type[View]
    name: str
    version: int

    @property
    def key(self) -> str:
        """What this view is registered and looked up under.

        Plain text for now, and deliberately so: what a component actually
        carries has to fit in a ``custom_id`` alongside everything else, so the
        wire form of this will be something short and hashed. Nothing outside
        the registry should care which, so nothing outside it should build this
        string by hand.
        """
        return f"{self.name}:{self.version}"


class Registry:
    """A mapping of routing key to the view registered under it."""

    __slots__ = ("_views",)

    def __init__(self) -> None:
        self._views: dict[str, ViewMeta] = {}

    def register(self, meta: ViewMeta) -> None:
        """Add a view, rejecting a key two different views both claim.

        Registering the same class twice is not an error -- a module imported
        twice, or a client that adds a view already registered globally, both
        arrive here with the same class and mean no harm.

        Parameters
        ----------
        meta
            The view to add.

        Raises
        ------
        DuplicateViewError
            If a different view is already registered under the same key.
        """
        existing = self._views.get(meta.key)
        if existing is not None and existing.cls is not meta.cls:
            raise errors.DuplicateViewError(meta.name, existing.name, meta.key)
        _LOGGER.debug("registering view %s", meta.name)
        self._views[meta.key] = meta

    def get(self, key: str) -> ViewMeta | None:
        """Return the view registered under ``key``.

        Parameters
        ----------
        key
            The routing key to look up.

        Returns
        -------
        ViewMeta | None
            The view, or ``None`` if nothing is registered under that key.
        """
        return self._views.get(key)

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
