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

import typing

from tools.docs import prepare

if typing.TYPE_CHECKING:
    import pathlib


def _tree(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "assets" / "web").mkdir(parents=True)
    (tmp_path / "assets" / "web" / "a.webp").write_bytes(b"web-bytes")
    (tmp_path / "assets" / "logo.svg").write_bytes(b"<svg/>")
    (tmp_path / "assets" / "logo-light.svg").write_bytes(b"<svg light/>")
    (tmp_path / "docs").mkdir()
    return tmp_path


def test_prepare_copies_the_web_artwork_into_the_docs(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)

    prepare.prepare(root)

    assert (root / "docs" / "assets" / "images" / "a.webp").read_bytes() == b"web-bytes"


def test_prepare_copies_the_logo_as_the_favicon(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)

    prepare.prepare(root)

    assert (root / "docs" / "assets" / "images" / "favicon.svg").read_bytes() == b"<svg/>"


def test_prepare_copies_both_small_logos(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)

    prepare.prepare(root)

    images = root / "docs" / "assets" / "images"
    assert (images / "logo.svg").read_bytes() == b"<svg/>"
    assert (images / "logo-light.svg").read_bytes() == b"<svg light/>"


def test_prepare_replaces_a_stale_copy(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    images = root / "docs" / "assets" / "images"
    images.mkdir(parents=True)
    (images / "a.webp").write_bytes(b"stale")
    (images / "gone.webp").write_bytes(b"orphan")

    prepare.prepare(root)

    assert (images / "a.webp").read_bytes() == b"web-bytes"
    assert not (images / "gone.webp").exists()


def test_prepare_publishes_the_social_preview_for_the_pages_link_previews(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    (root / "assets" / "web" / "social-preview.png").write_bytes(b"png")

    prepare.prepare(root)

    assert (root / "docs" / "assets" / "images" / "social-preview.png").read_bytes() == b"png"
