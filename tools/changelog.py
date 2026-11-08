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

__all__ = ("ChangelogError", "page", "problems")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.changelog")
_FRAGMENT: typing.Final = re.compile(r"(\d+)\.([a-z]+)(?:\.\d+)?\.md")
# towncrier indents an entry's later lines by two spaces, too few for Python-Markdown to keep a second block inside it.
_BLOCK: typing.Final = re.compile(r"^[ \t]*$|^[ \t]*(?:[-*+]\s|#{1,6}\s|\d+[.)]\s|>|```|~~~)", re.MULTILINE)
_LOCAL_LINK: typing.Final = re.compile(r"\]\((?!https?://)|\]\[")
_SKIP_LABEL: typing.Final = "skip-changelog"
_VERSION_FILE: typing.Final = "risa/_about.py"


class ChangelogError(Exception):
    """Raised when the changelog page cannot be rendered."""

    def __init__(self, problems: collections.abc.Sequence[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


def problems(
    root: pathlib.Path,
    *,
    pull_request: int | None = None,
    changed: collections.abc.Sequence[str] = (),
) -> collections.abc.Sequence[str]:
    """Return what is wrong with the fragments in ``changes/``, or nothing.

    Parameters
    ----------
    root
        The repository root.
    pull_request
        The pull request that changes ``changed``. If that reaches into ``risa/`` beyond the
        version, the pull request needs a fragment named after it.
    changed
        The paths the pull request changes.

    Returns
    -------
    :
        One line for each problem.
    """
    kinds = _kinds(root)
    found: list[str] = []
    for path in _fragments(root):
        match = _FRAGMENT.fullmatch(path.name)
        if match is None or match[2] not in kinds:
            found.append(
                f"changes/{path.name} is not named <pull request>.<kind>.md, with a kind of {', '.join(kinds)}"
            )
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text or _BLOCK.search(text):
            found.append(
                f"changes/{path.name} should be one paragraph, with no blank line, list, quote, heading or fence"
            )
        if _LOCAL_LINK.search(text):
            found.append(f"changes/{path.name} links to something other than an https:// address")
    if pull_request is not None:
        own = [
            path
            for path in changed
            if path.startswith("changes/")
            and (match := _FRAGMENT.fullmatch(path.removeprefix("changes/")))
            and match[1] == str(pull_request)
        ]
        if not own and any(path.startswith("risa/") and path != _VERSION_FILE for path in changed):
            found.append(
                f"this pull request changes risa/ but adds no changes/{pull_request}.<kind>.md; add one, or give it"
                f" the label {_SKIP_LABEL} if users see no difference"
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
    ChangelogError
        If a fragment breaks a rule of ``problems``, ``CHANGELOG.md`` lacks towncrier's
        marker, or towncrier fails.
    """
    if found := problems(root):
        raise ChangelogError(found)
    marker = _marker(root)
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if marker not in text:
        raise ChangelogError([f"CHANGELOG.md lacks the line {marker} that towncrier adds releases below"])
    released = text.split(marker, 1)[1].strip()
    if not _fragments(root):
        return released + "\n"
    done = subprocess.run(
        [sys.executable, "-m", "towncrier", "build", "--draft", "--version", "Unreleased"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise ChangelogError([f"towncrier could not draft the unreleased changes: {done.stderr.strip()}"])
    # towncrier's title carries a date, which an unreleased section does not have yet.
    _, _, body = done.stdout.strip().partition("\n")
    return f"## Unreleased\n{body.rstrip()}\n\n{released}\n"


def _kinds(root: pathlib.Path) -> collections.abc.Sequence[str]:
    with (root / "pyproject.toml").open("rb") as file:
        types = tomllib.load(file)["tool"]["towncrier"]["type"]
    return [kind["directory"] for kind in types]


def _marker(root: pathlib.Path) -> str:
    with (root / "pyproject.toml").open("rb") as file:
        return str(tomllib.load(file)["tool"]["towncrier"]["start_string"]).strip()


def _fragments(root: pathlib.Path) -> collections.abc.Sequence[pathlib.Path]:
    directory = root / "changes"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if not path.name.startswith("."))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argument_parser = argparse.ArgumentParser(description="Check the changelog fragments in changes/.")
    argument_parser.add_argument("--pull-request", type=int, help="the pull request whose changes --changed lists")
    argument_parser.add_argument(
        "--changed", type=pathlib.Path, help="a file listing the paths the pull request changes, one per line"
    )
    arguments = argument_parser.parse_args()
    if (arguments.pull_request is None) != (arguments.changed is None):
        argument_parser.error("--pull-request and --changed go together")
    changed: list[str] = [] if arguments.changed is None else arguments.changed.read_text(encoding="utf-8").split()
    found = problems(pathlib.Path.cwd(), pull_request=arguments.pull_request, changed=changed)
    for problem in found:
        _LOGGER.error(problem)
    if found:
        raise SystemExit(1)
    _LOGGER.info("the changelog fragments are in order")


if __name__ == "__main__":
    main()
