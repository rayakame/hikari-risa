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
import re
import typing

import pytest

import risa
from tools.docs import embeds

if typing.TYPE_CHECKING:
    import collections.abc
    import pathlib

_ROOT = "https://docs.example/1.0.0/"
_SCRIPT = re.compile(r'<script id="discord:component-embed" type="application/json">(.*?)</script>\n</head>')
_LINK_BUTTON_KEYS = {"type", "style", "url", "label", "emoji", "disabled"}
_ARROW = "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"


class _Media(typing.TypedDict):
    url: str


class _Item(typing.TypedDict):
    media: _Media


class _Component(typing.TypedDict, total=False):
    type: typing.Required[int]
    accent_color: int
    components: list[_Component]
    accessory: _Component
    content: str
    label: str
    url: str
    style: int
    media: _Media
    items: list[_Item]


def _page(
    site: pathlib.Path,
    path: str,
    *,
    title: str,
    body: str = "",
    crumbs: collections.abc.Sequence[str] = ("Home",),
    root: str = _ROOT,
) -> pathlib.Path:
    page = site / path / "index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    items = "".join(f'<li><a href="#"><span class="md-ellipsis">\n {crumb}\n</span></a></li>' for crumb in crumbs)
    trail = f'<nav class="md-path" aria-label="Navigation"><ol>{items}</ol></nav>' if path else ""
    page.write_text(
        f'<html><head><link rel="canonical" href="{root}{path}{"/" if path else ""}">'
        '<meta name="description" content="Views for hikari &amp; more."></head>'
        f'<body>{trail}<h1 id="x">{title}<a class="headerlink" href="#x">&para;</a></h1>{body}</body></html>'
    )
    return page


def _site(tmp_path: pathlib.Path, *, root: str = _ROOT) -> pathlib.Path:
    site = tmp_path / "site"
    _page(site, "", title="hikari-risa", root=root)
    return site


def _component(page: pathlib.Path) -> _Component:
    match = _SCRIPT.search(page.read_text(encoding="utf-8"))
    assert match is not None
    component: _Component = json.loads(match[1])["component"]
    return component


def _all(component: _Component) -> collections.abc.Iterator[_Component]:
    yield component
    for child in component.get("components", []):
        yield from _all(child)
    if "accessory" in component:
        yield from _all(component["accessory"])


def _texts(component: _Component) -> list[str]:
    return [child.get("content", "") for child in _all(component) if child["type"] == 10]


def _buttons(component: _Component) -> list[_Component]:
    return [child for child in _all(component) if child["type"] == 2]


