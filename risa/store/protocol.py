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
"""The contract a state store implements."""

from __future__ import annotations

import typing

import msgspec

if typing.TYPE_CHECKING:
    import contextlib

__all__ = (
    "DEFAULT_LOCK_TIMEOUT",
    "Store",
    "VersionedEntry",
)

DEFAULT_LOCK_TIMEOUT: typing.Final[float] = 10.0
"""Seconds `Store.lock()` waits for a key before it raises `LockTimeoutError`."""


class VersionedEntry(msgspec.Struct, frozen=True):
    """An entry as a store holds it.

    Attributes
    ----------
    data
        The bytes last written.
    version
        The version of that write. It changes with every write to the entry.
    """

    data: bytes
    version: int


class Store(typing.Protocol):
    """Where risa keeps the state of `InStore` views.

    Implement it to keep state somewhere risa ships no store for, and check the
    implementation with `risa.store.testing.verify_store`. A missing or expired entry is
    never an error: it reads as `None`. Apart from `LockTimeoutError` from `lock`, any
    exception a method raises means the backend could not be reached, and risa reports it
    as `StoreUnavailableError`.
    """

    __slots__ = ()

    async def get_versioned(self, key: str) -> VersionedEntry | None:
        """Return the data and version under `key`, or `None` if there is none or it has expired.

        Parameters
        ----------
        key
            The entry's key, an opaque string. A view's record is under `{prefix}:1:{hex}`,
            the count of a ready-made hook under `{prefix}:hook:...`, and risa passes the one
            key of an entry to every method, `lock` included.

        Returns
        -------
        :
            The bytes last written with their version, or `None`.
        """

    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        """Write `value` under `key`, whether or not an entry exists.

        The entry's version changes. `ttl` is the entry's lifetime in seconds from now, and
        `None` clears any expiry, so the entry lives until it is deleted.

        Parameters
        ----------
        key
            The entry's key, as for `get_versioned`.
        value
            The bytes to keep. `get_versioned` returns them unchanged, whatever they hold, empty included.
        ttl
            Seconds the entry lives, counted from this write and fractions included. `None`
            gives it no expiry, which also clears the expiry an earlier write set.
        """

    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        """Write `value` under `key` only if the entry is still at version `expected`.

        The compare and the write are one atomic step: of several writers expecting the same
        version, exactly one succeeds. `ttl` works as in `put`.

        Parameters
        ----------
        key
            The entry's key, as for `get_versioned`.
        value
            The bytes to keep if the write goes through.
        expected
            A version from `get_versioned`, or returned by an earlier `put_if_version`. The
            write goes through only while the entry is still at exactly that version. Versions
            compare only within one entry's life: a store may restart them once the entry is
            deleted or has expired.
        ttl
            Seconds the entry lives from this write, as in `put`. `None` gives it no expiry.

        Returns
        -------
        :
            The entry's new version, or `None` if the entry is missing, expired, or at a
            different version, in which case nothing was written.
        """

    async def delete(self, key: str) -> None:
        """Remove the entry under `key`, if there is one.

        Parameters
        ----------
        key
            The entry's key, as for `get_versioned`.
        """

    async def touch(self, key: str, *, ttl: float) -> None:
        """Restart the lifetime of the entry under `key`, which now expires `ttl` seconds from now.

        The data and version stay as they are, and a missing key stays missing.

        Parameters
        ----------
        key
            The entry's key, as for `get_versioned`.
        ttl
            Seconds the entry now lives, counted from this call and replacing the expiry it
            had.
        """

    def lock(self, key: str, *, timeout: float = DEFAULT_LOCK_TIMEOUT) -> contextlib.AbstractAsyncContextManager[None]:
        """Hold `key` exclusively while the returned context manager is entered.

        risa holds it for the length of a click, so clicks on one view run one after
        another. A store that several processes share should exclude all of them; when such
        a lock lapses, the version check of `put_if_version` still decides whose write
        lands.

        Parameters
        ----------
        key
            The key to hold; risa passes the key of the entry it is about to read and write.
            Holding one key does not make another wait, and the key needs no entry.
        timeout
            Seconds to wait for the lock, fractions included, before raising
            `LockTimeoutError`.

        Raises
        ------
        LockTimeoutError
            If the lock is not granted within `timeout` seconds.
        """
        ...
