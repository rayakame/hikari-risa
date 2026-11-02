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

import asyncio
import logging
import secrets
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import sys

import asyncpg
import pytest

import risa
from risa.store import postgres
from risa.store import testing


def fresh_key() -> str:
    return f"risa:test:{secrets.token_hex(8)}"


def unopened_pool() -> asyncpg.Pool:
    return asyncpg.create_pool("postgresql://risa@localhost/risa")


async def stored_keys(pool: asyncpg.Pool, table: str) -> set[str]:
    rows = await pool.fetch(f'SELECT key FROM "{table}"')  # ruff:ignore[hardcoded-sql-expression]
    return {row["key"] for row in rows}


async def lease_count(pool: asyncpg.Pool, table: str) -> int:
    return await pool.fetchval(f'SELECT count(*) FROM "{table}_lease"')  # ruff:ignore[hardcoded-sql-expression]


async def test_a_postgres_store_conforms(postgres_pool: asyncpg.Pool, postgres_table: str) -> None:
    await testing.verify_store(postgres.PostgresStore(postgres_pool, table=postgres_table))


async def test_a_whole_second_ttl_is_accepted(postgres_pool: asyncpg.Pool, postgres_table: str) -> None:
    store = postgres.PostgresStore(postgres_pool, table=postgres_table)
    key = fresh_key()

    await store.put(key, b"one", ttl=30 * 86400)
    await store.touch(key, ttl=60)

    assert await store.get(key) == b"one"


async def test_the_tables_are_created_on_first_use(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="risa"):
        assert await postgres.PostgresStore(postgres_pool, table=postgres_table).get(fresh_key()) is None

    assert await postgres_pool.fetchval("SELECT to_regclass($1) IS NOT NULL", postgres_table)
    assert await postgres_pool.fetchval("SELECT to_regclass($1) IS NOT NULL", f"{postgres_table}_lease")
    assert [record.levelno for record in caplog.records] == [logging.INFO]


