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

import itertools
import typing
import unittest.mock

import hikari
import msgspec

import risa
from risa import context as context_
from risa.internal import anchor as anchor_
from risa.internal import codec as codec_
from risa.internal import constants as constants_
from risa.internal import registry as registry_
from risa.state import store as store_

if typing.TYPE_CHECKING:
    import collections.abc
    import contextlib

# Distinct per interaction, so that the per-message locks and version cache a
# message-resident view keeps never bleed from one test into the next.
_MESSAGE_IDS = itertools.count(1)


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
    def lock(
        self,
        key: str,
        *,
        timeout: float = store_.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        return self._inner.lock(key, timeout=timeout)


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


def interaction(
    custom_id: str,
    *,
    components: collections.abc.Sequence[str | hikari.PartialComponent] = (),
    message_id: int | None = None,
) -> hikari.ComponentInteraction:
    """Build a mock component interaction whose response methods can be asserted on.

    ``components`` is what the interaction's message carries -- ``custom_id``\\ s
    for the flat case, or built components for a nested one -- which is where a
    message-resident view reads its state back from. ``message_id`` pins the
    message, for a test that puts two renders on one.
    """
    mock = unittest.mock.Mock(spec=hikari.ComponentInteraction)
    mock.custom_id = custom_id
    mock.id = 1
    mock.message = unittest.mock.Mock(spec=hikari.Message)
    mock.message.id = next(_MESSAGE_IDS) if message_id is None else message_id
    mock.message.components = [
        component(rendered) if isinstance(rendered, str) else rendered for rendered in components
    ]
    mock.execute.return_value = unittest.mock.Mock(spec=hikari.Message)
    return typing.cast("hikari.ComponentInteraction", mock)


def component(custom_id: str) -> hikari.PartialComponent:
    """Return a component of a delivered message, carrying ``custom_id``."""
    mock = unittest.mock.Mock(spec=hikari.ButtonComponent)
    mock.custom_id = custom_id
    return typing.cast("hikari.PartialComponent", mock)


async def clicked(client: risa.Client, view: risa.View, *, on: int = 0) -> hikari.ComponentInteraction:
    """Render a view and return a click on one of the components it rendered.

    The whole message goes along, exactly as Discord delivers it, so a view that
    carries its state in its own components can read it back.
    """
    rendered = await all_custom_ids(client, view)
    return interaction(rendered[on], components=rendered)


def meta_of(cls: type[risa.View]) -> registry_.ViewMeta:
    """Return the metadata ``@risa.register`` stamped onto a view class."""
    return typing.cast("registry_.ViewMeta", getattr(cls, constants_.VIEW_META))


def mock_of(method: object) -> unittest.mock.AsyncMock:
    """Recover the mock behind a method of a spec'd interaction, for assertions."""
    return typing.cast("unittest.mock.AsyncMock", method)


def state_key_of(decoded: codec_.CustomID) -> str:
    """Return the store key a decoded component's anchor names."""
    parsed = anchor_.parse(decoded.fragment)
    assert isinstance(parsed, anchor_.StoreAnchor)
    return parsed.key


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


def answered_with(inter: hikari.ComponentInteraction) -> list[str]:
    """Return the ``custom_id``\\ s of the components an interaction was answered with."""
    for method in (inter.create_initial_response, inter.edit_initial_response, inter.message.edit):
        call = mock_of(method).await_args
        if call is not None and "components" in call.kwargs:
            return builder_custom_ids(list(call.kwargs["components"]))
    return []


def next_click(inter: hikari.ComponentInteraction, *, on: int = 0) -> hikari.ComponentInteraction:
    """Return a click on the message as the previous answer left it."""
    rendered = answered_with(inter)
    return interaction(rendered[on], components=rendered)


async def first_custom_id(client: risa.Client, view: risa.View) -> str:
    """Build a view and return the first ``custom_id`` it rendered."""
    found = await all_custom_ids(client, view)
    assert found
    return found[0]
