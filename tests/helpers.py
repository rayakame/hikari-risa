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
"""Shared stubs and drivers for the test suite."""

from __future__ import annotations

import typing
import unittest.mock

import hikari
import msgspec

import risa
from risa import context as context_

if typing.TYPE_CHECKING:
    import contextlib


class Write(msgspec.Struct, frozen=True):
    """One call to :meth:`RecordingStore.put` or :meth:`RecordingStore.touch`."""

    key: str
    ttl: float | None


class RecordingStore(risa.Store):
    """A store that keeps a log of what was written, and defers to a real one.

    Lets a test assert on what the client asked the store to do without reading
    the store's internals, which is the only part of this a test has any business
    knowing about.
    """

    __slots__ = ("_inner", "touches", "writes")

    def __init__(self) -> None:
        self._inner = risa.MemoryStore()
        self.writes: list[Write] = []
        self.touches: list[Write] = []

    @typing.override
    async def get(self, key: str) -> bytes | None:
        return await self._inner.get(key)

    @typing.override
    async def get_versioned(self, key: str) -> tuple[bytes, int] | None:
        return await self._inner.get_versioned(key)

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None = None) -> None:
        self.writes.append(Write(key=key, ttl=ttl))
        await self._inner.put(key, value, ttl=ttl)

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None = None) -> bool:
        self.writes.append(Write(key=key, ttl=ttl))
        return await self._inner.put_if_version(key, value, expected=expected, ttl=ttl)

    @typing.override
    async def delete(self, key: str) -> None:
        await self._inner.delete(key)

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        self.touches.append(Write(key=key, ttl=ttl))
        await self._inner.touch(key, ttl=ttl)

    @typing.override
    def lock(self, key: str) -> contextlib.AbstractAsyncContextManager[None]:
        return self._inner.lock(key)


class StubClient(risa.Client):
    """A client detached from any bot, so dispatch can be driven directly."""

    __slots__ = ()

    @property
    @typing.override
    def app(self) -> hikari.RESTAware:
        raise NotImplementedError

    async def dispatch(self, interaction_: hikari.ComponentInteraction) -> risa.ComponentContext:
        """Feed an interaction in, standing in for a gateway event or a request."""
        state = context_.DispatchState()
        ctx = risa.ComponentContext(self, interaction_, state)
        await self._process_component_interaction(interaction_, ctx, state)
        return ctx


def interaction(custom_id: str) -> hikari.ComponentInteraction:
    """Build a mock component interaction whose response methods can be asserted on."""
    mock = unittest.mock.Mock(spec=hikari.ComponentInteraction)
    mock.custom_id = custom_id
    mock.id = 1
    mock.message = unittest.mock.Mock(spec=hikari.Message)
    mock.execute.return_value = unittest.mock.Mock(spec=hikari.Message)
    return typing.cast("hikari.ComponentInteraction", mock)


def mock_of(method: object) -> unittest.mock.AsyncMock:
    """Recover the mock behind a method of a spec'd interaction, for assertions."""
    return typing.cast("unittest.mock.AsyncMock", method)


def find_custom_ids(node: object) -> list[str]:
    """Return every ``custom_id`` anywhere in a built payload, in order."""
    found: list[str] = []
    if isinstance(node, dict):
        value = typing.cast("dict[str, object]", node).get("custom_id")
        if isinstance(value, str):
            found.append(value)
        for nested in typing.cast("dict[str, object]", node).values():
            found.extend(find_custom_ids(nested))
    elif isinstance(node, list):
        for item in typing.cast("list[object]", node):
            found.extend(find_custom_ids(item))
    return found


def builder_custom_ids(builders: object) -> list[str]:
    """Return every ``custom_id`` in a sequence of hikari component builders."""
    found: list[str] = []
    if isinstance(builders, list):
        for builder in typing.cast("list[hikari.api.ComponentBuilder]", builders):
            payload, _ = builder.build()
            found.extend(find_custom_ids(payload))
    return found


async def all_custom_ids(client: risa.Client, view: risa.View) -> list[str]:
    """Build a view and return every ``custom_id`` it rendered."""
    builders = await client.build(view)
    found: list[str] = []
    for builder in builders:
        payload, _ = builder.build()
        found.extend(find_custom_ids(payload))
    return found


async def first_custom_id(client: risa.Client, view: risa.View) -> str:
    """Build a view and return the first ``custom_id`` it rendered."""
    found = await all_custom_ids(client, view)
    assert found
    return found[0]
