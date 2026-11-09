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

import json
import sys
import typing

import pytest

from tools.docs import publish

if typing.TYPE_CHECKING:
    import pathlib


def _site(tmp_path: pathlib.Path, marker: str) -> pathlib.Path:
    site = tmp_path / f"site-{marker}"
    site.mkdir()
    (site / "index.html").write_text(marker)
    (site / "404.html").write_text(f"404-{marker}")
    return site


def _versions(target: pathlib.Path) -> list[dict[str, object]]:
    return json.loads((target / "versions.json").read_text())


def test_main_goes_to_its_own_folder_and_the_root_points_at_it_before_any_release(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()

    publish.publish(_site(tmp_path, "m"), target, version="main")

    assert (target / "main" / "index.html").read_text() == "m"
    assert (target / ".nojekyll").exists()
    assert 'content="0; url=main/"' in (target / "index.html").read_text()
    assert not (target / "stable").exists()
    assert _versions(target) == [{"version": "main", "title": "main", "aliases": ["stable"]}]


def test_the_first_release_takes_the_stable_alias_away_from_main(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "m"), target, version="main")

    publish.publish(_site(tmp_path, "r"), target, version="v0.1.0")

    assert _versions(target) == [
        {"version": "0.1.0", "title": "0.1.0", "aliases": ["stable"]},
        {"version": "main", "title": "main", "aliases": []},
    ]


def test_the_root_serves_the_404_of_the_default_version(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()

    publish.publish(_site(tmp_path, "m"), target, version="main")
    assert (target / "404.html").read_text() == "404-m"

    publish.publish(_site(tmp_path, "r"), target, version="v0.1.0")
    assert (target / "404.html").read_text() == "404-r"

    publish.publish(_site(tmp_path, "m2"), target, version="main")
    assert (target / "404.html").read_text() == "404-r"


def test_the_root_carries_the_link_preview_of_the_default_version(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    preview = (
        '<meta name="description" content="{0}">\n<meta property="og:title" content="{0}">\n'
        '<meta name="twitter:card" content="summary_large_image">'
    )
    main = _site(tmp_path, "m")
    (main / "index.html").write_text(f'<head>{preview.format("main")}<meta name="viewport" content="x"></head>')
    release = _site(tmp_path, "r")
    (release / "index.html").write_text(f"<head>{preview.format('release')}</head>")

    publish.publish(main, target, version="main")
    assert preview.format("main") in (target / "index.html").read_text()
    assert "viewport" not in (target / "index.html").read_text()

    publish.publish(release, target, version="v0.1.0")
    assert preview.format("release") in (target / "index.html").read_text()
    assert preview.format("main") not in (target / "index.html").read_text()


def test_a_release_becomes_stable_and_the_root_points_at_stable(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()

    publish.publish(_site(tmp_path, "r"), target, version="v0.2.1")

    assert (target / "0.2.1" / "index.html").read_text() == "r"
    assert (target / "stable" / "index.html").read_text() == "r"
    assert 'content="0; url=stable/"' in (target / "index.html").read_text()
    assert _versions(target) == [{"version": "0.2.1", "title": "0.2.1", "aliases": ["stable"]}]


def test_a_patch_release_gets_a_folder_of_its_own_and_replaces_stable(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "old"), target, version="v0.2.0")
    (target / "stable" / "stale.html").write_text("stale")

    publish.publish(_site(tmp_path, "new"), target, version="v0.2.1")

    assert (target / "0.2.0" / "index.html").read_text() == "old"
    assert (target / "0.2.1" / "index.html").read_text() == "new"
    assert (target / "stable" / "index.html").read_text() == "new"
    assert not (target / "stable" / "stale.html").exists()
    assert [entry["version"] for entry in _versions(target)] == ["0.2.1", "0.2.0"]


def test_an_older_patch_published_later_does_not_take_stable(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "newer"), target, version="v0.2.10")

    publish.publish(_site(tmp_path, "older"), target, version="v0.2.9")

    assert (target / "0.2.9" / "index.html").read_text() == "older"
    assert (target / "stable" / "index.html").read_text() == "newer"
    assert [entry["version"] for entry in _versions(target)] == ["0.2.10", "0.2.9"]


def test_republishing_the_same_patch_replaces_its_folder(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "first"), target, version="v0.2.3")
    (target / "0.2.3" / "stale.html").write_text("stale")

    publish.publish(_site(tmp_path, "again"), target, version="v0.2.3")

    assert (target / "0.2.3" / "index.html").read_text() == "again"
    assert not (target / "0.2.3" / "stale.html").exists()
    assert (target / "stable" / "index.html").read_text() == "again"


