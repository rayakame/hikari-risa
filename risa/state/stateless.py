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
"""The placement of a view that has no durable state to place.

A view whose fields are all props -- or which has no fields at all -- keeps
nothing between clicks, so there is nothing to lock, nothing to read and nothing
to write. Every dispatch simply builds a fresh instance and lets ``load`` fill
it in.

This exists as a backend of its own rather than as a branch in the other two so
that the layers above never ask whether a view is stateful. It is the degenerate
case of the same contract, not a second mode.
"""

from __future__ import annotations

import logging
import typing

from risa.state import backend

if typing.TYPE_CHECKING:
    import hikari

    from risa import view as view_
    from risa.internal import codec
    from risa.internal import registry

__all__ = ("StatelessBackend", "StatelessSession")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.state.stateless")


class StatelessSession(backend.StateSession):
    """One dispatch against a view that keeps nothing.

    Parameters
    ----------
    meta
        The view being dispatched.
    carried
        What the clicked component carried where state would go. Anything at
        all means the component was rendered while the view still had durable
        fields, which is a schema change the click must not survive.
    interaction
        The interaction being dispatched, for diagnostics.
    """

    __slots__ = ("_carried", "_interaction", "_meta")

    def __init__(
        self,
        *,
        meta: registry.ViewMeta,
        carried: str,
        interaction: hikari.ComponentInteraction,
    ) -> None:
        self._meta = meta
        self._carried = carried
        self._interaction = interaction

    @typing.override
    async def open(self) -> None:
        """Take no lock: concurrent clicks share nothing to protect."""

    @typing.override
    async def close(self) -> None:
        """Release nothing."""

    @typing.override
    async def restore(self) -> backend.Restored | backend.Unreadably:
        """Build a fresh instance, or refuse a component that predates the view.

        Returns
        -------
        backend.Restored | backend.Unreadably
            A new view, or :attr:`~risa.state.backend.Unreadably.OUTDATED` when
            the component still carries state this view no longer has.
        """
        if self._carried:
            _LOGGER.error(
                "interaction %s: view %r keeps no state, but the component carries some. Its durable"
                " fields were all removed without bumping the version on @risa.register.",
                self._interaction.id,
                self._meta.name,
            )
            return backend.Unreadably.OUTDATED

        return backend.Restored(view=self._meta.cls())

    @typing.override
    async def stage(self, view: view_.View) -> str:
        """Return the empty anchor: there is nothing for a component to carry.

        Parameters
        ----------
        view
            The view about to be rendered.

        Returns
        -------
        str
            Always ``""``.
        """
        return ""

    @typing.override
    async def settle(self, view: view_.View) -> bool:
        """Report that nothing is owed, because nothing can be.

        Parameters
        ----------
        view
            The view as the handler left it.

        Returns
        -------
        bool
            Always ``False``.
        """
        return False


class StatelessBackend(backend.StateBackend):
    """Opens sessions for views with nothing to keep."""

    __slots__ = ()

    @typing.override
    def session(
        self,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> StatelessSession:
        """Open a session that reads and writes nothing.

        Parameters
        ----------
        meta
            The view the interaction resolved to.
        component
            The clicked component, decoded.
        interaction
            The interaction being dispatched.

        Returns
        -------
        StatelessSession
            A session with no lock and no storage behind it.
        """
        return StatelessSession(meta=meta, carried=component.fragment, interaction=interaction)

    @typing.override
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> str:
        """Return the empty anchor.

        Parameters
        ----------
        meta
            The view being sent.
        view
            The view instance.

        Returns
        -------
        str
            Always ``""``.
        """
        return ""
