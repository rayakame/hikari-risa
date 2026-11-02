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

import os
import secrets
import typing

import asyncpg
import pytest

if typing.TYPE_CHECKING:
    import collections.abc

POSTGRES_URL = os.environ.get("RISA_TEST_POSTGRES_URL")


@pytest.fixture
async def postgres_pool() -> collections.abc.AsyncIterator[asyncpg.Pool]:
    if POSTGRES_URL is None:
        pytest.skip("set RISA_TEST_POSTGRES_URL to test against a real Postgres")
    pool = await asyncpg.create_pool(POSTGRES_URL)
    yield pool
    await pool.close()


@pytest.fixture
async def postgres_table(postgres_pool: asyncpg.Pool) -> collections.abc.AsyncIterator[str]:
    table = f"risa_test_{secrets.token_hex(6)}"
    yield table
    await postgres_pool.execute(f'DROP TABLE IF EXISTS "{table}", "{table}_lease"')
