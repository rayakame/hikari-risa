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
"""The `InMessage` placement: state read from the clicked message, written by editing it."""

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
    from risa.internal import registry

__all__ = (
    "DEFAULT_MAX_CACHED",
    "MessageBackend",
    "MessageSession",
    "read_anchor",
    "unpack_anchor",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.state.message")

DEFAULT_MAX_CACHED: typing.Final[int] = 10_000
_SEQ_SPACE: typing.Final[int] = anchor.MAX_SEQ + 1


class MessageSession(backend.StateSession):
    """One click on an `InMessage` view, or one update of it through `Client.open`.

    The state comes from the origin's message, for a click the one Discord delivered with
    it. When two clicks land close together, the second can arrive with the
    message from before the first one's edit. The backend therefore remembers the last
    commit per message, and the anchor whose `seq` is newer wins.
    """

    __slots__ = ("_backend", "_baseline", "_cookie", "_held", "_meta", "_origin", "_seq", "_staged")

    def __init__(
        self,
        owner: MessageBackend,
        *,
        meta: registry.ViewMeta,
        origin: backend.Origin,
    ) -> None:
        self._backend = owner
        self._meta = meta
        self._cookie = origin.component.cookie
        self._origin = origin
        self._held = contextlib.AsyncExitStack()
        self._seq = 0
        self._baseline: bytes | None = None
        self._staged: anchor.MessageAnchor | None = None

    @typing.override
    async def open(self) -> None:
        await self._held.enter_async_context(self._backend.hold(self._origin.message.id))

    @typing.override
    async def close(self) -> None:
        await self._held.aclose()

    @typing.override
    async def restore(self) -> backend.Restored | backend.Unreadably:
        read = read_anchor(self._meta, self._origin, self._cookie)
        if isinstance(read, anchor.StoreAnchor | anchor.TakenOverAnchor):
            _LOGGER.error(
                "%s routes to view %s, which keeps its state in the message, but message %s"
                " points at a store record: the placement was switched without bumping version=%d; bump"
                " the version to retire these components",
                self._origin.asker,
                self._meta.name,
                self._origin.message.id,
                self._meta.version,
            )
            return backend.Unreadably.OUTDATED
        if read is None:
            return backend.Unreadably.OUTDATED
        cached = self._backend.cached(self._origin.message.id, self._cookie)
        if cached is not None and _newer(cached.seq, read.seq):
            read = cached
        values = unpack_anchor(self._meta, self._origin, read)
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
        self._backend.remember(self._origin.message.id, self._cookie, staged)

    @typing.override
    async def settle(self, view: view_.View) -> bool:
        if self._baseline is None:
            msg = "settle() needs a restore() before it"
            raise RuntimeError(msg)
        return self._meta.schema.pack_view(view) != self._baseline


class MessageBackend(backend.StateBackend):
    """The `InMessage` placement, with one lock per message and the cache of recent commits.

    Parameters
    ----------
    max_cached
        How many messages' last commits to remember, least recently used first out.
    lock_timeout
        Seconds a click waits for the message's lock.
    """

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
    def session(self, *, meta: registry.ViewMeta, origin: backend.Origin) -> MessageSession:
        return MessageSession(self, meta=meta, origin=origin)

    @typing.override
    @contextlib.asynccontextmanager
    async def update(
        self,
        *,
        meta: registry.ViewMeta,
        message_id: hikari.Snowflake,
        origin: backend.OriginFetcher,
    ) -> collections.abc.AsyncGenerator[MessageSession]:
        # The lock's key is the message id, so the fetch runs under it and never reads what a click is about to change.
        async with self.hold(message_id):
            yield MessageSession(self, meta=meta, origin=await origin())

    @typing.override
    def first_anchor(self, *, meta: registry.ViewMeta, view: view_.View) -> bytes:
        return anchor.MessageAnchor(meta.schema.fingerprint, 0, meta.schema.pack_view(view)).pack()

    @typing.override
    async def commit_first(self, *, meta: registry.ViewMeta, view: view_.View, anchor: bytes) -> None:
        pass

    def hold(self, message_id: hikari.Snowflake) -> contextlib.AbstractAsyncContextManager[None]:
        """Hold the lock of one message, so clicks on it run one at a time."""
        return self._locks.hold(str(message_id), timeout=self._lock_timeout)

    def cached(self, message_id: hikari.Snowflake, cookie: str) -> anchor.MessageAnchor | None:
        """Return the last commit remembered for the view `cookie` on a message, or `None`."""
        committed = self._cache.get((message_id, cookie))
        if committed is not None:
            self._cache.move_to_end((message_id, cookie))
        return committed

    def forget(self, message_id: hikari.Snowflake) -> None:
        """Drop every commit remembered for a message, of whichever view."""
        for key in [key for key in self._cache if key[0] == message_id]:
            del self._cache[key]

    def remember(self, message_id: hikari.Snowflake, cookie: str, committed: anchor.MessageAnchor) -> None:
        """Remember a commit as the latest for the view `cookie` on a message."""
        self._cache[message_id, cookie] = committed
        self._cache.move_to_end((message_id, cookie))
        while len(self._cache) > self._max_cached:
            self._cache.popitem(last=False)


def read_anchor(
    meta: registry.ViewMeta,
    origin: backend.Origin,
    cookie: str,
) -> anchor.MessageAnchor | anchor.StoreAnchor | anchor.TakenOverAnchor | None:
    """Reassemble the anchor of the view `cookie` from the origin's message.

    Returns
    -------
    MessageAnchor | StoreAnchor | TakenOverAnchor | None
        The anchor in whichever dialect the message holds it, or `None` after a warning that
        it holds none.

    Raises
    ------
    CodecUnavailableError
        If a component is written in a codec this process cannot decode.
    """
    found = reader.fragments(origin.message.components, cookie=cookie)
    if isinstance(found, errors.CodecUnavailableError):
        raise found
    if found is None:
        _log_torn(meta, origin)
        return None
    if _unwritten(found):
        return anchor.MessageAnchor(meta.schema.empty_fingerprint, 0, b"")
    raw = anchor.gather(found)
    parsed = None if raw is None else anchor.parse(raw)
    if parsed is None:
        _log_torn(meta, origin)
    return parsed


def unpack_anchor(
    meta: registry.ViewMeta,
    origin: backend.Origin,
    read: anchor.MessageAnchor,
) -> collections.abc.Sequence[object] | None:
    """Return the durable values a message anchor holds, or log why it cannot be read and return `None`."""
    schema = meta.schema
    if not schema.accepts(read.schema_fp):
        _LOGGER.error(
            "%s routes to view %s, but message %s was written by durable fields this code"
            " no longer accepts: a field was removed, reordered, retyped, or added without a default,"
            " without bumping version=%d; bump the version to retire these components",
            origin.asker,
            meta.name,
            origin.message.id,
            meta.version,
        )
        return None
    values = schema.unpack(read.schema_fp, read.payload)
    if values is None:
        _LOGGER.warning(
            "%s routes to view %s, but the state in message %s does not decode under its"
            " own fingerprint: a value of an enum or Literal field may have been removed, or the"
            " components are forged or corrupted",
            origin.asker,
            meta.name,
            origin.message.id,
        )
    return values


def _log_torn(meta: registry.ViewMeta, origin: backend.Origin) -> None:
    _LOGGER.warning(
        "%s routes to view %s, but its state could not be reassembled from message %s;"
        " the message was edited by something other than risa, or its components were forged",
        origin.asker,
        meta.name,
        origin.message.id,
    )


def _unwritten(found: collections.abc.Mapping[int, bytes]) -> bool:
    """Whether every fragment is present and empty, as for a view sent before it had state."""
    return bool(found) and not any(found.values()) and sorted(found) == list(range(len(found)))


def _newer(candidate: int, than: int) -> bool:
    """Whether sequence number `candidate` is newer than `than`.

    The counter wraps, so this is serial number arithmetic: a number up to half the
    sequence space ahead counts as newer.

    Returns
    -------
    bool
        Whether `candidate` is the newer of the two.
    """
    return 0 < (candidate - than) % _SEQ_SPACE < _SEQ_SPACE // 2
