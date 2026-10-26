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

import pytest

import risa
from risa.internal import locks


async def test_one_key_admits_one_holder_at_a_time() -> None:
    table = locks.KeyedLocks()
    order: list[str] = []
    first_in = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> None:
        async with table.hold("message", timeout=1):
            order.append("first in")
            first_in.set()
            await release_first.wait()
            order.append("first out")

    async def second() -> None:
        await first_in.wait()
        async with table.hold("message", timeout=1):
            order.append("second in")

    task = asyncio.gather(first(), second())
    await first_in.wait()
    await asyncio.sleep(0)
    release_first.set()
    await task

    assert order == ["first in", "first out", "second in"]


async def test_different_keys_do_not_wait_for_each_other() -> None:
    table = locks.KeyedLocks()

    async with table.hold("one", timeout=1), table.hold("two", timeout=0.01):
        assert len(table) == 2


async def test_a_wait_past_the_timeout_names_the_key() -> None:
    table = locks.KeyedLocks()

    async with table.hold("message", timeout=1):
        with pytest.raises(risa.LockTimeoutError) as exc_info:
            async with table.hold("message", timeout=0.01):
                pass

    assert exc_info.value.key == "message"
    assert exc_info.value.timeout == pytest.approx(0.01)


async def test_the_table_drops_a_key_once_its_last_holder_leaves() -> None:
    table = locks.KeyedLocks()

    async with table.hold("message", timeout=1):
        assert len(table) == 1

    assert len(table) == 0


async def test_a_holder_that_raises_still_releases() -> None:
    table = locks.KeyedLocks()

    async def failing() -> None:
        async with table.hold("message", timeout=1):
            msg = "handler failed"
            raise ValueError(msg)

    with pytest.raises(ValueError, match="handler failed"):
        await failing()

    async with table.hold("message", timeout=0.01):
        assert len(table) == 1
