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


class VersionedEntry(msgspec.Struct, frozen=True):
    data: bytes
    version: int


class Store(typing.Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def get_versioned(self, key: str) -> VersionedEntry | None: ...
    async def put(self, key: str, value: bytes, *, ttl: float | None) -> None: ...
    async def put_if_version(self, key: str, value: bytes, *, expected: int, ttl: float | None) -> bool: ...
    async def delete(self, key: str) -> None: ...
    async def touch(self, key: str, *, ttl: float) -> None: ...
    def lock(
        self, key: str, *, timeout: float = DEFAULT_LOCK_TIMEOUT
    ) -> contextlib.AbstractAsyncContextManager[None]: ...
