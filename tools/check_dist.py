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
_WHEEL_NEEDS: typing.Final = ("risa/__init__.py", "risa/errors.py", "risa/py.typed")
# hatch skips what .gitignore names but not .git/info/exclude, so a local build can pack private files.
_SDIST_HOLDS: typing.Final = frozenset({".gitignore", "LICENSE", "PKG-INFO", "README.md", "pyproject.toml", "risa"})


def problems(dist: pathlib.Path) -> collections.abc.Sequence[str]:
    """Return what is wrong with the one wheel and the one sdist in ``dist``, or nothing."""
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        return [f"{dist} should hold one wheel and one sdist, not {[path.name for path in (*wheels, *sdists)]}"]
    found: list[str] = []
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    found.extend(f"{wheels[0].name} lacks {name}" for name in _WHEEL_NEEDS if name not in names)
    stray = {name.split("/")[0] for name in names} - {"risa"}
    found.extend(f"{wheels[0].name} holds {name}" for name in sorted(stray) if not name.endswith(".dist-info"))
    with tarfile.open(sdists[0]) as sdist:
        top = {name.split("/")[1] for name in sdist.getnames() if "/" in name}
    found.extend(f"{sdists[0].name} holds {name}" for name in sorted(top - _SDIST_HOLDS))
    return found


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
