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

from risa.state import backend

if typing.TYPE_CHECKING:
    import hikari

    from risa import view as view_
    from risa.internal import codec
    from risa.internal import registry

__all__ = (
    "StatelessBackend",
    "StatelessSession",
)


class StatelessSession(backend.StateSession):
    __slots__ = ("_meta",)

    def __init__(self, *, meta: registry.ViewMeta) -> None:
        self._meta = meta

    @typing.override
    async def open(self) -> None:
        pass

    @typing.override
    async def close(self) -> None:
        pass

    @typing.override
    async def restore(self) -> backend.Restored:
        return backend.Restored(self._meta.cls())

    @typing.override
    async def stage(self, view: view_.View) -> bytes:
        return b""

    @typing.override
    async def published(self, view: view_.View) -> None:
        pass

    @typing.override
    async def settle(self, view: view_.View) -> bool:
        return False


class StatelessBackend(backend.StateBackend):
    __slots__ = ()

    @typing.override
    def session(
        self,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> StatelessSession:
        return StatelessSession(meta=meta)

    @typing.override
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> bytes:
        return b""

    @typing.override
    async def commit_first(self, *, meta: registry.ViewMeta, view: view_.View, anchor: bytes) -> None:
        pass