async def test_tables_that_already_exist_are_used_as_they_are(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await postgres.PostgresStore(postgres_pool, table=postgres_table).get(fresh_key())

    with caplog.at_level(logging.INFO, logger="risa"):
        await postgres.PostgresStore(postgres_pool, table=postgres_table).put(fresh_key(), b"one", ttl=None)

    assert not caplog.records


async def test_processes_starting_together_create_the_tables_once(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    stores = [postgres.PostgresStore(postgres_pool, table=postgres_table) for _ in range(4)]

    await asyncio.gather(*(store.put(fresh_key(), b"one", ttl=None) for store in stores))

    assert len(await stored_keys(postgres_pool, postgres_table)) == len(stores)


async def test_the_first_write_deletes_entries_that_expired_before_it(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    earlier = postgres.PostgresStore(postgres_pool, table=postgres_table)
    await earlier.put("kept", b"", ttl=None)
    for key in ("first", "second", "third"):
        await earlier.put(key, b"", ttl=0.05)
    await asyncio.sleep(0.15)

    await postgres.PostgresStore(postgres_pool, table=postgres_table).put("new", b"", ttl=None)

    assert await stored_keys(postgres_pool, postgres_table) == {"kept", "new"}


async def test_after_the_first_write_expired_entries_are_deleted_every_hundred_writes(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    store = postgres.PostgresStore(postgres_pool, table=postgres_table)
    await store.put("kept", b"", ttl=None)
    await store.put("expired", b"", ttl=0.05)
    await asyncio.sleep(0.15)

    for _ in range(98):
        await store.touch("kept", ttl=60.0)
    left_before = await stored_keys(postgres_pool, postgres_table)
    await store.touch("kept", ttl=60.0)

    assert left_before == {"kept", "expired"}
    assert await stored_keys(postgres_pool, postgres_table) == {"kept"}


async def test_a_lapsed_lease_left_by_a_crashed_process_is_deleted(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    store = postgres.PostgresStore(postgres_pool, table=postgres_table)
    await store.get(fresh_key())
    await postgres_pool.execute(
        f"INSERT INTO \"{postgres_table}_lease\" VALUES ($1, 'crashed', now() - interval '1 second')",  # ruff:ignore[hardcoded-sql-expression]
        fresh_key(),
    )

    await store.put(fresh_key(), b"one", ttl=None)

    assert await lease_count(postgres_pool, postgres_table) == 0


async def test_a_failing_sweep_is_logged_and_the_write_still_lands(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = postgres.PostgresStore(postgres_pool, table=postgres_table)
    key = fresh_key()
    await store.get(key)
    await postgres_pool.execute(f'DROP TABLE "{postgres_table}_lease"')

    with caplog.at_level(logging.WARNING, logger="risa"):
        await store.put(key, b"one", ttl=None)

    assert await store.get(key) == b"one"
    (warning,) = [record.getMessage() for record in caplog.records]
    assert "expired view state" in warning


async def test_the_lock_excludes_another_process(postgres_pool: asyncpg.Pool, postgres_table: str) -> None:
    first = postgres.PostgresStore(postgres_pool, table=postgres_table)
    second = postgres.PostgresStore(postgres_pool, table=postgres_table)
    key = fresh_key()

    async with first.lock(key):
        with pytest.raises(risa.LockTimeoutError):
            async with second.lock(key, timeout=0.2):
                pytest.fail("the second process entered while the first held the lock")


async def test_another_process_enters_once_the_lock_is_released(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    first = postgres.PostgresStore(postgres_pool, table=postgres_table)
    second = postgres.PostgresStore(postgres_pool, table=postgres_table)
    key = fresh_key()
    entered = asyncio.Event()

    async def wait_for_it() -> None:
        async with second.lock(key, timeout=2.0):
            entered.set()

    async with first.lock(key):
        waiter = asyncio.create_task(wait_for_it())
        await asyncio.sleep(0.1)
        assert not entered.is_set()
    await asyncio.wait_for(waiter, timeout=2.0)

    assert entered.is_set()
    assert await lease_count(postgres_pool, postgres_table) == 0


async def test_a_held_lease_is_renewed_past_its_length(postgres_pool: asyncpg.Pool, postgres_table: str) -> None:
    first = postgres.PostgresStore(postgres_pool, table=postgres_table, lease=0.3)
    second = postgres.PostgresStore(postgres_pool, table=postgres_table, lease=0.3)
    key = fresh_key()

    async with first.lock(key):
        with pytest.raises(risa.LockTimeoutError):
            async with second.lock(key, timeout=1.0):
                pytest.fail("the lease lapsed while its holder was still inside")


async def test_a_crashed_holder_is_taken_over_once_its_lease_lapses(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    store = postgres.PostgresStore(postgres_pool, table=postgres_table)
    key = fresh_key()
    await store.get(key)
    await postgres_pool.execute(
        f"INSERT INTO \"{postgres_table}_lease\" VALUES ($1, 'crashed', now() + interval '0.2 second')",  # ruff:ignore[hardcoded-sql-expression]
        key,
    )
    loop = asyncio.get_running_loop()
    started = loop.time()

    async with store.lock(key, timeout=2.0):
        waited = loop.time() - started

    assert waited >= 0.1


async def test_a_holder_past_max_hold_lets_the_next_one_in(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stalled = postgres.PostgresStore(postgres_pool, table=postgres_table, lease=0.3, max_hold=0.6)
    second = postgres.PostgresStore(postgres_pool, table=postgres_table)
    key = fresh_key()

    with caplog.at_level(logging.WARNING, logger="risa"):
        async with stalled.lock(key), second.lock(key, timeout=2.0):
            pass

    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("max_hold" in message for message in messages)
    assert any("lapsed" in message for message in messages)


async def test_a_lapsed_holder_letting_go_leaves_the_next_holder_alone(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    stalled = postgres.PostgresStore(postgres_pool, table=postgres_table, lease=0.1, max_hold=0.2)
    successor = postgres.PostgresStore(postgres_pool, table=postgres_table)
    latecomer = postgres.PostgresStore(postgres_pool, table=postgres_table)
    key = fresh_key()
    inside = asyncio.Event()
    leave = asyncio.Event()

    async def hold() -> None:
        async with successor.lock(key, timeout=2.0):
            inside.set()
            await leave.wait()

    async with stalled.lock(key):
        holder = asyncio.create_task(hold())
        await asyncio.wait_for(inside.wait(), timeout=2.0)
    try:
        with pytest.raises(risa.LockTimeoutError):
            async with latecomer.lock(key, timeout=0.2):
                pytest.fail("letting go of a lapsed lease freed the lock its successor holds")
    finally:
        leave.set()
        await holder


async def test_a_blocked_lease_table_cannot_stretch_the_lock_timeout(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    store = postgres.PostgresStore(postgres_pool, table=postgres_table)
    await store.get(fresh_key())

    async with postgres_pool.acquire() as connection, connection.transaction():
        await connection.execute(f'LOCK TABLE "{postgres_table}_lease" IN ACCESS EXCLUSIVE MODE')
        with pytest.raises(risa.LockTimeoutError):
            async with asyncio.timeout(2.0), store.lock(fresh_key(), timeout=0.1):
                pytest.fail("the lock was granted while the lease table was locked")


async def test_a_blocked_table_creation_cannot_stretch_the_lock_timeout(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    store = postgres.PostgresStore(postgres_pool, table=postgres_table)

    async with postgres_pool.acquire() as connection:
        await connection.execute("SELECT pg_advisory_lock(hashtext($1))", postgres_table)
        try:
            with pytest.raises(risa.LockTimeoutError):
                async with asyncio.timeout(2.0), store.lock(fresh_key(), timeout=0.1):
                    pytest.fail("the lock was granted before its tables could be created")
        finally:
            await connection.execute("SELECT pg_advisory_unlock(hashtext($1))", postgres_table)


async def test_contention_within_one_process_queues_without_timing_out(
    postgres_pool: asyncpg.Pool,
    postgres_table: str,
) -> None:
    store = postgres.PostgresStore(postgres_pool, table=postgres_table)
    key = fresh_key()
    order: list[int] = []

    async def take(index: int) -> None:
        async with store.lock(key, timeout=2.0):
            order.append(index)
            await asyncio.sleep(0.01)

    await asyncio.gather(*(take(index) for index in range(5)))

    assert sorted(order) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("table", ["", "Risa", "risa-state", "1risa", 'risa"; DROP TABLE x; --', "r" * 53])
async def test_a_table_that_is_not_a_plain_identifier_is_refused(table: str) -> None:  # ruff:ignore[unused-async]
    with pytest.raises(ValueError, match="table"):
        postgres.PostgresStore(unopened_pool(), table=table)


async def test_the_longest_table_name_is_accepted() -> None:  # ruff:ignore[unused-async]
    postgres.PostgresStore(unopened_pool(), table="r" * 52)


@pytest.mark.parametrize("lease", [0.0, -1.0])
async def test_a_lease_must_be_positive(lease: float) -> None:  # ruff:ignore[unused-async]
    with pytest.raises(ValueError, match="lease"):
        postgres.PostgresStore(unopened_pool(), lease=lease)


async def test_a_max_hold_shorter_than_the_lease_is_refused() -> None:  # ruff:ignore[unused-async]
    with pytest.raises(ValueError, match="max_hold"):
        postgres.PostgresStore(unopened_pool(), lease=5.0, max_hold=1.0)


def test_importing_risa_leaves_asyncpg_unimported() -> None:
    probe = "import sys, risa; print('asyncpg' in sys.modules)"

    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False"
