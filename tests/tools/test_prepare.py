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
    (tmp_path / "docs" / "examples").mkdir(parents=True)
    (tmp_path / "docs" / "examples" / "previews.toml").write_text("")
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


_TINY = """
import risa
from risa import ui


# --8<-- [start:view]
@risa.register(name="tiny")
class Tiny(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Container(ui.TextDisplay(f"Count {self.count}"), ui.Row(ui.Button(self.go, label="Go")))

    @risa.handler
    async def go(self, ctx: risa.ComponentContext) -> None:
        self.count += 1
        await ctx.rerender()
# --8<-- [end:view]
"""

_MANIFEST = """
[[preview]]
kind = "view"
id = "tiny-three"
example = "tiny"
view = "Tiny"
fields = { count = 3 }

[[preview]]
kind = "reply"
id = "pong"
text = "pong"
ephemeral = true
"""


def test_prepare_writes_one_file_per_preview(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    (root / "docs" / "examples" / "tiny.py").write_text(_TINY)
    (root / "docs" / "examples" / "previews.toml").write_text(_MANIFEST)

    prepare.prepare(root)

    previews = root / "docs" / "generated" / "previews"
    assert sorted(path.name for path in previews.iterdir()) == ["pong.html", "tiny-three.html"]
    assert "Count 3" in (previews / "tiny-three.html").read_text()
    assert "Only you can see this" in (previews / "pong.html").read_text()


def test_prepare_removes_previews_that_left_the_manifest(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    previews = root / "docs" / "generated" / "previews"
    previews.mkdir(parents=True)
    (previews / "gone.html").write_text("stale")

    prepare.prepare(root)

    assert not (previews / "gone.html").exists()


def test_a_preview_file_carries_its_own_wrapper_and_label(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    (root / "docs" / "examples" / "previews.toml").write_text(
        '[[preview]]\nkind = "reply"\nid = "pong"\ntext = "pong"\nephemeral = false\nlabel = "Reply"\n',
    )

    prepare.prepare(root)

    text = (root / "docs" / "generated" / "previews" / "pong.html").read_text()
    assert text.startswith(
        '<div class="risa-result" data-search-exclude><p class="risa-result__label">Reply</p><div class="dc">',
    )
    assert "\n\n" not in text


def test_prepare_draws_a_thinking_preview(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    (root / "docs" / "examples" / "previews.toml").write_text('[[preview]]\nkind = "thinking"\nid = "wait"\n')

    prepare.prepare(root)

    assert "is thinking" in (root / "docs" / "generated" / "previews" / "wait.html").read_text()


def test_prepare_leaves_a_subdirectory_of_the_previews_folder_alone(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    folder = root / "docs" / "generated" / "previews" / "nested"
    folder.mkdir(parents=True)
    (folder / "x.html").write_text("keep")

    prepare.prepare(root)

    assert (folder / "x.html").read_text() == "keep"


def test_prepare_draws_a_live_preview_into_one_block(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    (root / "docs" / "examples" / "tiny.py").write_text(_TINY)
    (root / "docs" / "examples" / "previews.toml").write_text(
        '[[preview]]\nkind = "live"\nid = "tiny-live"\nexample = "tiny"\nview = "Tiny"\n'
        'section = "view"\nfield = "count"\n',
    )

    prepare.prepare(root)

    text = (root / "docs" / "generated" / "previews" / "tiny-live.html").read_text()
    assert text.count("<template data-live-frame") == 1
    assert "\n\n" not in text
