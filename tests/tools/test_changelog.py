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

import pathlib
import shutil

import pytest

from tools import changelog

_REPOSITORY = pathlib.Path(__file__).parents[2]
_RELEASED = "## 1.0.0 (2026-11-08)\n\nThe first release.\n"


def _root(tmp_path: pathlib.Path, *fragments: str) -> pathlib.Path:
    shutil.copyfile(_REPOSITORY / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n<!-- towncrier release notes start -->\n\n{_RELEASED}")
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / ".gitkeep").write_text("")
    for name in fragments:
        (tmp_path / "changes" / name).write_text(f"The change of {name}.\n")
    return tmp_path


def test_well_named_fragments_pass(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "7.feature.md", "8.bugfix.md", "9.breaking.md", "10.deprecation.md")

    assert changelog.problems(root) == []


def test_a_fragment_of_an_unknown_kind_or_without_a_number_is_named(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "+orphan.feature.md", "12.feat.md")

    kinds = "breaking, deprecation, feature, bugfix"
    assert changelog.problems(root) == [
        f"changes/+orphan.feature.md is not named <pull request>.<kind>.md, with a kind of {kinds}",
        f"changes/12.feat.md is not named <pull request>.<kind>.md, with a kind of {kinds}",
    ]


@pytest.mark.parametrize(
    ("changed", "lacks"),
    [
        (["risa/view.py"], True),
        (["risa/view.py", "changes/5.bugfix.md"], False),
        (["docs/index.md", "uv.lock"], False),
        ([], False),
    ],
)
def test_a_change_to_risa_needs_a_fragment(tmp_path: pathlib.Path, changed: list[str], *, lacks: bool) -> None:
    found = changelog.problems(_root(tmp_path), changed)

    assert bool(found) is lacks
    assert all("skip-changelog" in problem for problem in found)


def test_the_page_lists_waiting_fragments_above_the_releases(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "7.feature.md", "8.bugfix.md")

    assert changelog.page(root) == (
        "## Unreleased\n\n### Features\n\n"
        "- The change of 7.feature.md. ([#7](https://github.com/rayakame/hikari-risa/pull/7))\n\n"
        "### Bug fixes\n\n"
        "- The change of 8.bugfix.md. ([#8](https://github.com/rayakame/hikari-risa/pull/8))\n\n"
        f"{_RELEASED}"
    )


def test_the_page_without_fragments_is_the_releases(tmp_path: pathlib.Path) -> None:
    assert changelog.page(_root(tmp_path)) == _RELEASED


def test_a_badly_named_fragment_stops_the_page(tmp_path: pathlib.Path) -> None:
    with pytest.raises(changelog.FragmentNameError) as caught:
        changelog.page(_root(tmp_path, "12.feat.md"))

    kinds = "breaking, deprecation, feature, bugfix"
    assert caught.value.found == [f"changes/12.feat.md is not named <pull request>.<kind>.md, with a kind of {kinds}"]


def test_the_changelog_of_the_repository_is_in_order() -> None:
    assert changelog.problems(_REPOSITORY) == []
    assert "## 1.0.0" in changelog.page(_REPOSITORY)
