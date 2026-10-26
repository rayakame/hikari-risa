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
import logging
import time
import typing

import msgspec

from risa.internal import locks
from risa.store import protocol

if typing.TYPE_CHECKING:
    import contextlib

__all__ = (
    "DEFAULT_MAX_ENTRIES",
    "MemoryStore",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.store.memory")

DEFAULT_MAX_ENTRIES: typing.Final[int] = 10_000


class _Entry(msgspec.Struct):
    data: bytes
    version: int
    expires_at: float | None


class MemoryStore:
    __slots__ = ("_capacity_warned", "_clock", "_entries", "_locks", "_max_entries")

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: collections.abc.Callable[[], float] = time.monotonic,
    ) -> None:
        self._entries: collections.OrderedDict[str, _Entry] = collections.OrderedDict()
        self._locks = locks.KeyedLocks()
        self._max_entries = max_entries
        self._clock = clock
        self._capacity_warned = False

    async def get(self, key: str) -> bytes | None:
        entry = self._live(key)
        return None if entry is None else entry.data

    async def get_versioned(self, key: str) -> protocol.VersionedEntry | None:
        entry = self._live(key)
        return None if entry is None else protocol.VersionedEntry(data=entry.data, version=entry.version)

    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        existing = self._live(key)
        self._write(key, value, version=1 if existing is None else existing.version + 1, ttl=ttl)

    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        existing = self._live(key)
        if existing is None or existing.version != expected:
            return None
        self._write(key, value, version=existing.version + 1, ttl=ttl)
        return existing.version + 1

    async def delete(self, key: str) -> None:
        self._entries.pop(key, None)

    async def touch(self, key: str, *, ttl: float) -> None:
        entry = self._live(key)
        if entry is None:
            _LOGGER.debug("state key %r is gone; nothing to keep alive", key)
            return
        entry.expires_at = self._deadline(ttl)

    def lock(
        self,
        key: str,
        *,
        timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        return self._locks.hold(key, timeout=timeout)

    def _live(self, key: str) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= self._clock():
            del self._entries[key]
            _LOGGER.debug("state key %r expired and was dropped", key)
            return None
        self._entries.move_to_end(key)
        return entry

    def _deadline(self, ttl: float | None) -> float | None:
        return None if ttl is None else self._clock() + ttl

    def _write(self, key: str, value: bytes, *, version: int, ttl: float | None) -> None:
        self._entries[key] = _Entry(data=value, version=version, expires_at=self._deadline(ttl))
        self._entries.move_to_end(key)
        self._evict()

    def _evict(self) -> None:
        while len(self._entries) > self._max_entries:
            if not self._capacity_warned:
                self._capacity_warned = True
                _LOGGER.warning(
                    "the in-memory state store reached its %d entry limit and is dropping the"
                    " least recently used state; live components whose state is dropped stop"
                    " working. Raise max_entries, or move to a real store.",
                    self._max_entries,
                )
            key, _ = self._entries.popitem(last=False)
            _LOGGER.debug("evicted state key %r to stay within %d entries", key, self._max_entries)
