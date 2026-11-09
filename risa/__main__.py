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
"""The ``hikari-risa`` command, also ``python -m risa``: the versions a bug report needs."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import pathlib
import platform
import re
import sys
import typing

import risa

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("main",)

_ART: typing.Final[collections.abc.Sequence[str]] = (
    r"         _           ",
    r"   _____(_)________ _",
    r"  / ___/ / ___/ __ `/",
    r" / /  / (__  ) /_/ / ",
    r"/_/  /_/____/\__,_/  ",
)
# 256-colour codes that read on dark and light terminals.
_ART_COLOURS: typing.Final[collections.abc.Sequence[str]] = ("38;5;205", "38;5;206", "38;5;170", "38;5;134", "38;5;98")
_PINK: typing.Final[str] = "38;5;205"
_BOLD: typing.Final[str] = "1"
_DIM: typing.Final[str] = "2"
_LABEL_WIDTH: typing.Final[int] = 10
_EXTRA: typing.Final[re.Pattern[str]] = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")

type _Paint = collections.abc.Callable[[str, str], str]


def _section(
    paint: _Paint, label: str, rows: collections.abc.Sequence[collections.abc.Sequence[str]]
) -> collections.abc.Iterator[str]:
    # The last column goes unpadded, so it may carry colour codes without breaking the alignment.
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]) - 1)] if rows else []
    for index, row in enumerate(rows):
        cells = [cell.ljust(width) for cell, width in zip(row, widths, strict=False)]
        heading = paint((label if index == 0 else "").ljust(_LABEL_WIDTH), _PINK)
        yield f"  {heading}{'  '.join([*cells, row[-1]])}"


def _report(*, colour: bool) -> collections.abc.Iterator[str]:
    def paint(text: str, style: str) -> str:
        return f"\x1b[{style}m{text}\x1b[0m" if colour else text

    for line, style in zip(_ART[:-1], _ART_COLOURS, strict=False):
        yield paint(line.rstrip(), style)
    yield paint(_ART[-1], _ART_COLOURS[-1]) + paint(f"hikari-risa {risa.__version__}", _BOLD)

    metadata = importlib.metadata.metadata("hikari-risa")
    urls = dict(url.split(", ", 1) for url in metadata.get_all("Project-URL") or ())
    required: list[collections.abc.Sequence[str]] = []
    extras: list[collections.abc.Sequence[str]] = []
    for requirement in importlib.metadata.requires("hikari-risa") or ():
        name = re.split(r"[^\w.-]", requirement, maxsplit=1)[0]
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = paint("not installed", _DIM)
        if extra := _EXTRA.search(requirement):
            extras.append((extra[1], name, version))
        else:
            required.append((name, version))

    yield ""
    yield paint(metadata["Summary"], _DIM)
    yield ""
    yield from _section(paint, "python", [(f"{platform.python_implementation()} {platform.python_version()}",)])
    yield from _section(paint, "platform", [(platform.platform(),)])
    yield from _section(paint, "location", [(str(pathlib.Path(risa.__file__).parent),)])
    yield ""
    yield from _section(paint, "requires", required)
    yield ""
    yield from _section(paint, "extras", extras)
    yield ""
    yield from _section(paint, "docs", [(urls["Documentation"],)])
    yield from _section(paint, "issues", [(urls["Issues"],)])


def main() -> None:
    """Print the versions of risa, of Python and of the packages risa installs."""
    parser = argparse.ArgumentParser(
        prog="hikari-risa", description="Print the versions of risa, Python and what risa installs, for a bug report."
    )
    parser.add_argument("--version", action="version", version=f"hikari-risa {risa.__version__}")
    parser.parse_args()
    # The classic Windows console prints escape codes as text.
    colour = (
        sys.stdout.isatty() and os.name != "nt" and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    )
    report = "\n".join(_report(colour=colour)) + "\n"
    # Before 3.15 a Windows pipe writes in the ANSI code page, which may lack a character of the path.
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.write(report.encode(encoding, "backslashreplace").decode(encoding))


if __name__ == "__main__":
    main()
