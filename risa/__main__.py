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
import pathlib
import platform
import re
import sys
import typing

import risa

__all__ = ("main",)

_EXTRA: typing.Final[re.Pattern[str]] = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")


def main() -> None:
    """Print the versions of risa, of Python and of the packages risa installs."""
    parser = argparse.ArgumentParser(
        prog="hikari-risa", description="Print the versions of risa, Python and what risa installs, for a bug report."
    )
    parser.add_argument("--version", action="version", version=f"hikari-risa {risa.__version__}")
    parser.parse_args()

    lines = [
        f"hikari-risa {risa.__version__} at {pathlib.Path(risa.__file__).parent}",
        f"{platform.python_implementation()} {platform.python_version()} on {platform.platform()}",
    ]
    for requirement in importlib.metadata.requires("hikari-risa") or ():
        name = re.split(r"[^\w.-]", requirement, maxsplit=1)[0]
        try:
            line = f"{name} {importlib.metadata.version(name)}"
        except importlib.metadata.PackageNotFoundError:
            line = f"{name} not installed"
        if extra := _EXTRA.search(requirement):
            line += f" ({extra[1]} extra)"
        lines.append(line)
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
