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

import pytest

from tools.docs import manifest

if typing.TYPE_CHECKING:
    import pathlib


def _write(tmp_path: pathlib.Path, text: str, *, examples: tuple[str, ...] = ("counter",)) -> pathlib.Path:
    for name in examples:
        (tmp_path / f"{name}.py").write_text("")
    path = tmp_path / "previews.toml"
    path.write_text(text)
    return path


def test_each_kind_loads(tmp_path: pathlib.Path) -> None:
    path = _write(
        tmp_path,
        """
[[preview]]
kind = "view"
id = "counter"
example = "counter"
view = "Counter"
fields = { count = 3 }

[[preview]]
kind = "reply"
id = "pong"
text = "pong"
ephemeral = true

[[preview]]
kind = "error"
id = "overflow"
example = "counter"
view = "Counter"
fields = { count = 99 }

[[preview]]
kind = "click"
id = "click"
example = "counter"
view = "Counter"
before = { count = 3 }
after = { count = 4 }
handler = "click"
""",
    )

    previews = manifest.load_manifest(path)

    assert [type(preview) for preview in previews] == [
        manifest.ViewPreview,
        manifest.ReplyPreview,
        manifest.ErrorPreview,
        manifest.ClickPath,
    ]
    assert previews[0] == manifest.ViewPreview(id="counter", example="counter", view="Counter", fields={"count": 3})
    assert previews[1] == manifest.ReplyPreview(id="pong", text="pong", ephemeral=True)


def test_an_unknown_kind_is_refused(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, '[[preview]]\nkind = "video"\nid = "x"\n')

    with pytest.raises(manifest.ManifestError, match="video"):
        manifest.load_manifest(path)


def test_two_previews_with_one_id_are_refused(tmp_path: pathlib.Path) -> None:
    reply = '[[preview]]\nkind = "reply"\nid = "same"\ntext = "a"\nephemeral = false\n'
    path = _write(tmp_path, reply + reply)

    with pytest.raises(manifest.ManifestError, match="same"):
        manifest.load_manifest(path)


def test_an_example_that_does_not_exist_is_refused(tmp_path: pathlib.Path) -> None:
    path = _write(
        tmp_path,
        '[[preview]]\nkind = "view"\nid = "x"\nexample = "missing"\nview = "V"\nfields = {}\n',
    )

    with pytest.raises(manifest.ManifestError, match="missing"):
        manifest.load_manifest(path)


@pytest.mark.parametrize("bad", ["../../esc", "Up", "a_b", "-a", "a--b", "", "a/b"])
def test_a_preview_id_outside_the_pattern_is_refused(tmp_path: pathlib.Path, bad: str) -> None:
    path = _write(tmp_path, f'[[preview]]\nkind = "reply"\nid = "{bad}"\ntext = "a"\nephemeral = false\n')

    with pytest.raises(manifest.ManifestError, match="id"):
        manifest.load_manifest(path)


def test_a_missing_manifest_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(manifest.ManifestError, match=r"previews\.toml"):
        manifest.load_manifest(tmp_path / "previews.toml")


def test_a_label_defaults_to_in_discord_and_can_be_set(tmp_path: pathlib.Path) -> None:
    path = _write(
        tmp_path,
        """
[[preview]]
kind = "reply"
id = "a"
text = "a"
ephemeral = false

[[preview]]
kind = "reply"
id = "b"
text = "b"
ephemeral = false
label = "After a click"
""",
    )

    first, second = manifest.load_manifest(path)

    assert isinstance(first, manifest.ReplyPreview)
    assert isinstance(second, manifest.ReplyPreview)
    assert (first.label, second.label) == ("In Discord", "After a click")


def test_a_thinking_preview_loads(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, '[[preview]]\nkind = "thinking"\nid = "t"\nephemeral = true\n')

    assert manifest.load_manifest(path) == [manifest.ThinkingPreview(id="t", ephemeral=True)]


def test_a_live_preview_loads(tmp_path: pathlib.Path) -> None:
    path = _write(
        tmp_path,
        """
[[preview]]
kind = "live"
id = "counter-live"
example = "counter"
view = "Counter"
section = "view"
field = "count"
note = "Click it."
""",
    )

    assert manifest.load_manifest(path) == [
        manifest.LivePreview(
            id="counter-live",
            example="counter",
            view="Counter",
            section="view",
            field="count",
            note="Click it.",
        ),
    ]


def test_a_live_preview_needs_an_example_with_a_file(tmp_path: pathlib.Path) -> None:
    path = _write(
        tmp_path,
        """
[[preview]]
kind = "live"
id = "counter-live"
example = "missing"
view = "Counter"
section = "view"
field = "count"
""",
    )

    with pytest.raises(manifest.ManifestError, match="missing"):
        manifest.load_manifest(path)


def test_an_ids_diagram_loads_with_its_defaults(tmp_path: pathlib.Path) -> None:
    path = _write(
        tmp_path,
        """
[[preview]]
kind = "ids"
id = "ids"
example = "counter"
view = "Counter"
before = { count = 3 }
""",
    )

    (preview,) = manifest.load_manifest(path)

    assert preview == manifest.IdsDiagram(id="ids", example="counter", view="Counter", before={"count": 3})
    assert isinstance(preview, manifest.IdsDiagram)
    assert preview.after is None
    assert preview.label == "What the buttons hold"


def test_an_ids_diagram_is_a_static_preview() -> None:
    assert manifest.IdsDiagram in typing.get_args(manifest.StaticPreview.__value__)


def test_a_view_preview_has_no_content_unless_it_says_so(tmp_path: pathlib.Path) -> None:
    path = _write(
        tmp_path,
        """
[[preview]]
kind = "view"
id = "plain"
example = "counter"
view = "Counter"
fields = {}

[[preview]]
kind = "view"
id = "with-text"
example = "counter"
view = "Counter"
fields = {}
content = "Hello"
""",
    )

    plain, with_text = manifest.load_manifest(path)

    assert isinstance(plain, manifest.ViewPreview)
    assert isinstance(with_text, manifest.ViewPreview)
    assert (plain.content, with_text.content) == ("", "Hello")


def test_a_log_preview_names_an_example_and_a_view(tmp_path: pathlib.Path) -> None:
    path = _write(tmp_path, '[[preview]]\nkind = "log"\nid = "warned"\nexample = "counter"\nview = "Counter"\n')

    assert manifest.load_manifest(path) == [manifest.LogPreview(id="warned", example="counter", view="Counter")]
