# Copyright (c) 2025-present Rayakame
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
r"""Turning values into ``custom_id`` characters, and back.

Everything risa writes into a ``custom_id`` passes through here:

* :func:`pack_bytes` renders arbitrary bytes, which is what carries packed
  state and encoded arguments.
* :func:`pack_uint` renders a small integer as a fixed number of characters,
  which is what carries lengths, indices and counters.
* :func:`pack_digest` renders a hash of some text, which is what carries the
  cookies and fingerprints identifying a view, a handler or a schema.
* :func:`split_fields` cuts a string back into the fixed-width fields a header
  is laid out as.

Every unpacking fails soft and returns ``None``, because everything it reads
came off a wire risa does not own: a ``custom_id`` written by another library
can be any string at all.

Why the alphabet is printable ASCII
-----------------------------------

Discord's limit is **100 Unicode code points** -- measured against the live API
in July 2026, not documented -- so Python's ``len`` is the correct check and a
character costs the same whatever it holds. Denser alphabets are therefore
available: raw bytes through ``latin-1`` would buy 25% more payload, and the
CJK block used as a digit supply would buy well over twice as much.

risa spends that density deliberately:

* ``custom_id``\ s surface in logs, in tracebacks, in Discord's own error
  bodies and in bug reports pasted by users. An id full of control characters
  or CJK is unreadable exactly when somebody needs to read it.
* Every character stays one byte in the request body, so a message carrying
  forty components does not quietly balloon under JSON escaping.
* The measurement covered the REST path. The path risa leans on hardest --
  reading other components' ids back out of ``interaction.message`` -- has not
  been measured, and ASCII is the choice that does not depend on the answer.

A denser encoding remains open as an opt-in later: an anchor's leading tag
already makes the format self-describing, and an unrecognised tag fails closed,
so a second encoding can be introduced without disturbing components already in
the wild.

Both alphabets exclude ``"`` and ``\\`` so that no JSON encoder ever has to
escape a ``custom_id``, and both are stable under every Unicode normalisation
form, so an id can be compared as the characters it arrived as. The base85
rendering is pure Python in the standard library and costs roughly 4 us per
100 bytes; base64 is twenty times faster but 8% less dense, and density is the
scarcer resource here.
"""

from __future__ import annotations

import base64
import hashlib
import typing

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = (
    "ALPHABET_SIZE",
    "largest_value",
    "pack_bytes",
    "pack_digest",
    "pack_uint",
    "split_fields",
    "unpack_bytes",
    "unpack_uint",
)

_ALPHABET: typing.Final[str] = "".join(chr(code) for code in range(33, 127) if chr(code) not in '"\\')

ALPHABET_SIZE: typing.Final[int] = len(_ALPHABET)
"""How many distinct values one packed character distinguishes."""

_VALUES: typing.Final[dict[str, int]] = {char: value for value, char in enumerate(_ALPHABET)}

# What base85 renders four bytes as.
_BYTES_PER_GROUP: typing.Final[int] = 4
_CHARS_PER_GROUP: typing.Final[int] = 5

# Precomputed for every width risa plausibly uses, since packing is on the
# rendering path of every component of every message.
_LARGEST: typing.Final[tuple[int, ...]] = tuple(ALPHABET_SIZE**width - 1 for width in range(9))


def largest_value(width: int) -> int:
    """Return the largest integer ``width`` characters can hold.

    Parameters
    ----------
    width
        How many characters the packed form occupies.

    Returns
    -------
    int
        The largest representable value.
    """
    if width < len(_LARGEST):
        return _LARGEST[width]
    return ALPHABET_SIZE**width - 1


def pack_uint(value: int, width: int) -> str:
    """Render a non-negative integer as exactly ``width`` characters.

    Parameters
    ----------
    value
        The integer to pack.
    width
        How many characters to use. Fixed rather than minimal, so that the
        reader can slice the field without a length prefix of its own.

    Returns
    -------
    str
        The packed characters, most significant first.

    Raises
    ------
    ValueError
        If ``value`` is negative or too large for ``width`` characters.
    """
    if value < 0:
        msg = f"cannot pack a negative value: {value}"
        raise ValueError(msg)
    if value > largest_value(width):
        msg = f"{value} does not fit in {width} characters (max {largest_value(width)})"
        raise ValueError(msg)

    if width == 1:
        return _ALPHABET[value]

    packed: list[str] = []
    remaining = value
    for _ in range(width):
        packed.append(_ALPHABET[remaining % ALPHABET_SIZE])
        remaining //= ALPHABET_SIZE
    return "".join(reversed(packed))


def unpack_uint(raw: str) -> int | None:
    """Read an integer back from its packed characters.

    Parameters
    ----------
    raw
        Exactly the characters :func:`pack_uint` produced.

    Returns
    -------
    int | None
        The integer, or ``None`` if any character is not from the alphabet.
    """
    if len(raw) == 1:
        return _VALUES.get(raw)

    value = 0
    for char in raw:
        digit = _VALUES.get(char)
        if digit is None:
            return None
        value = value * ALPHABET_SIZE + digit
    return value


def pack_bytes(data: bytes) -> str:
    """Render arbitrary bytes as ``custom_id`` characters.

    Parameters
    ----------
    data
        The bytes to render.

    Returns
    -------
    str
        The rendered characters, five per four bytes.
    """
    return base64.b85encode(data).decode("ascii")


def unpack_bytes(raw: str) -> bytes | None:
    """Read bytes back out of their rendered characters.

    Parameters
    ----------
    raw
        Exactly the characters :func:`pack_bytes` produced.

    Returns
    -------
    bytes | None
        The bytes, or ``None`` if ``raw`` is not something :func:`pack_bytes`
        produced.
    """
    try:
        return base64.b85decode(raw.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


def pack_digest(text: str, *, width: int) -> str:
    """Hash ``text`` down to exactly ``width`` ``custom_id`` characters.

    How every identity risa puts on the wire is derived -- view cookies,
    handler tokens, signature and schema fingerprints. They differ only in what
    they hash and how much collision each can afford, so they share this, and
    the caller states only how many characters it is willing to spend: enough
    hash to fill them is taken for it, so no caller can ask for more characters
    than it hashed.

    Parameters
    ----------
    text
        What to hash. Callers join their parts with ``:`` so the pieces cannot
        run together.
    width
        Characters to keep.

    Returns
    -------
    str
        Exactly ``width`` characters.
    """
    digest_size = -(-width * _BYTES_PER_GROUP // _CHARS_PER_GROUP)
    return pack_bytes(hashlib.blake2s(text.encode(), digest_size=digest_size).digest())[:width]


def split_fields(raw: str, widths: collections.abc.Sequence[int]) -> list[str]:
    """Cut a header into its fixed-width fields, in order.

    Short input yields short fields rather than raising: the caller is reading
    a string it did not write, and decides for itself what a truncated field
    means.

    Parameters
    ----------
    raw
        The string to cut.
    widths
        How wide each field is, in order.

    Returns
    -------
    list[str]
        One string per width.
    """
    fields: list[str] = []
    start = 0
    for width in widths:
        fields.append(raw[start : start + width])
        start += width
    return fields
