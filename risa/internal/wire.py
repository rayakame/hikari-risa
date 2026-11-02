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
"""The printable alphabet every id risa writes is spelled in.

`ALPHABET` is the 92 printable ASCII characters from `!` to `~` without the double
quote and the backslash, so ids survive JSON and logs unescaped. Integers are written in it as fixed-width
base-92 numbers, most significant digit first.
"""

from __future__ import annotations

import base64
import hashlib
import typing

__all__ = (
    "ALPHABET",
    "ALPHABET_SIZE",
    "MAX_DIGEST_CHARS",
    "largest_value",
    "pack_digest",
    "pack_uint",
    "unpack_uint",
)

ALPHABET: typing.Final[str] = "".join(chr(point) for point in range(33, 127) if chr(point) not in '"\\')
ALPHABET_SIZE: typing.Final[int] = len(ALPHABET)
MAX_DIGEST_CHARS: typing.Final[int] = 40

_INDEX: typing.Final[dict[str, int]] = {char: index for index, char in enumerate(ALPHABET)}


def largest_value(width: int) -> int:
    if width < 0:
        msg = f"width must be 0 or greater, got {width}"
        raise ValueError(msg)
    return ALPHABET_SIZE**width - 1


def pack_uint(value: int, width: int) -> str:
    """Write `value` as exactly `width` base-92 digits.

    Returns
    -------
    str
        The digits, most significant first.

    Raises
    ------
    ValueError
        If `value` is negative or needs more than `width` digits.
    """
    if value < 0:
        msg = f"cannot pack negative value {value}"
        raise ValueError(msg)
    if value > largest_value(width):
        msg = f"{value} does not fit in {width} character(s); the largest value is {largest_value(width)}"
        raise ValueError(msg)

    digits: list[str] = []
    for _ in range(width):
        value, digit = divmod(value, ALPHABET_SIZE)
        digits.append(ALPHABET[digit])
    return "".join(reversed(digits))


def unpack_uint(raw: str) -> int | None:
    """Read base-92 digits back into an integer, or return `None` if one is not in the alphabet."""
    value = 0
    for char in raw:
        digit = _INDEX.get(char)
        if digit is None:
            return None
        value = value * ALPHABET_SIZE + digit
    return value


def pack_digest(data: str, *, chars: int) -> str:
    """Hash `data` into `chars` characters of the alphabet.

    blake2s, cut to the bytes `chars` base85 characters need, then base85-encoded and cut
    again. base85's characters are a subset of the alphabet. Cookies, handler tokens and
    fingerprints are all such digests.

    Returns
    -------
    str
        The digest, `chars` characters long.

    Raises
    ------
    ValueError
        If `chars` is not between 1 and `MAX_DIGEST_CHARS`.
    """
    if not 1 <= chars <= MAX_DIGEST_CHARS:
        msg = f"chars must be between 1 and {MAX_DIGEST_CHARS}, got {chars}"
        raise ValueError(msg)

    digest_size = -(-chars * 4 // 5)
    digest = hashlib.blake2s(data.encode(), digest_size=digest_size).digest()
    return base64.b85encode(digest).decode("ascii")[:chars]
