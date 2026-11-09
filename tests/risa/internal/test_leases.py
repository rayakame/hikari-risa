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
import typing

from risa.internal import leases

if typing.TYPE_CHECKING:
    import pytest


async def _granted(*_args: str | float) -> bool:  # ruff:ignore[unused-async]
    return True


async def _ready() -> None:
    pass


async def test_a_failing_renewal_is_retried_and_a_lapsed_lease_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="risa")
    outcomes: list[bool | Exception] = [ConnectionError("reset"), False]
    lapsed = asyncio.Event()

    async def renew(_key: str, _token: str, _lease: float) -> bool:  # ruff:ignore[unused-async]
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        lapsed.set()
        return outcome

    held = leases.LeaseLocks(
        claim=_granted,
        renew=renew,
        release=_granted,
        prepare=_ready,
        transient=[ConnectionError],
        lease=0.03,
        max_hold=10.0,
    )
    async with held.hold("key", timeout=1.0):
        await asyncio.wait_for(lapsed.wait(), timeout=1.0)

    messages = [record.getMessage() for record in caplog.records]
    assert any("renewing the lease on state key 'key' failed (reset)" in message for message in messages)
    assert any("lapsed while its holder was still inside" in message for message in messages)
    assert outcomes == []
