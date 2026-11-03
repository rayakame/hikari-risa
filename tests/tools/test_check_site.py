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

from tools.docs import check_site

if typing.TYPE_CHECKING:
    import pathlib

_PAGE = """<!doctype html>
<html>
  <head><link rel="stylesheet" href="{up}assets/stylesheets/risa.css"></head>
  <body dir="ltr" data-md-color-scheme="{scheme}">
    {palette}
    <img src="{up}assets/images/logo.svg#only-dark" alt="">
    <img src="https://example.com/remote.png" alt="">
    <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="">
    {extra}
  </body>
</html>
"""


_PALETTE = (
    '<input data-md-color-media="{media}" data-md-color-scheme="{first}" type="radio" name="__palette">'
    '<input data-md-color-media="none" data-md-color-scheme="risa-banner" type="radio" name="__palette">'
)


def _page(
    site: pathlib.Path,
    name: str,
    *,
    scheme: str = "risa-night",
    extra: str = "",
    first: str = "risa-night",
    media: str = "none",
) -> None:
    path = site / name
    path.parent.mkdir(parents=True, exist_ok=True)
    up = "../" * (len(path.relative_to(site).parts) - 1)
    palette = _PALETTE.format(media=media, first=first)
    path.write_text(_PAGE.format(up=up, scheme=scheme, extra=extra, palette=palette), encoding="utf-8")


def _site(tmp_path: pathlib.Path) -> pathlib.Path:
    site = tmp_path / "site"
    (site / "assets" / "images").mkdir(parents=True)
    (site / "assets" / "images" / "logo.svg").write_text("<svg/>", encoding="utf-8")
    (site / "assets" / "stylesheets").mkdir(parents=True)
    (site / "assets" / "stylesheets" / "risa.css").write_text("body { color: red; }", encoding="utf-8")
    _page(site, "index.html")
    _page(site, "get-started/index.html")
    return site


def test_a_clean_site_has_no_problems(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)

    assert check_site.check_site(site) == []


def test_an_empty_site_is_reported(tmp_path: pathlib.Path) -> None:
    site = tmp_path / "site"
    site.mkdir()

    assert len(check_site.check_site(site)) == 1


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("index.html", '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'),
        ("assets/stylesheets/extra.css", "@import url('https://fonts.googleapis.com/css?family=Inter');"),
    ],
)
def test_a_page_that_loads_google_fonts_is_reported(tmp_path: pathlib.Path, name: str, content: str) -> None:
    site = _site(tmp_path)
    if name.endswith(".html"):
        _page(site, name, extra=content)
    else:
        (site / name).write_text(content, encoding="utf-8")

    problems = check_site.check_site(site)

    assert len(problems) == 1
    assert name in problems[0]


@pytest.mark.parametrize("scheme", ["slate", "risa-banner"])
def test_a_page_whose_body_does_not_default_to_risa_night_is_reported(tmp_path: pathlib.Path, scheme: str) -> None:
    site = _site(tmp_path)
    _page(site, "get-started/index.html", scheme=scheme)

    problems = check_site.check_site(site)

    assert len(problems) == 1
    assert "get-started/index.html" in problems[0]


def test_a_page_without_a_colour_scheme_is_reported(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    (site / "bare.html").write_text("<html><body><p>hi</p></body></html>", encoding="utf-8")

    problems = check_site.check_site(site)

    assert problems
    assert all("bare.html" in problem for problem in problems)
    assert any("<body>" in problem for problem in problems)


@pytest.mark.parametrize("folder", ["specs", "examples", "generated"])
def test_a_published_specs_folder_is_reported(tmp_path: pathlib.Path, folder: str) -> None:
    site = _site(tmp_path)
    _page(site, f"{folder}/index.html")

    problems = check_site.check_site(site)

    assert len(problems) == 1
    assert folder in problems[0]


@pytest.mark.parametrize(
    "src",
    [
        "assets/images/missing.webp",
        "../assets/images/logo.svg",
        "../outside.svg",
        "assets/images/",
    ],
)
def test_a_missing_image_reference_is_reported(tmp_path: pathlib.Path, src: str) -> None:
    site = _site(tmp_path)
    (tmp_path / "outside.svg").write_text("<svg/>", encoding="utf-8")
    _page(site, "index.html", extra=f'<img src="{src}" alt="">')

    problems = check_site.check_site(site)

    assert len(problems) == 1
    assert src in problems[0]


def test_a_first_palette_entry_that_is_not_risa_night_is_reported(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    _page(site, "get-started/index.html", first="risa-banner")

    problems = check_site.check_site(site)

    assert len(problems) == 1
    assert "get-started/index.html" in problems[0]
    assert "first palette" in problems[0]


def test_a_palette_entry_with_a_media_query_is_reported(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    _page(site, "index.html", media="(prefers-color-scheme: dark)")

    problems = check_site.check_site(site)

    assert len(problems) == 1
    assert "index.html" in problems[0]
    assert "(prefers-color-scheme: dark)" in problems[0]


_EXTERNAL_LINKS = (
    '<a href="https://docs.python.org/3/library/typing.html#typing.Final">Final</a>'
    '<a href="https://docs.hikari-py.dev/en/stable/reference/hikari/guilds/">guilds</a>'
)


def test_missing_inventory_links_are_reported_when_required(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    _page(site, "api/risa/index.html", extra='<a href="https://docs.python.org/3/library/typing.html">typing</a>')

    problems = check_site.check_site(site, require_inventory_links=True)

    assert len(problems) == 1
    assert "https://docs.hikari-py.dev/" in problems[0]


def test_present_inventory_links_are_clean_when_required(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    _page(site, "api/risa/index.html", extra=_EXTERNAL_LINKS)

    assert check_site.check_site(site, require_inventory_links=True) == []


def test_inventory_links_outside_the_api_pages_do_not_count(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)
    _page(site, "index.html", extra=_EXTERNAL_LINKS)

    assert len(check_site.check_site(site, require_inventory_links=True)) == 2


def test_missing_inventory_links_are_ignored_by_default(tmp_path: pathlib.Path) -> None:
    site = _site(tmp_path)

    assert check_site.check_site(site) == []
