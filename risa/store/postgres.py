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
"""`PostgresStore`, a store on PostgreSQL. Install it with the `[postgres]` extra."""

from __future__ import annotations

import asyncio
import logging
import re
import typing

import asyncpg
import msgspec

from risa.internal import leases
from risa.store import protocol

if typing.TYPE_CHECKING:
    import contextlib

__all__ = (
    "DEFAULT_LEASE",
    "DEFAULT_MAX_HOLD",
    "DEFAULT_TABLE",
    "PostgresStore",
)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("risa.store.postgres")

DEFAULT_LEASE: typing.Final[float] = leases.DEFAULT_LEASE
"""Seconds a lock's lease lasts before it needs renewing."""
DEFAULT_MAX_HOLD: typing.Final[float] = leases.DEFAULT_MAX_HOLD
"""Seconds after which a held lock is no longer renewed; its last lease then runs out."""
DEFAULT_TABLE: typing.Final[str] = "risa_state"
"""The name of the state table when none is given."""

_TABLE_NAME: typing.Final[re.Pattern[str]] = re.compile(r"[a-z_][a-z0-9_]{0,51}")
_SWEEP_EVERY: typing.Final[int] = 100
_SWEEP_BATCH: typing.Final[int] = 500
_TRANSIENT: typing.Final[tuple[type[Exception], ...]] = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
)

_EXISTS: typing.Final[str] = "SELECT to_regclass($1) IS NOT NULL AND to_regclass($2) IS NOT NULL"
_SCHEMA_LOCK: typing.Final[str] = "SELECT pg_advisory_xact_lock(hashtext($1))"
_CREATE: typing.Final[str] = """
CREATE TABLE IF NOT EXISTS {state} (
    key text PRIMARY KEY,
    data bytea NOT NULL,
    version bigint NOT NULL,
    expires_at timestamptz
);
CREATE INDEX IF NOT EXISTS {expiry} ON {state} (expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS {lease} (
    key text PRIMARY KEY,
    token text NOT NULL,
    expires_at timestamptz NOT NULL
)
"""
_GET_VERSIONED: typing.Final[str] = """
SELECT data, version FROM {state} WHERE key = $1 AND (expires_at IS NULL OR expires_at > now())
"""
_PUT: typing.Final[str] = """
INSERT INTO {state} AS entry (key, data, version, expires_at)
VALUES ($1, $2, 1, now() + $3::float8 * interval '1 second')
ON CONFLICT (key) DO UPDATE
SET data = excluded.data, version = entry.version + 1, expires_at = excluded.expires_at
"""
_PUT_IF_VERSION: typing.Final[str] = """
UPDATE {state}
SET data = $2, version = version + 1, expires_at = now() + $4::float8 * interval '1 second'
WHERE key = $1 AND version = $3 AND (expires_at IS NULL OR expires_at > now())
RETURNING version
"""
_DELETE: typing.Final[str] = """
DELETE FROM {state} WHERE key = $1
"""
_TOUCH: typing.Final[str] = """
UPDATE {state}
SET expires_at = now() + $2::float8 * interval '1 second'
WHERE key = $1 AND (expires_at IS NULL OR expires_at > now())
RETURNING true
"""
_SWEEP: typing.Final[str] = """
DELETE FROM {state} WHERE key IN (
    SELECT key FROM {state} WHERE expires_at <= now() LIMIT {batch} FOR UPDATE SKIP LOCKED
);
DELETE FROM {lease} WHERE key IN (
    SELECT key FROM {lease} WHERE expires_at <= now() LIMIT {batch} FOR UPDATE SKIP LOCKED
)
"""
_CLAIM: typing.Final[str] = """
INSERT INTO {lease} AS lease (key, token, expires_at)
VALUES ($1, $2, now() + $3::float8 * interval '1 second')
ON CONFLICT (key) DO UPDATE
SET token = excluded.token, expires_at = excluded.expires_at
WHERE lease.expires_at <= now()
RETURNING true
"""
_RENEW: typing.Final[str] = """
UPDATE {lease}
SET expires_at = now() + $3::float8 * interval '1 second'
WHERE key = $1 AND token = $2 AND expires_at > now()
RETURNING true
"""
_RELEASE: typing.Final[str] = """
DELETE FROM {lease} WHERE key = $1 AND token = $2 RETURNING expires_at > now()
"""


class _Sql(msgspec.Struct, frozen=True):
    state: str
    lease: str
    create: str
    get_versioned: str
    put: str
    put_if_version: str
    delete: str
    touch: str
    sweep: str
    claim: str
    renew: str
    release: str