def test_a_chapter_shows_its_place_title_and_lead_and_links_to_itself(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    page = _page(
        site,
        "by-example/02-buttons",
        title="Buttons that remember",
        crumbs=("Home", "By example"),
        body='<p class="risa-lead">Fields last from click to click.</p><p>Later.</p>',
    )

    embeds.embed_site(site)

    component = _component(page)
    assert component["type"] == 17
    assert component.get("accent_color") == 0xF680A1
    assert _texts(component)[0] == (
        f"-# By example {_ARROW} Chapter 2\n## Buttons that remember\nFields last from click to click."
    )
    assert [(button.get("label"), button.get("url")) for button in _buttons(component)] == [
        ("Open the chapter", f"{_ROOT}by-example/02-buttons/"),
        ("GitHub", "https://github.com/rayakame/hikari-risa"),
    ]
    assert [child.get("media") for child in _all(component) if child["type"] == 11] == [
        {"url": f"{_ROOT}assets/images/mascot-sticker.webp"}
    ]


def test_the_landing_page_shows_the_banner_the_install_line_and_three_links(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)

    embeds.embed_site(site)

    component = _component(site / "index.html")
    galleries = [child for child in _all(component) if child["type"] == 12]
    assert [item["media"]["url"] for gallery in galleries for item in gallery.get("items", [])] == [
        f"{_ROOT}assets/images/banner.webp"
    ]
    assert _texts(component)[:2] == ["# hikari-risa\nViews for hikari & more.", "```sh\nuv add hikari-risa\n```"]
    assert [button.get("label") for button in _buttons(component)] == ["Read the docs", "GitHub", "PyPI"]
    assert _buttons(component)[0].get("url") == _ROOT


def test_a_page_without_a_lead_takes_its_first_paragraph_and_keeps_its_code(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    page = _page(
        site,
        "api/ui/nodes",
        title="risa.ui.nodes",
        crumbs=("Home", "API reference", "risa.ui"),
        body='<div><p>The components of a <code class="highlight">View</code>, &lt;V2&gt;.</p><p>More.</p></div>',
    )

    embeds.embed_site(site)

    component = _component(page)
    assert _texts(component)[0] == (
        f"-# API reference {_ARROW} risa.ui\n## risa.ui.nodes\nThe components of a `View`, <V2>."
    )
    assert _buttons(component)[0].get("label") == "Open the page"


def test_an_overview_does_not_repeat_its_title_above_it(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    page = _page(site, "api", title="API reference", crumbs=("Home", "API reference"), body="<p>Everything.</p>")

    embeds.embed_site(site)

    assert _texts(_component(page))[0] == "## API reference\nEverything."


def test_a_long_lead_is_cut_at_a_word_and_never_inside_code(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    words = " ".join(["word"] * 54)
    page = _page(site, "long", title="Long", body=f'<p class="risa-lead">{words} <code>some code</code> after</p>')

    embeds.embed_site(site)

    assert _texts(_component(page))[0].split("\n")[-1] == f"{words}…"


def test_a_long_lead_ends_after_its_last_whole_sentence(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    first = " ".join(["word"] * 30) + "."
    page = _page(site, "long", title="Long", body=f'<p class="risa-lead">{first} {" ".join(["more"] * 40)}</p>')

    embeds.embed_site(site)

    assert _texts(_component(page))[0].split("\n")[-1] == first


def test_a_long_lead_without_a_space_is_cut_at_the_limit(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    page = _page(site, "long", title="Long", body=f'<p class="risa-lead">{"x" * 300}</p>')

    embeds.embed_site(site)

    assert _texts(_component(page))[0].split("\n")[-1] == "x" * 280 + "…"


def test_every_button_is_a_link_button_with_only_the_keys_discord_takes(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    page = _page(site, "page", title="Page", body="<p>Text.</p>")

    embeds.embed_site(site)

    for component in (_component(site / "index.html"), _component(page)):
        buttons = _buttons(component)
        assert buttons
        assert all(button.get("style") == 5 and set(button) <= _LINK_BUTTON_KEYS for button in buttons)


@pytest.mark.parametrize(
    ("root", "version"),
    [
        ("https://docs.example/1.0.0/", "1.0.0"),
        ("https://docs.example/pr/73/", "pr/73"),
        ("https://docs.example/", risa.__version__),
    ],
)
def test_the_footer_names_the_version_the_site_is_built_for(tmp_path: pathlib.Path, root: str, version: str) -> None:
    site = _site(tmp_path, root=root)

    embeds.embed_site(site)

    assert _texts(_component(site / "index.html"))[-1] == f"-# hikari-risa {version} · This preview is a risa view"


def test_a_closing_tag_in_the_text_cannot_end_the_script(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    page = _page(site, "page", title="Page", body="<p>Write &lt;/script&gt; nowhere.</p>")

    embeds.embed_site(site)

    assert "\\u003c/script>" in page.read_text(encoding="utf-8")
    assert _texts(_component(page))[0].endswith("Write </script> nowhere.")


def test_a_preview_longer_than_discord_reads_is_refused(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    _page(site, "page", title="x" * 3000, body="<p>Text.</p>")

    with pytest.raises(ValueError, match="bytes"):
        embeds.embed_site(site)


@pytest.mark.parametrize(("body", "missing"), [("", "heading"), ("<h1>Bare</h1>", "paragraph")])
def test_a_page_without_a_heading_or_a_paragraph_is_refused(tmp_path: pathlib.Path, body: str, missing: str) -> None:
    site = _site(tmp_path)
    page = site / "bare" / "index.html"
    page.parent.mkdir()
    page.write_text(
        f'<html><head><link rel="canonical" href="https://docs.example/bare/"></head><body>{body}</body></html>'
    )

    with pytest.raises(ValueError, match=missing):
        embeds.embed_site(site)


def test_the_not_found_page_is_left_alone(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    not_found = site / "404.html"
    not_found.write_text("<html><head></head><body><h1>404</h1></body></html>")

    assert embeds.embed_site(site) == 1

    assert "discord:component-embed" not in not_found.read_text()
