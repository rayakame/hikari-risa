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
"""A store of your own, and the test that holds it to what risa relies on."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
import time
import typing

import hikari
import msgspec

import risa
from risa import ui
from risa.store.testing import verify_store

if typing.TYPE_CHECKING:
    import collections.abc


class _Entry(msgspec.Struct):
    data: bytes
    version: int
    expires_at: float | None


class DictStore(risa.Store):
    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._versions = itertools.count(1)

    async def get(self, key: str) -> bytes | None:
        entry = self._live(key)
        return None if entry is None else entry.data

    async def get_versioned(self, key: str) -> risa.VersionedEntry | None:
        entry = self._live(key)
        if entry is None:
            return None
        return risa.VersionedEntry(data=entry.data, version=entry.version)

    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None:
        self._entries[key] = self._new_entry(value, ttl)

    # --8<-- [start:compare-and-set]
    async def put_if_version(
        self,
        key: str,
        value: bytes,
        *,
        expected: int,
        ttl: float | None,
    ) -> int | None:
        entry = self._live(key)
        if entry is None or entry.version != expected:
            return None
        written = self._new_entry(value, ttl)
        self._entries[key] = written
        return written.version
        # --8<-- [end:compare-and-set]

    async def delete(self, key: str) -> None:
        self._entries.pop(key, None)

    async def touch(self, key: str, *, ttl: float) -> None:
        entry = self._live(key)
        if entry is not None:
            entry.expires_at = time.monotonic() + ttl

    # --8<-- [start:lock]
    @contextlib.asynccontextmanager
    async def lock(
        self,
        key: str,
        *,
        timeout: float = risa.DEFAULT_LOCK_TIMEOUT,
    ) -> collections.abc.AsyncGenerator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with asyncio.timeout(timeout):
                await lock.acquire()
        except TimeoutError as exc:
            raise risa.LockTimeoutError(key, timeout) from exc
        try:
            yield
        finally:
            lock.release()
            # --8<-- [end:lock]

    def _live(self, key: str) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is None or entry.expires_at is None:
            return entry
        if entry.expires_at > time.monotonic():
            return entry
        del self._entries[key]
        return None

    def _new_entry(self, value: bytes, ttl: float | None) -> _Entry:
        expires_at = None if ttl is None else time.monotonic() + ttl
        return _Entry(value, next(self._versions), expires_at)


# --8<-- [start:test]
async def test_dict_store() -> None:
    await verify_store(DictStore())
    # --8<-- [end:test]


@risa.register(name="tally", state=risa.InStore(ttl=3600))
class Tally(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.add, label=f"Count: {self.count}"))

    @risa.handler
    async def add(self, ctx: risa.ComponentContext) -> None:
        self.count += 1
        await ctx.rerender()


def main() -> None:
    bot = hikari.GatewayBot(
        os.environ["DISCORD_TOKEN"],
        intents=hikari.Intents.ALL_UNPRIVILEGED | hikari.Intents.MESSAGE_CONTENT,
    )
    # --8<-- [start:client]
    client = risa.client_from_app(bot, store=DictStore())
    client.add_view(Tally)
    # --8<-- [end:client]

    @bot.listen()
    async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
        if event.content == "!tally":
            rendered = await client.build(Tally())
            await rendered.send_to(bot.rest, event.channel_id)

    bot.run()


if __name__ == "__main__":
    main()
