from __future__ import annotations

import base64
import hashlib
import typing

__all__ = (
    "ALPHABET",
    "ALPHABET_SIZE",
    "MAX_DIGEST_CHARS",
    "largest_value",
    "pack_bytes",
    "pack_digest",
    "pack_uint",
    "unpack_bytes",
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
    value = 0
    for char in raw:
        digit = _INDEX.get(char)
        if digit is None:
            return None
        value = value * ALPHABET_SIZE + digit
    return value


def pack_bytes(data: bytes) -> str:
    return base64.b85encode(data).decode("ascii")


def unpack_bytes(raw: str) -> bytes | None:
    if len(raw) % 5 == 1:
        return None
    try:
        return base64.b85decode(raw)
    except ValueError:
        return None


def pack_digest(data: str, *, chars: int) -> str:
    if not 1 <= chars <= MAX_DIGEST_CHARS:
        msg = f"chars must be between 1 and {MAX_DIGEST_CHARS}, got {chars}"
        raise ValueError(msg)

    digest_size = -(-chars * 4 // 5)
    digest = hashlib.blake2s(data.encode(), digest_size=digest_size).digest()
    return base64.b85encode(digest).decode("ascii")[:chars]
