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
import io
import os
import pathlib
import sys
import typing

import pytest

import risa
import risa.__main__


class _Terminal(io.StringIO):
    @typing.override
    def isatty(self) -> bool:
        return True


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str) -> list[str]:
    monkeypatch.setattr(sys, "argv", ["hikari-risa", *args])
    risa.__main__.main()
    return capsys.readouterr().out.splitlines()


def test_the_report_names_the_version_and_where_risa_is(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = _run(monkeypatch, capsys)

    assert any(line.endswith(f"hikari-risa {risa.__version__}") for line in lines)
    assert ["location", str(pathlib.Path(risa.__file__).parent)] in [line.split(None, 1) for line in lines]


def test_each_dependency_shows_its_installed_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [line.split() for line in _run(monkeypatch, capsys)]

    for name in ("hikari", "linkd", "msgspec"):
        assert [name, importlib.metadata.version(name)] in [row[-2:] for row in rows]


def test_an_extra_names_itself_and_says_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installed = importlib.metadata.version

    def version(name: str) -> str:
        if name == "radixly":
            raise importlib.metadata.PackageNotFoundError(name)
        return installed(name)

    monkeypatch.setattr(importlib.metadata, "version", version)
    rows = [line.split() for line in _run(monkeypatch, capsys)]

    assert ["radixly", "radixly", "not", "installed"] in [row[-4:] for row in rows]
    assert ["redis", "redis", installed("redis")] in [row[-3:] for row in rows]


def test_a_pipe_gets_no_colour(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")

    assert not any("\x1b[" in line for line in _run(monkeypatch, capsys))


@pytest.mark.parametrize(("no_color", "coloured"), [(None, True), ("1", False)])
def test_a_terminal_gets_colour_unless_no_color_is_set(
    monkeypatch: pytest.MonkeyPatch, no_color: str | None, *, coloured: bool
) -> None:
    terminal = _Terminal()
    monkeypatch.setattr(sys, "argv", ["hikari-risa"])
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setenv("TERM", "xterm-256color")
    if no_color is None:
        monkeypatch.delenv("NO_COLOR", raising=False)
    else:
        monkeypatch.setenv("NO_COLOR", no_color)

    risa.__main__.main()

    assert ("\x1b[" in terminal.getvalue()) is (coloured and os.name != "nt")


def test_a_character_the_output_cannot_encode_is_escaped(monkeypatch: pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "argv", ["hikari-risa"])
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="ascii"))
    monkeypatch.setattr(risa, "__file__", "/home/j\u00fcrgen/risa/__init__.py")

    risa.__main__.main()
    sys.stdout.flush()

    assert ["location", "/home/j\\xfcrgen/risa"] in [
        line.split(None, 1) for line in buffer.getvalue().decode().splitlines()
    ]


def test_version_prints_only_the_version(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        _run(monkeypatch, capsys, "--version")

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"hikari-risa {risa.__version__}\n"


def test_the_command_is_installed_as_hikari_risa() -> None:
    commands = importlib.metadata.entry_points(group="console_scripts", name="hikari-risa")

    assert [command.value for command in commands] == ["risa.__main__:main"]
