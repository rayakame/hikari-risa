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

import abc
import enum
import typing

import msgspec

if typing.TYPE_CHECKING:
    import collections.abc
    import types

    import hikari

    from risa import view as view_
    from risa.internal import codec
    from risa.internal import registry

__all__ = (
    "Restored",
    "StateBackend",
    "StateSession",
    "Unreadably",
    "hydrate",
)


class Unreadably(enum.Enum):
    MISSING = enum.auto()
    OUTDATED = enum.auto()


class Restored(msgspec.Struct, frozen=True):
    view: view_.View


class StateSession(abc.ABC):
    __slots__ = ()

    async def __aenter__(self) -> typing.Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        await self.close()

    @abc.abstractmethod
    async def open(self) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @abc.abstractmethod
    async def restore(self) -> Restored | Unreadably: ...

    @abc.abstractmethod
    async def stage(self, view: view_.View) -> bytes: ...

    @abc.abstractmethod
    async def published(self, view: view_.View) -> None: ...

    @abc.abstractmethod
    async def settle(self, view: view_.View) -> bool: ...


class StateBackend(abc.ABC):
    __slots__ = ()

    @abc.abstractmethod
    def session(
        self,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> StateSession: ...

    @abc.abstractmethod
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> bytes: ...

    @abc.abstractmethod
    async def commit_first(
        self,
        *,
        meta: registry.ViewMeta,
        view: view_.View,
        anchor: bytes,
    ) -> None: ...


def hydrate(meta: registry.ViewMeta, values: collections.abc.Sequence[object]) -> view_.View:
    kwargs = {field.name: value for field, value in zip(meta.schema.fields, values, strict=False)}
    return meta.cls(**kwargs)
