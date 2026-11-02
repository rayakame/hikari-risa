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
"""How `InMessage` state is written into `custom_id` characters.

`BASE85`, the default, is built in and keeps every character printable ASCII.
`BASE32768` and `URO14` come with the `[radixly]` extra and carry more than twice as
many bytes per character.
"""

from __future__ import annotations

import base64
import functools
import importlib
import typing

import msgspec

from risa import errors
from risa.internal import wire

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "BASE85",
    "BASE32768",
    "RADIXLY_EXTRA",
    "URO14",
    "Codec",
    "by_tag",
    "ensure_available",
    "register",
)

RADIXLY_EXTRA: typing.Final[str] = "radixly"


class _RadixlyModule(typing.Protocol):
    def encode(self, data: bytes, /) -> str: ...

    def decode(self, text: str, /) -> bytes: ...

    def max_bytes(self, chars: int, /) -> int: ...


class Codec(msgspec.Struct, frozen=True):
    """A way of writing bytes as `custom_id` characters.

    Attributes
    ----------
    tag
        One character, written ahead of the state, that names the codec on the wire.
    name
        A human-readable name for messages and logs.
    encode
        Turns bytes into text.
    decode
        Turns that text back into the same bytes.
    max_bytes
        How many bytes fit into a given number of characters.
    """

    tag: str
    name: str
    encode: collections.abc.Callable[[bytes], str]
    decode: collections.abc.Callable[[str], bytes]
    max_bytes: collections.abc.Callable[[int], int]

    def __post_init__(self) -> None:
        if len(self.tag) != 1 or self.tag not in wire.ALPHABET:
            msg = f"a codec tag is exactly one character of the wire alphabet, got {self.tag!r}"
            raise ValueError(msg)
        if not self.name:
            msg = "a codec needs a name"
            raise ValueError(msg)


def _b85_encode(data: bytes) -> str:
    return base64.b85encode(data).decode("ascii")


def _b85_decode(text: str) -> bytes:
    if len(text) % 5 == 1:
        msg = f"{len(text)} characters is not a base85 length"
        raise ValueError(msg)
    return base64.b85decode(text)


def _b85_max_bytes(chars: int) -> int:
    return chars // 5 * 4 + max(0, chars % 5 - 1)


def _radixly_module(name: str) -> _RadixlyModule:
    try:
        module = importlib.import_module(f"radixly.{name}")
    except ImportError as exc:
        raise errors.CodecUnavailableError(name, RADIXLY_EXTRA) from exc
    return typing.cast("_RadixlyModule", module)


def _radixly_encode(name: str, data: bytes) -> str:
    return _radixly_module(name).encode(data)


def _radixly_decode(name: str, text: str) -> bytes:
    return _radixly_module(name).decode(text)


def _radixly_max_bytes(name: str, chars: int) -> int:
    return _radixly_module(name).max_bytes(chars)


def _radixly(tag: str, name: str) -> Codec:
    return Codec(
        tag=tag,
        name=name,
        encode=functools.partial(_radixly_encode, name),
        decode=functools.partial(_radixly_decode, name),
        max_bytes=functools.partial(_radixly_max_bytes, name),
    )


BASE85: typing.Final[Codec] = Codec(
    tag="a", name="base85", encode=_b85_encode, decode=_b85_decode, max_bytes=_b85_max_bytes
)
BASE32768: typing.Final[Codec] = _radixly("w", "base32768")
URO14: typing.Final[Codec] = _radixly("u", "uro14")

_REGISTRY: typing.Final[dict[str, Codec]] = {c.tag: c for c in (BASE85, BASE32768, URO14)}


def register(codec: Codec) -> None:
    """Make `codec` known under its tag, so state written with it can be read back.

    Raises
    ------
    ValueError
        If a different codec already has the tag.
    """
    existing = _REGISTRY.get(codec.tag)
    if existing is None:
        _REGISTRY[codec.tag] = codec
    elif existing != codec:
        msg = f"codec tag {codec.tag!r} already belongs to {existing.name!r}"
        raise ValueError(msg)


def by_tag(tag: str) -> Codec | None:
    """Return the codec registered under `tag`, or `None`."""
    return _REGISTRY.get(tag)


def ensure_available(codec: Codec) -> None:
    """Raise `CodecUnavailableError` if `codec` needs an extra that is not installed."""
    codec.max_bytes(1)