def test_an_older_minor_published_later_does_not_take_stable(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "ten"), target, version="v0.10.0")

    publish.publish(_site(tmp_path, "nine"), target, version="v0.9.5")

    assert (target / "stable" / "index.html").read_text() == "ten"
    assert (target / "0.9.5" / "index.html").read_text() == "nine"
    assert _versions(target) == [
        {"version": "0.10.0", "title": "0.10.0", "aliases": ["stable"]},
        {"version": "0.9.5", "title": "0.9.5", "aliases": []},
    ]


def test_versions_json_lists_releases_newest_first_then_main(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "a"), target, version="main")
    publish.publish(_site(tmp_path, "b"), target, version="v0.1.0")
    publish.publish(_site(tmp_path, "c"), target, version="v0.2.0")

    assert [entry["version"] for entry in _versions(target)] == ["0.2.0", "0.1.0", "main"]
    assert _versions(target)[0]["aliases"] == ["stable"]


def test_republishing_main_replaces_only_main(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "r"), target, version="v0.1.0")
    publish.publish(_site(tmp_path, "m1"), target, version="main")
    (target / "main" / "stale.html").write_text("stale")
    marker = target / "stable" / "marker"
    marker.write_text("kept")

    publish.publish(_site(tmp_path, "m2"), target, version="main")

    assert (target / "main" / "index.html").read_text() == "m2"
    assert not (target / "main" / "stale.html").exists()
    assert marker.read_text() == "kept"
    assert (target / "0.1.0" / "index.html").read_text() == "r"
    assert 'url=stable/"' in (target / "index.html").read_text()


@pytest.mark.parametrize("tag", ["v0.2.0rc1", "v0.2.0-rc.1", "0.2.0", "v0.2", "latest", "v1.2.3.4", ""])
def test_a_pre_release_or_malformed_tag_is_refused(tmp_path: pathlib.Path, tag: str) -> None:
    target = tmp_path / "pages"
    target.mkdir()

    with pytest.raises(ValueError, match="tag"):
        publish.publish(_site(tmp_path, "x"), target, version=tag)

    assert list(target.iterdir()) == []


def test_release_folder_is_the_version_of_the_tag() -> None:
    assert publish.release_folder("v0.2.1") == "0.2.1"
    assert publish.release_folder("v10.14.0") == "10.14.0"


def test_a_missing_site_is_refused_and_leaves_the_target_untouched(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "live"), target, version="v0.1.0")
    before = {path: path.read_bytes() for path in target.rglob("*") if path.is_file()}

    with pytest.raises(NotADirectoryError, match="site"):
        publish.publish(tmp_path / "missing", target, version="v0.1.1")

    assert {path: path.read_bytes() for path in target.rglob("*") if path.is_file()} == before


def test_a_failed_copy_keeps_the_previous_folder_and_leaves_no_temporary_one(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "live"), target, version="main")
    before = sorted(path.name for path in target.iterdir())

    def failing_copytree(source: pathlib.Path, destination: pathlib.Path) -> None:
        destination.mkdir()
        (destination / "partial.html").write_text("partial")
        raise OSError(source)

    monkeypatch.setattr(publish.shutil, "copytree", failing_copytree)

    with pytest.raises(OSError, match="site-new"):
        publish.publish(_site(tmp_path, "new"), target, version="main")

    assert (target / "main" / "index.html").read_text() == "live"
    assert sorted(path.name for path in target.iterdir()) == before


