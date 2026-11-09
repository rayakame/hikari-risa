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

import importlib.metadata
import pathlib
import sys

import pytest

import risa
import risa.__main__


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    monkeypatch.setattr(sys, "argv", ["hikari-risa", *args])
    risa.__main__.main()
    return capsys.readouterr().out.splitlines()


def test_the_first_line_names_the_version_and_where_risa_is(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _run(monkeypatch, capsys)

    assert lines[0] == f"hikari-risa {risa.__version__} at {pathlib.Path(risa.__file__).parent}"


def test_each_dependency_shows_its_installed_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _run(monkeypatch, capsys)

    for name in ("hikari", "linkd", "msgspec"):
        assert f"{name} {importlib.metadata.version(name)}" in lines


def test_an_extra_names_itself_and_says_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed = importlib.metadata.version

    def version(name: str) -> str:
        if name == "radixly":
            raise importlib.metadata.PackageNotFoundError(name)
        return installed(name)

    monkeypatch.setattr(importlib.metadata, "version", version)
    lines = _run(monkeypatch, capsys)

    assert "radixly not installed (radixly extra)" in lines
    assert f"redis {installed('redis')} (redis extra)" in lines


def test_version_prints_only_the_version(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, capsys, "--version")

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"hikari-risa {risa.__version__}\n"


def test_the_command_is_installed_as_hikari_risa() -> None:
    commands = importlib.metadata.entry_points(group="console_scripts", name="hikari-risa")

    assert [command.value for command in commands] == ["risa.__main__:main"]
