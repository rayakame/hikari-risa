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
"""Checks the changelog fragments in ``changes/`` and renders the changelog page of the documentation."""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tomllib
import typing

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("FragmentNameError", "page", "problems")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.changelog")
_START: typing.Final = "<!-- towncrier release notes start -->"
_FRAGMENT: typing.Final = re.compile(r"(\d+)\.([a-z]+)\.md")
_SKIP_LABEL: typing.Final = "skip-changelog"


class FragmentNameError(Exception):
    """Raised when a file in ``changes/`` is not a fragment towncrier would read, since it would drop it silently."""

    def __init__(self, found: collections.abc.Sequence[str]) -> None:
        self.found = found
        super().__init__("\n".join(found))


def problems(root: pathlib.Path, changed: collections.abc.Sequence[str] | None = None) -> collections.abc.Sequence[str]:
    """Return what is wrong with the fragments in ``changes/``, or nothing.

    Parameters
    ----------
    root
        The repository root.
    changed
        The paths a pull request changes. If given, a change to ``risa/`` needs a fragment.

    Returns
    -------
    :
        One line for each problem.
    """
    kinds = _kinds(root)
    found = [
        f"changes/{path.name} is not named <pull request>.<kind>.md, with a kind of {', '.join(kinds)}"
        for path in _fragments(root)
        if not ((match := _FRAGMENT.fullmatch(path.name)) and match[2] in kinds)
    ]
    if changed is not None:
        touched = {path.split("/")[0] for path in changed}
        if "risa" in touched and "changes" not in touched:
            found.append(
                "this pull request changes risa/ but adds no fragment to changes/; add one, or give the pull"
                f" request the label {_SKIP_LABEL} if users see no difference"
            )
    return found


def page(root: pathlib.Path) -> str:
    """Return the changelog for the documentation: what waits in ``changes/``, if anything, then every release.

    Returns
    -------
    :
        The Markdown of the page below its title.

    Raises
    ------
    FragmentNameError
        If a file in ``changes/`` is not a fragment towncrier would read.
    """
    if found := problems(root):
        raise FragmentNameError(found)
    released = (root / "CHANGELOG.md").read_text(encoding="utf-8").split(_START, 1)[1].strip()
    if not _fragments(root):
        return released + "\n"
    draft = subprocess.run(
        [sys.executable, "-m", "towncrier", "build", "--draft", "--version", "Unreleased"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # towncrier's title carries a date, which an unreleased section does not have yet.
    _, _, body = draft.partition("\n")
    return f"## Unreleased\n{body.rstrip()}\n\n{released}\n"


def _kinds(root: pathlib.Path) -> collections.abc.Sequence[str]:
    with (root / "pyproject.toml").open("rb") as file:
        types = tomllib.load(file)["tool"]["towncrier"]["type"]
    return [kind["directory"] for kind in types]


def _fragments(root: pathlib.Path) -> collections.abc.Sequence[pathlib.Path]:
    directory = root / "changes"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if not path.name.startswith("."))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argument_parser = argparse.ArgumentParser(description="Check the changelog fragments in changes/.")
    argument_parser.add_argument(
        "--changed",
        type=pathlib.Path,
        help="a file listing the paths a pull request changes, one per line; a change to risa/ then needs a fragment",
    )
    arguments = argument_parser.parse_args()
    changed = None if arguments.changed is None else arguments.changed.read_text(encoding="utf-8").split()
    found = problems(pathlib.Path.cwd(), changed)
    for problem in found:
        _LOGGER.error(problem)
    if found:
        raise SystemExit(1)
    _LOGGER.info("the changelog fragments are in order")


if __name__ == "__main__":
    main()