def test_a_preview_goes_under_pr_and_changes_nothing_else(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "m"), target, version="main")
    before = {path: path.read_bytes() for path in target.rglob("*") if path.is_file()}

    publish.publish_preview(_site(tmp_path, "p"), target, number=57)

    assert (target / "pr" / "57" / "index.html").read_text() == "p"
    previews = target / "pr"
    outside = {path: path.read_bytes() for path in target.rglob("*") if path.is_file() and previews not in path.parents}
    assert outside == before


def test_a_preview_on_an_empty_branch_leaves_a_root_that_pages_can_serve(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"

    publish.publish_preview(_site(tmp_path, "p"), target, number=57)

    assert (target / ".nojekyll").exists()
    assert 'content="0; url=main/"' in (target / "index.html").read_text()
    assert not (target / "versions.json").exists()


def test_a_new_push_replaces_the_preview(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish_preview(_site(tmp_path, "old"), target, number=57)
    (target / "pr" / "57" / "stale.html").write_text("stale")

    publish.publish_preview(_site(tmp_path, "new"), target, number=57)

    assert (target / "pr" / "57" / "index.html").read_text() == "new"
    assert not (target / "pr" / "57" / "stale.html").exists()


def test_publishing_a_version_keeps_the_previews_and_does_not_list_them(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish_preview(_site(tmp_path, "p"), target, number=57)

    publish.publish(_site(tmp_path, "m"), target, version="main")
    publish.publish(_site(tmp_path, "r"), target, version="v0.1.0")

    assert (target / "pr" / "57" / "index.html").read_text() == "p"
    assert [entry["version"] for entry in _versions(target)] == ["0.1.0", "main"]


def test_removing_a_preview_keeps_the_others(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish_preview(_site(tmp_path, "a"), target, number=57)
    publish.publish_preview(_site(tmp_path, "b"), target, number=58)

    publish.remove_preview(target, number=57)

    assert not (target / "pr" / "57").exists()
    assert (target / "pr" / "58" / "index.html").read_text() == "b"


def test_removing_a_preview_that_is_not_there_changes_nothing(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish(_site(tmp_path, "m"), target, version="main")
    before = {path: path.read_bytes() for path in target.rglob("*") if path.is_file()}

    publish.remove_preview(target, number=57)

    assert {path: path.read_bytes() for path in target.rglob("*") if path.is_file()} == before


def test_a_preview_of_a_missing_site_is_refused_and_keeps_the_old_one(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish_preview(_site(tmp_path, "live"), target, number=57)

    with pytest.raises(NotADirectoryError, match="site"):
        publish.publish_preview(tmp_path / "missing", target, number=57)

    assert (target / "pr" / "57" / "index.html").read_text() == "live"


@pytest.mark.parametrize(
    ("arguments", "served"),
    [
        (["--site", "{site}", "--version", "main"], "main/index.html"),
        (["--site", "{site}", "--preview", "57"], "pr/57/index.html"),
    ],
)
def test_the_command_line_places_what_it_is_asked_to(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], served: str
) -> None:
    site = _site(tmp_path, "s")
    target = tmp_path / "pages"
    filled = [argument.format(site=site) for argument in arguments]
    monkeypatch.setattr(sys, "argv", ["publish", "--target", str(target), *filled])

    publish.main()

    assert (target / served).read_text() == "s"


def test_the_command_line_removes_a_preview_without_a_site(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "pages"
    target.mkdir()
    publish.publish_preview(_site(tmp_path, "p"), target, number=57)
    monkeypatch.setattr(sys, "argv", ["publish", "--target", str(target), "--remove-preview", "57"])

    publish.main()

    assert not (target / "pr" / "57").exists()


@pytest.mark.parametrize("action", [["--version", "main"], ["--preview", "57"]])
def test_the_command_line_needs_a_site_to_place(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, action: list[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["publish", "--target", str(tmp_path), *action])

    with pytest.raises(SystemExit):
        publish.main()