class PostgresStore(protocol.Store):
    """A store on PostgreSQL, through an asyncpg pool.

    An entry is a row of `table`. `lock()` takes a lease on a row of `{table}_lease` and
    renews it while held, so clicks on one view queue up across every process that shares
    the database. The store creates both tables on first use if they are missing, and
    deletes expired rows itself: on its first write, then on every hundredth.

    Parameters
    ----------
    pool
        The pool the bot owns. The store borrows a connection for each statement and never
        opens or closes the pool.
    table
        The name of the state table: a lowercase identifier of at most 52 characters. The
        lease table is named after it.
    lease
        Seconds a lock's lease lasts. It is renewed every third of that while held, and a
        crashed holder blocks its view for at most this long.
    max_hold
        Seconds after which a held lock is no longer renewed, so a stuck handler cannot
        block its view for good.

    Raises
    ------
    ValueError
        If `table` is not a plain lowercase identifier, `lease` is not positive, or
        `max_hold` is shorter than `lease`.
    """

    __slots__ = ("_locks", "_pool", "_prepared", "_preparing", "_sql", "_table", "_writes_until_sweep")

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        table: str = DEFAULT_TABLE,
        lease: float = DEFAULT_LEASE,
        max_hold: float = DEFAULT_MAX_HOLD,
    ) -> None:
        if not _TABLE_NAME.fullmatch(table):
            msg = f"table must be a lowercase SQL identifier of at most 52 characters, got {table!r}"
            raise ValueError(msg)
        self._pool = pool
        self._table = table
        self._sql = _statements(table)
        self._prepared = False
        self._preparing = asyncio.Lock()
        self._writes_until_sweep = 1
        self._locks = leases.LeaseLocks(
            claim=self._claim,
            renew=self._renew,
            release=self._release,
            prepare=self._prepare,
            transient=_TRANSIENT,
            lease=lease,
            max_hold=max_hold,
        )

    @typing.override
    async def get_versioned(self, key: str) -> protocol.VersionedEntry | None:
        await self._prepare()
        row = await self._pool.fetchrow(self._sql.get_versioned, key)
        if row is None:
            return None
        return protocol.VersionedEntry(data=_expect(row["data"], bytes), version=_expect(row["version"], int))

    @typing.override
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        await self._prepare()
        await self._pool.execute(self._sql.put, key, value, ttl)
        await self._sweep_if_due()

    @typing.override
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> int | None:
        await self._prepare()
        written = await self._pool.fetchval(self._sql.put_if_version, key, value, expected, ttl)
        await self._sweep_if_due()
        return None if written is None else _expect(written, int)

    @typing.override
    async def delete(self, key: str) -> None:
        await self._prepare()
        await self._pool.execute(self._sql.delete, key)

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        await self._prepare()
        if await self._pool.fetchval(self._sql.touch, key, ttl) is None:
            _LOGGER.debug("state key %r is gone; nothing to keep alive", key)
        await self._sweep_if_due()

    @typing.override
    def lock(
        self,
        key: str,
        *,
        timeout: float = protocol.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        return self._locks.hold(key, timeout=timeout)

    async def _prepare(self) -> None:
        if self._prepared:
            return
        async with self._preparing:
            if not self._prepared:
                await self._create()
                self._prepared = True

    async def _create(self) -> None:
        if await self._pool.fetchval(_EXISTS, self._sql.state, self._sql.lease):
            return
        _LOGGER.info("creating the tables %s and %s_lease that keep view state", self._table, self._table)
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(_SCHEMA_LOCK, self._table)
            await connection.execute(self._sql.create)

    async def _sweep_if_due(self) -> None:
        self._writes_until_sweep -= 1
        if self._writes_until_sweep > 0:
            return
        self._writes_until_sweep = _SWEEP_EVERY
        try:
            await self._pool.execute(self._sql.sweep)
        except _TRANSIENT as exc:
            _LOGGER.warning(
                "deleting expired view state from %s failed (%s); it stays unreadable, and a later sweep retries",
                self._table,
                exc,
            )

    async def _claim(self, key: str, token: str, lease: float) -> bool:
        return await self._pool.fetchval(self._sql.claim, key, token, lease) is not None

    async def _renew(self, key: str, token: str, lease: float) -> bool:
        return await self._pool.fetchval(self._sql.renew, key, token, lease) is not None

    async def _release(self, key: str, token: str) -> bool:
        return await self._pool.fetchval(self._sql.release, key, token) is True


def _statements(table: str) -> _Sql:
    names = {"state": f'"{table}"', "lease": f'"{table}_lease"', "expiry": f'"{table}_expires_at"'}
    return _Sql(
        state=names["state"],
        lease=names["lease"],
        create=_CREATE.format_map(names),
        get_versioned=_GET_VERSIONED.format_map(names),
        put=_PUT.format_map(names),
        put_if_version=_PUT_IF_VERSION.format_map(names),
        delete=_DELETE.format_map(names),
        touch=_TOUCH.format_map(names),
        sweep=_SWEEP.format_map({**names, "batch": _SWEEP_BATCH}),
        claim=_CLAIM.format_map(names),
        renew=_RENEW.format_map(names),
        release=_RELEASE.format_map(names),
    )


def _expect[T](value: object, kind: type[T]) -> T:
    if isinstance(value, kind):
        return value
    msg = (
        f"Postgres answered with {type(value).__name__}, not {kind.__name__};"
        " a type codec on the pool must not change how risa's columns decode"
    )
    raise TypeError(msg)
