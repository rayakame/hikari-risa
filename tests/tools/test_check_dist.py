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

import io
import tarfile
import typing
import zipfile

from tools import check_dist

if typing.TYPE_CHECKING:
    import collections.abc
    import pathlib


def _wheel(dist: pathlib.Path, names: collections.abc.Sequence[str]) -> None:
    with zipfile.ZipFile(dist / "hikari_risa-1.0.0-py3-none-any.whl", "w") as wheel:
        for name in names:
            wheel.writestr(name, "")


def _sdist(dist: pathlib.Path, names: collections.abc.Sequence[str]) -> None:
    with tarfile.open(dist / "hikari_risa-1.0.0.tar.gz", "w:gz") as sdist:
        for name in names:
            sdist.addfile(tarfile.TarInfo(f"hikari_risa-1.0.0/{name}"), io.BytesIO())


_GOOD_WHEEL: typing.Final = (
    "risa/__init__.py",
    "risa/errors.py",
    "risa/py.typed",
    "hikari_risa-1.0.0.dist-info/METADATA",
)
_GOOD_SDIST: typing.Final = ("PKG-INFO", "pyproject.toml", "README.md", "LICENSE", ".gitignore", "risa/__init__.py")


def test_a_wheel_and_an_sdist_holding_the_package_pass(tmp_path: pathlib.Path) -> None:
    _wheel(tmp_path, _GOOD_WHEEL)
    _sdist(tmp_path, _GOOD_SDIST)

    assert check_dist.problems(tmp_path) == []


def test_a_private_file_in_the_sdist_is_named(tmp_path: pathlib.Path) -> None:
    _wheel(tmp_path, _GOOD_WHEEL)
    _sdist(tmp_path, (*_GOOD_SDIST, ".notes/HANDOFF.md", "CLAUDE.md"))

    assert check_dist.problems(tmp_path) == [
        "hikari_risa-1.0.0.tar.gz holds .notes",
        "hikari_risa-1.0.0.tar.gz holds CLAUDE.md",
    ]


def test_a_wheel_without_the_typing_marker_or_with_a_stray_package_is_named(tmp_path: pathlib.Path) -> None:
    _wheel(tmp_path, (*[name for name in _GOOD_WHEEL if name != "risa/py.typed"], "tests/__init__.py"))
    _sdist(tmp_path, _GOOD_SDIST)

    assert check_dist.problems(tmp_path) == [
        "hikari_risa-1.0.0-py3-none-any.whl lacks risa/py.typed",
        "hikari_risa-1.0.0-py3-none-any.whl holds tests",
    ]


def test_a_missing_sdist_is_named(tmp_path: pathlib.Path) -> None:
    _wheel(tmp_path, _GOOD_WHEEL)

    assert check_dist.problems(tmp_path) == [
        f"{tmp_path} should hold one wheel and one sdist, not ['hikari_risa-1.0.0-py3-none-any.whl']"
    ]
