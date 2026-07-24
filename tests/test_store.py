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

from risa.state import store as store_


async def test_get_returns_what_put_wrote() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"value")
    assert await store.get("k") == b"value"


async def test_get_returns_none_for_an_absent_key() -> None:
    assert await store_.MemoryStore().get("nothing") is None


async def test_delete_removes_the_entry() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"value")
    await store.delete("k")
    assert await store.get("k") is None


async def test_version_starts_at_one_and_increments_per_write() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"one")
    assert await store.get_versioned("k") == (b"one", 1)
    await store.put("k", b"two")
    assert await store.get_versioned("k") == (b"two", 2)


async def test_put_if_version_writes_when_the_version_still_matches() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"one")
    assert await store.put_if_version("k", b"two", expected=1) is True
    assert await store.get("k") == b"two"


async def test_put_if_version_refuses_a_write_computed_from_stale_state() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"one")
    _, version = (await store.get_versioned("k")) or (b"", 0)

    # Somebody else writes between this reader's read and its write.
    await store.put("k", b"theirs")

    assert await store.put_if_version("k", b"mine", expected=version) is False
    assert await store.get("k") == b"theirs"


async def test_put_if_version_refuses_a_write_to_a_key_that_is_gone() -> None:
    store = store_.MemoryStore()
    assert await store.put_if_version("gone", b"value", expected=1) is False


async def test_an_expired_entry_reads_as_absent() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"value", ttl=0.01)
    await asyncio.sleep(0.05)
    assert await store.get("k") is None


async def test_no_ttl_means_the_entry_does_not_expire() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"value")
    await asyncio.sleep(0.05)
    assert await store.get("k") == b"value"


async def test_touch_pushes_expiry_back() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"value", ttl=0.05)
    await asyncio.sleep(0.03)
    await store.touch("k", ttl=0.5)
    await asyncio.sleep(0.05)
    assert await store.get("k") == b"value"


async def test_the_least_recently_used_entry_is_dropped_first() -> None:
    store = store_.MemoryStore(max_entries=2)
    await store.put("a", b"a")
    await store.put("b", b"b")
    await store.get("a")  # "a" is now the more recently used of the two.
    await store.put("c", b"c")

    assert await store.get("b") is None
    assert await store.get("a") == b"a"
    assert await store.get("c") == b"c"


async def test_the_lock_serialises_a_read_change_write() -> None:
    store = store_.MemoryStore()
    await store.put("k", b"0")

    async def increment() -> None:
        async with store.lock("k"):
            current = await store.get("k")
            assert current is not None
            # Yield mid-sequence: without the lock, every task reads the same
            # value here and the writes collapse into one.
            await asyncio.sleep(0)
            await store.put("k", str(int(current) + 1).encode())

    await asyncio.gather(*(increment() for _ in range(10)))
    assert await store.get("k") == b"10"


async def test_locks_for_different_keys_do_not_block_each_other() -> None:
    store = store_.MemoryStore()
    entered = asyncio.Event()

    async def hold_a() -> None:
        async with store.lock("a"):
            entered.set()
            await asyncio.sleep(0.2)

    async def take_b() -> None:
        await entered.wait()
        async with store.lock("b"):
            pass

    async with asyncio.timeout(0.1):
        task = asyncio.create_task(hold_a())
        await take_b()

    await task
