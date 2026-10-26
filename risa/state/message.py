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

import collections.abc
import contextlib
import logging
import typing

from risa import errors
from risa.internal import locks
from risa.internal import reader
from risa.state import anchor
from risa.state import backend
from risa.store import protocol

if typing.TYPE_CHECKING:
    import hikari

    from risa import view as view_
    from risa.internal import codec
    from risa.internal import registry

__all__ = (
    "DEFAULT_MAX_CACHED",
    "MessageBackend",
    "MessageSession",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.state.message")

DEFAULT_MAX_CACHED: typing.Final[int] = 10_000
_SEQ_SPACE: typing.Final[int] = anchor.MAX_SEQ + 1


class MessageSession(backend.StateSession):
    __slots__ = ("_backend", "_baseline", "_cookie", "_held", "_interaction", "_meta", "_seq", "_staged")

    def __init__(
        self,
        owner: MessageBackend,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> None:
        self._backend = owner
        self._meta = meta
        self._cookie = component.cookie
        self._interaction = interaction
        self._held = contextlib.AsyncExitStack()
        self._seq = 0
        self._baseline: bytes | None = None
        self._staged: anchor.MessageAnchor | None = None

    @typing.override
    async def open(self) -> None:
        await self._held.enter_async_context(self._backend.hold(self._interaction.message.id))

    @typing.override
    async def close(self) -> None:
        await self._held.aclose()

    @typing.override
    async def restore(self) -> backend.Restored | backend.Unreadably:
        read = self._read()
        if read is None:
            return backend.Unreadably.OUTDATED
        cached = self._backend.cached(self._interaction.message.id, self._cookie)
        if cached is not None and _newer(cached.seq, read.seq):
            read = cached
        values = self._unpack(read)
        if values is None:
            return backend.Unreadably.OUTDATED
        view = backend.hydrate(self._meta, values)
        self._seq = read.seq
        self._baseline = self._meta.schema.pack_view(view)
        return backend.Restored(view)

    @typing.override
    async def stage(self, view: view_.View) -> bytes:
        schema = self._meta.schema
        staged = anchor.MessageAnchor(schema.fingerprint, (self._seq + 1) % _SEQ_SPACE, schema.pack_view(view))
        raw = staged.pack()
        self._staged = staged
        return raw

    @typing.override
    async def published(self, view: view_.View) -> None:
        staged = self._staged
        if staged is None:
            msg = "published() needs a stage() before it"
            raise RuntimeError(msg)
        self._staged = None
        self._seq = staged.seq
        self._baseline = staged.payload
        self._backend.remember(self._interaction.message.id, self._cookie, staged)

    @typing.override
    async def settle(self, view: view_.View) -> bool:
        if self._baseline is None:
            msg = "settle() needs a restore() before it"
            raise RuntimeError(msg)
        return self._meta.schema.pack_view(view) != self._baseline

    def _read(self) -> anchor.MessageAnchor | None:
        found = reader.fragments(self._interaction.message.components, cookie=self._cookie)
        if isinstance(found, errors.CodecUnavailableError):
            raise found
        if found is None:
            self._torn()
            return None
        if _unwritten(found):
            return anchor.MessageAnchor(self._meta.schema.empty_fingerprint, 0, b"")
        raw = anchor.gather(found)
        parsed = None if raw is None else anchor.parse(raw)
        if isinstance(parsed, anchor.StoreAnchor):
            _LOGGER.error(
                "interaction %s routes to view %s, which keeps its state in the message, but message %s"
                " carries a store key: the placement was switched without bumping version=%d; bump the"
                " version to retire these components",
                self._interaction.id,
                self._meta.name,
                self._interaction.message.id,
                self._meta.version,
            )
            return None
        if parsed is None:
            self._torn()
        return parsed

    def _torn(self) -> None:
        _LOGGER.warning(
            "interaction %s routes to view %s, but its state could not be reassembled from message %s;"
            " the message was edited by something other than risa, or its components were forged",
            self._interaction.id,
            self._meta.name,
            self._interaction.message.id,
        )

    def _unpack(self, read: anchor.MessageAnchor) -> collections.abc.Sequence[object] | None:
        schema = self._meta.schema
        if not schema.accepts(read.schema_fp):
            _LOGGER.error(
                "interaction %s routes to view %s, but message %s was written by durable fields this code"
                " no longer accepts: a field was removed, reordered, retyped, or added without a default,"
                " without bumping version=%d; bump the version to retire these components",
                self._interaction.id,
                self._meta.name,
                self._interaction.message.id,
                self._meta.version,
            )
            return None
        values = schema.unpack(read.schema_fp, read.payload)
        if values is None:
            _LOGGER.warning(
                "interaction %s routes to view %s, but the state in message %s does not decode under its"
                " own fingerprint; the components may be forged or corrupted",
                self._interaction.id,
                self._meta.name,
                self._interaction.message.id,
            )
        return values


class MessageBackend(backend.StateBackend):
    __slots__ = ("_cache", "_lock_timeout", "_locks", "_max_cached")

    def __init__(
        self,
        *,
        max_cached: int = DEFAULT_MAX_CACHED,
        lock_timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> None:
        self._locks = locks.KeyedLocks()
        self._cache: collections.OrderedDict[tuple[hikari.Snowflake, str], anchor.MessageAnchor] = (
            collections.OrderedDict()
        )
        self._max_cached = max_cached
        self._lock_timeout = lock_timeout

    @typing.override
    def session(
        self,
        *,
        meta: registry.ViewMeta,
        component: codec.CustomID,
        interaction: hikari.ComponentInteraction,
    ) -> MessageSession:
        return MessageSession(self, meta=meta, component=component, interaction=interaction)

    @typing.override
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> bytes:
        return anchor.MessageAnchor(meta.schema.fingerprint, 0, meta.schema.pack_view(view)).pack()

    @typing.override
    async def commit_first(self, *, meta: registry.ViewMeta, view: view_.View, anchor: bytes) -> None:
        pass

    def hold(self, message_id: hikari.Snowflake) -> contextlib.AbstractAsyncContextManager[None]:
        return self._locks.hold(str(message_id), timeout=self._lock_timeout)

    def cached(self, message_id: hikari.Snowflake, cookie: str) -> anchor.MessageAnchor | None:
        committed = self._cache.get((message_id, cookie))
        if committed is not None:
            self._cache.move_to_end((message_id, cookie))
        return committed

    def remember(self, message_id: hikari.Snowflake, cookie: str, committed: anchor.MessageAnchor) -> None:
        self._cache[message_id, cookie] = committed
        self._cache.move_to_end((message_id, cookie))
        while len(self._cache) > self._max_cached:
            self._cache.popitem(last=False)


def _unwritten(found: collections.abc.Mapping[int, bytes]) -> bool:
    return bool(found) and not any(found.values()) and sorted(found) == list(range(len(found)))


def _newer(candidate: int, than: int) -> bool:
    return 0 < (candidate - than) % _SEQ_SPACE < _SEQ_SPACE // 2
