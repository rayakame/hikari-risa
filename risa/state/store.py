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
"""Where a view's state lives between one interaction and the next.

A ``custom_id`` has room for a key, not for state, so everything a view holds is
kept here and the id carries only the random token it is filed under. That
indirection is what lets state outgrow a hundred characters, and what lets a
component keep working after a restart -- provided the store outlives the
process, which :class:`MemoryStore` does not.

Stores move bytes. Turning a view into bytes and back is a separate concern, so
that a backend never has to know anything about views.
"""

from __future__ import annotations

import asyncio
import collections
import time
import typing
import weakref

import msgspec

if typing.TYPE_CHECKING:
    import contextlib

__all__ = ("MemoryStore", "Store")


@typing.runtime_checkable
class Store(typing.Protocol):
    """Somewhere a view's state can be kept between interactions.

    Implement this to keep state somewhere shared. Anything beyond one process --
    shards split across processes, or a REST bot behind a load balancer -- needs
    a store every instance can reach, since a random key belongs to no shard in
    particular.
    """

    async def get(self, key: str) -> bytes | None:
        """Return what is stored under ``key``, or ``None`` if nothing is.

        Parameters
        ----------
        key
            The key to read.

        Returns
        -------
        bytes | None
            The stored value, or ``None`` when absent or expired.
        """
        ...

    async def get_versioned(self, key: str) -> tuple[bytes, int] | None:
        """Return what is stored under ``key`` together with its version.

        The version is what :meth:`put_if_version` compares against, so that a
        write which lost a race is rejected rather than silently overwriting.

        Parameters
        ----------
        key
            The key to read.

        Returns
        -------
        tuple[bytes, int] | None
            The stored value and its version, or ``None`` when absent.
        """
        ...

    async def put(self, key: str, value: bytes, *, ttl: float | None = None) -> None:
        """Store ``value`` under ``key``, replacing whatever was there.

        Parameters
        ----------
        key
            The key to write.
        value
            The bytes to store.
        ttl
            Seconds until the entry expires, or ``None`` to keep it until it is
            deleted or evicted.
        """
        ...

    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None = None) -> bool:
        """Store ``value`` only if the stored version is still ``expected``.

        Parameters
        ----------
        key
            The key to write.
        value
            The bytes to store.
        expected
            The version the caller read before making its changes.
        ttl
            Seconds until the entry expires, or ``None`` for no expiry.

        Returns
        -------
        bool
            Whether the write happened. ``False`` means something else wrote
            first and the caller's changes were computed from stale state.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove whatever is stored under ``key``, if anything is.

        Parameters
        ----------
        key
            The key to remove.
        """
        ...

    async def touch(self, key: str, *, ttl: float) -> None:
        """Push back when ``key`` expires, without reading or rewriting it.

        Kept apart from :meth:`get` so that whether reading counts as use is the
        caller's policy rather than the backend's.

        Parameters
        ----------
        key
            The key to renew.
        ttl
            Seconds from now until the entry expires.
        """
        ...

    def lock(self, key: str) -> contextlib.AbstractAsyncContextManager[None]:
        """Hold ``key`` for the duration of a read, change and write.

        Two people pressing the same button at once otherwise both read the same
        state and both write their own version of it, and one of the two changes
        is lost. Held around the whole sequence, not just the write.

        Parameters
        ----------
        key
            The key to hold.

        Returns
        -------
        contextlib.AbstractAsyncContextManager[None]
            A context manager holding the key while it is entered.
        """
        ...


class _Entry(msgspec.Struct):
    """One stored value, with what is needed to expire and version it."""

    value: bytes
    version: int
    expires_at: float | None


class MemoryStore(Store):
    """A store keeping everything in this process.

    The default, and enough for a bot running as a single process. It is not
    enough for anything else: state written by one process is invisible to every
    other, so shards split across processes, a REST bot behind a load balancer,
    or a send from a worker that did not receive the click will all find nothing
    under the key. It does not survive a restart either.

    Parameters
    ----------
    max_entries
        How many entries to keep before the least recently used is dropped.
    """

    __slots__ = ("_entries", "_locks", "_max_entries")

    def __init__(self, *, max_entries: int = 100_000) -> None:
        self._entries: collections.OrderedDict[str, _Entry] = collections.OrderedDict()
        # Weak, so that a key's lock lives exactly as long as somebody is holding
        # or waiting on it, rather than accumulating one per key ever used.
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._max_entries = max_entries

    @typing.override
    async def get(self, key: str) -> bytes | None:
        """Return what is stored under ``key``, or ``None`` if nothing is.

        Parameters
        ----------
        key
            The key to read.

        Returns
        -------
        bytes | None
            The stored value, or ``None`` when absent or expired.
        """
        entry = self._live_entry(key)
        return None if entry is None else entry.value

    @typing.override
    async def get_versioned(self, key: str) -> tuple[bytes, int] | None:
        """Return what is stored under ``key`` together with its version.

        Parameters
        ----------
        key
            The key to read.

        Returns
        -------
        tuple[bytes, int] | None
            The stored value and its version, or ``None`` when absent.
        """
        entry = self._live_entry(key)
        return None if entry is None else (entry.value, entry.version)

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None = None) -> None:
        """Store ``value`` under ``key``, replacing whatever was there.

        Parameters
        ----------
        key
            The key to write.
        value
            The bytes to store.
        ttl
            Seconds until the entry expires, or ``None`` for no expiry.
        """
        previous = self._live_entry(key)
        version = 1 if previous is None else previous.version + 1
        self._write(key, _Entry(value=value, version=version, expires_at=self._deadline(ttl)))

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None = None) -> bool:
        """Store ``value`` only if the stored version is still ``expected``.

        Parameters
        ----------
        key
            The key to write.
        value
            The bytes to store.
        expected
            The version the caller read before making its changes.
        ttl
            Seconds until the entry expires, or ``None`` for no expiry.

        Returns
        -------
        bool
            Whether the write happened.
        """
        entry = self._live_entry(key)
        if entry is None or entry.version != expected:
            return False

        self._write(key, _Entry(value=value, version=entry.version + 1, expires_at=self._deadline(ttl)))
        return True

    @typing.override
    async def delete(self, key: str) -> None:
        """Remove whatever is stored under ``key``, if anything is.

        Parameters
        ----------
        key
            The key to remove.
        """
        self._entries.pop(key, None)

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        """Push back when ``key`` expires.

        Parameters
        ----------
        key
            The key to renew.
        ttl
            Seconds from now until the entry expires.
        """
        entry = self._live_entry(key)
        if entry is not None:
            entry.expires_at = self._deadline(ttl)

    @typing.override
    def lock(self, key: str) -> contextlib.AbstractAsyncContextManager[None]:
        """Hold ``key`` for the duration of a read, change and write.

        Parameters
        ----------
        key
            The key to hold.

        Returns
        -------
        contextlib.AbstractAsyncContextManager[None]
            A context manager holding the key while it is entered.
        """
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _live_entry(self, key: str) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None

        if entry.expires_at is not None and entry.expires_at <= time.monotonic():
            del self._entries[key]
            return None

        self._entries.move_to_end(key)
        return entry

    def _write(self, key: str, entry: _Entry) -> None:
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    @staticmethod
    def _deadline(ttl: float | None) -> float | None:
        return None if ttl is None else time.monotonic() + ttl
