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
"""Checks the built wheel and sdist before they are uploaded."""

from __future__ import annotations

import argparse
import logging
import pathlib
import tarfile
import typing
import zipfile

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("problems",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.check_dist")
_PACKAGE_NEEDS: typing.Final = ("risa/__init__.py", "risa/errors.py", "risa/py.typed")
_SDIST_NEEDS: typing.Final = ("LICENSE", "PKG-INFO", "README.md", "pyproject.toml", *_PACKAGE_NEEDS)
# hatch skips what .gitignore names but not .git/info/exclude, so a local build can pack private files.
_SDIST_MAY_HOLD: typing.Final = frozenset({*_SDIST_NEEDS, ".gitignore"})


def problems(dist: pathlib.Path) -> collections.abc.Sequence[str]:
    """Return what is wrong with the one wheel and the one sdist in ``dist``, or nothing."""
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        return [f"{dist} should hold one wheel and one sdist, not {[path.name for path in (*wheels, *sdists)]}"]
    metadata = "-".join(wheels[0].name.split("-")[:2]) + ".dist-info/"
    with zipfile.ZipFile(wheels[0]) as wheel:
        in_wheel = [name for name in wheel.namelist() if not name.endswith("/") and not name.startswith(metadata)]
    root = sdists[0].name.removesuffix(".tar.gz") + "/"
    with tarfile.open(sdists[0]) as sdist:
        in_sdist = [member.name.removeprefix(root) for member in sdist.getmembers() if member.isfile()]
    return [
        *_compare(wheels[0].name, in_wheel, _PACKAGE_NEEDS, frozenset()),
        *_compare(sdists[0].name, in_sdist, _SDIST_NEEDS, _SDIST_MAY_HOLD),
    ]


def _compare(
    archive: str,
    files: collections.abc.Sequence[str],
    needs: collections.abc.Sequence[str],
    may_hold: frozenset[str],
) -> list[str]:
    present = set(files)
    found = [f"{archive} lacks {name}" for name in needs if name not in present]
    found.extend(f"{archive} holds {name}" for name in sorted(present - may_hold) if not _in_package(name))
    return found


def _in_package(name: str) -> bool:
    return name.startswith("risa/") and (name.endswith(".py") or name == "risa/py.typed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argument_parser = argparse.ArgumentParser(description="Check the built wheel and sdist before they are uploaded.")
    argument_parser.add_argument("dist", type=pathlib.Path)
    dist = argument_parser.parse_args().dist
    found = problems(dist)
    for problem in found:
        _LOGGER.error(problem)
    if found:
        raise SystemExit(1)
    _LOGGER.info("%s holds a wheel and an sdist with what they should", dist)


if __name__ == "__main__":
    main()
