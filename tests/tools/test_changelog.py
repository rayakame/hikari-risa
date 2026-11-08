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
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import sys

import pytest

from tools import changelog

_REPOSITORY = pathlib.Path(__file__).parents[2]
_MARKER = "<!-- towncrier release notes start -->"
_RELEASED = "## 1.0.0 (2026-11-08)\n\nThe first release.\n"
_KINDS = "breaking, deprecation, feature, bugfix"


def _root(tmp_path: pathlib.Path, *fragments: str) -> pathlib.Path:
    shutil.copyfile(_REPOSITORY / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n{_MARKER}\n\n{_RELEASED}")
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / ".gitkeep").write_text("")
    for name in fragments:
        (tmp_path / "changes" / name).write_text(f"The change of {name}.\n")
    return tmp_path


def test_well_named_fragments_pass(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "7.feature.md", "7.feature.1.md", "8.bugfix.md", "9.breaking.md", "10.deprecation.md")

    assert changelog.problems(root) == []


def test_a_fragment_of_an_unknown_kind_or_without_a_number_is_named(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "+orphan.feature.md", "12.feat.md")

    assert changelog.problems(root) == [
        f"changes/+orphan.feature.md is not named <pull request>.<kind>.md, with a kind of {_KINDS}",
        f"changes/12.feat.md is not named <pull request>.<kind>.md, with a kind of {_KINDS}",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Two\nlines.",
        "One paragraph.\r\n\r\nAnd a second one.",
        "- a list item",
        "1. an ordered one",
        "# A heading",
        "> A quote",
        "```python",
        "~~~",
        ": a definition",
        "[docs]: https://rayakame.github.io/hikari-risa/",
        "",
    ],
)
def test_a_fragment_that_is_not_one_line_of_text_is_named(tmp_path: pathlib.Path, text: str) -> None:
    root = _root(tmp_path)
    (root / "changes" / "7.feature.md").write_text(text)

    assert changelog.problems(root) == [
        "changes/7.feature.md should be one line of text, not a list item, quote, heading or fence"
    ]


@pytest.mark.parametrize(
    ("text", "local"),
    [
        ("See [the guide](../by-example/01-views.md).", True),
        ("See [`add_view`][risa.client.Client.add_view].", True),
        ("See [the guide](http://rayakame.github.io/hikari-risa/).", True),
        ("See [the guide](https://rayakame.github.io/hikari-risa/stable/).", False),
        ("Works with `rows[0][1]`, `list[int]()` and ``a](b``.", False),
    ],
)
def test_a_fragment_links_only_to_https_addresses(tmp_path: pathlib.Path, text: str, *, local: bool) -> None:
    root = _root(tmp_path)
    (root / "changes" / "7.feature.md").write_text(text)

    expected = ["changes/7.feature.md links to something other than an https:// address"] if local else []
    assert changelog.problems(root) == expected


def test_a_long_line_with_inline_markdown_and_other_scripts_passes(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    text = "`ctx.replace()` keeps *every* field " + "and more " * 20 + "for Ünïcödé views 🎉.\r\n"
    (root / "changes" / "7.feature.md").write_bytes(text.encode())

    assert changelog.problems(root) == []


@pytest.mark.parametrize(
    ("changed", "lacks"),
    [
        (["risa/view.py"], True),
        (["risa/view.py", "changes/70.feature.md"], True),
        (["risa/view.py", "changes/71.feature.md"], False),
        (["risa/view.py", "changes/71.bugfix.1.md"], False),
        (["risa/_about.py", "pyproject.toml", "CHANGELOG.md", "changes/7.feature.md"], False),
        (["docs/index.md", "uv.lock"], False),
    ],
)
def test_a_change_to_risa_needs_a_fragment_of_its_own(
    tmp_path: pathlib.Path, changed: list[str], *, lacks: bool
) -> None:
    found = changelog.problems(_root(tmp_path), pull_request=71, changed=changed)

    expected = (
        "this pull request changes risa/ but adds no changes/71.<kind>.md; add one, or give it the label"
        " skip-changelog if users see no difference"
    )
    assert found == ([expected] if lacks else [])


def test_the_command_line_fails_a_pull_request_without_its_fragment(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root = _root(tmp_path)
    (root / "changed").write_text("risa/über view.py\ndocs/index.md\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["changelog", "--pull-request", "71", "--changed", "changed"])

    with pytest.raises(SystemExit) as exited:
        changelog.main()

    assert exited.value.code == 1
    assert "adds no changes/71.<kind>.md" in caplog.text


def test_the_command_line_wants_the_pull_request_with_its_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_root(tmp_path))
    monkeypatch.setattr(sys, "argv", ["changelog", "--pull-request", "71"])

    with pytest.raises(SystemExit) as exited:
        changelog.main()

    assert exited.value.code == 2


@pytest.mark.parametrize(
    ("fragments", "heading", "expected"),
    [
        ((), "## 1.0.1 (2026-12-01)", []),
        (
            ("7.feature.md",),
            "## 1.0.1 (2026-12-01)",
            [
                (
                    "fragments still wait in changes/; move them into CHANGELOG.md with"
                    " towncrier build --version 1.0.1 --yes"
                )
            ],
        ),
        ((), "## 1.0.10 (2027-01-01)", ["CHANGELOG.md has no section for 1.0.1"]),
    ],
    ids=["ready", "fragments-wait", "no-section"],
)
def test_a_release_needs_its_section_and_no_waiting_fragments(
    tmp_path: pathlib.Path, fragments: tuple[str, ...], heading: str, expected: list[str]
) -> None:
    root = _root(tmp_path, *fragments)
    (root / "CHANGELOG.md").write_text(f"# Changelog\n\n{_MARKER}\n\n{heading}\n\nA fix.\n\n{_RELEASED}")

    assert changelog.release_problems(root, "1.0.1") == expected


def test_the_command_line_fails_a_release_without_its_section(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(_root(tmp_path))
    monkeypatch.setattr(sys, "argv", ["changelog", "--release", "1.1.0"])

    with pytest.raises(SystemExit) as exited:
        changelog.main()

    assert exited.value.code == 1
    assert "CHANGELOG.md has no section for 1.1.0" in caplog.text


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
    with pytest.raises(changelog.ChangelogError) as caught:
        changelog.page(_root(tmp_path, "12.feat.md"))

    assert caught.value.problems == [
        f"changes/12.feat.md is not named <pull request>.<kind>.md, with a kind of {_KINDS}"
    ]


def test_a_changelog_without_the_marker_stops_the_page(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    (root / "CHANGELOG.md").write_text(f"# Changelog\n\n{_RELEASED}")

    with pytest.raises(changelog.ChangelogError) as caught:
        changelog.page(root)

    assert caught.value.problems == [f"CHANGELOG.md lacks the line {_MARKER} that towncrier adds releases below"]


def test_a_failing_towncrier_passes_on_what_it_said(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "07.feature.md", "7.feature.md")

    with pytest.raises(changelog.ChangelogError) as caught:
        changelog.page(root)

    (problem,) = caught.value.problems
    prefix = "towncrier could not draft the unreleased changes: "
    assert problem.startswith(prefix)
    assert problem.removeprefix(prefix)


def test_a_release_build_puts_its_section_above_the_last_release(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, "7.feature.md")
    git = shutil.which("git")
    assert git is not None
    subprocess.run([git, "init", "-q"], cwd=root, check=True)  # ruff:ignore[subprocess-without-shell-equals-true]
    subprocess.run([git, "add", "."], cwd=root, check=True)  # ruff:ignore[subprocess-without-shell-equals-true]

    subprocess.run(
        [sys.executable, "-m", "towncrier", "build", "--version", "1.1.0", "--date", "2026-12-01", "--yes"],
        cwd=root,
        check=True,
        capture_output=True,
    )

    assert sorted(path.name for path in (root / "changes").iterdir()) == [".gitkeep"]
    assert changelog.page(root).startswith(
        "## 1.1.0 (2026-12-01)\n\n### Features\n\n"
        "- The change of 7.feature.md. ([#7](https://github.com/rayakame/hikari-risa/pull/7))\n\n\n"
        f"{_RELEASED}"
    )


def test_the_marker_comes_from_the_towncrier_settings(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path)
    settings = (root / "pyproject.toml").read_text().replace(_MARKER, "<!-- releases -->")
    (root / "pyproject.toml").write_text(settings)
    (root / "CHANGELOG.md").write_text(f"# Changelog\n\n<!-- releases -->\n\n{_RELEASED}")

    assert changelog.page(root) == _RELEASED


def test_the_changelog_of_the_repository_is_in_order() -> None:
    assert changelog.problems(_REPOSITORY) == []
    # Fragments of later pull requests may wait in changes/; the 1.0.0 section must stay.
    assert "CHANGELOG.md has no section for 1.0.0" not in changelog.release_problems(_REPOSITORY, "1.0.0")
    assert "## 1.0.0" in changelog.page(_REPOSITORY)
