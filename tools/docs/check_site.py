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
"""Checks the built documentation site for the invariants the build itself cannot see."""

from __future__ import annotations

import argparse
import html.parser
import logging
import pathlib
import sys
import typing
import urllib.parse

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("check_site",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.docs.check_site")

_DEFAULT_SCHEME: typing.Final = "risa-night"
_GOOGLE_FONTS_HOSTS: typing.Final = ("fonts.googleapis.com", "fonts.gstatic.com")
_NO_MEDIA: typing.Final = "none"
_UNPUBLISHED_FOLDERS: typing.Final = ("specs", "examples", "generated")
_INVENTORY_HOSTS: typing.Final = ("https://docs.python.org/3/", "https://docs.hikari-py.dev/")
_LANDING_PAGE: typing.Final = "index.html"
_LIVE_SCRIPT: typing.Final = "javascripts/live-preview.js"
_MIN_LIVE_FRAMES: typing.Final = 2
_TITLE_ENDINGS: typing.Final = (".", "?", "!", "…", ":", ")", '"', "'", "”", "\u2019")


class _PageParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.body_scheme: str | None = None
        self.image_sources: list[str] = []
        self.links: list[str] = []
        self.live_frames = 0
        self.palette: list[tuple[str | None, str | None]] = []
        self.script_sources: list[str] = []
        self.preview_in_paragraph = False
        self.run_in_titles: list[str] = []
        self._in_paragraph = False
        self._title: list[str] | None = None

    @typing.override
    def handle_data(self, data: str) -> None:
        if self._title is not None:
            self._title.append(data)

    @typing.override
    def handle_endtag(self, tag: str) -> None:
        if tag == "p":
            self._in_paragraph = False
            if self._title is not None:
                self.run_in_titles.append("".join(self._title).strip())
                self._title = None

    @typing.override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "p":
            self._in_paragraph = True
            if "admonition-title" in (values.get("class") or "").split():
                self._title = []
        elif self._in_paragraph and "dc" in (values.get("class") or "").split():
            self.preview_in_paragraph = True
        if tag == "body":
            self.body_scheme = values.get("data-md-color-scheme")
        elif tag == "img" and (src := values.get("src")) is not None:
            self.image_sources.append(src)
        elif tag == "a" and (href := values.get("href")) is not None:
            self.links.append(href)
        elif tag == "input" and values.get("name") == "__palette":
            self.palette.append((values.get("data-md-color-scheme"), values.get("data-md-color-media")))
        elif tag == "script" and (src := values.get("src")) is not None:
            self.script_sources.append(src)
        elif tag == "template" and "data-live-frame" in values:
            self.live_frames += 1


def check_site(site: pathlib.Path, *, require_inventory_links: bool = False) -> collections.abc.Sequence[str]:
    """Return every problem found in the built site under ``site``.

    The site must default to the ``risa-night`` scheme on every page, in the body and in the
    first palette entry with no media query anywhere, load nothing from Google Fonts, leave
    the design folders unpublished and resolve every relative image. An admonition's title
    runs into its text, so it must end like a sentence. The landing page must also carry the
    frames of its live preview and load the script that swaps them.

    Parameters
    ----------
    site
        The directory ``zensical build`` wrote.
    require_inventory_links
        Also require the API pages to link into the Python and hikari documentation, which
        only happens when both object inventories were reachable during the build.

    Returns
    -------
    collections.abc.Sequence[str]
        One human-readable line per problem; empty when the site is clean.
    """
    pages = sorted(site.rglob("*.html"))
    if not pages:
        return [f"{site}: no HTML pages found"]

    problems = [
        f"{name}/: published, but it is not part of the site" for name in _UNPUBLISHED_FOLDERS if (site / name).exists()
    ]
    api_links: list[str] = []
    for path in sorted([*pages, *site.rglob("*.css")]):
        text = path.read_text(encoding="utf-8")
        if any(host in text for host in _GOOGLE_FONTS_HOSTS):
            problems.append(f"{_relative(path, site)}: loads Google Fonts")
        if path.suffix == ".html":
            parser = _parse(text)
            problems.extend(_check_page(parser, path, site))
            if _relative(path, site) == _LANDING_PAGE:
                problems.extend(_check_landing(parser, path, site))
            if _relative(path, site).startswith("api/"):
                api_links.extend(parser.links)
    if require_inventory_links:
        problems.extend(
            f"api/: no link to {host}, so the object inventory was not loaded"
            for host in _INVENTORY_HOSTS
            if not any(link.startswith(host) for link in api_links)
        )
    return problems


def _parse(text: str) -> _PageParser:
    parser = _PageParser()
    parser.feed(text)
    parser.close()
    return parser


def _check_page(parser: _PageParser, path: pathlib.Path, site: pathlib.Path) -> collections.abc.Sequence[str]:
    name = _relative(path, site)
    problems: list[str] = []
    if parser.body_scheme != _DEFAULT_SCHEME:
        problems.append(f"{name}: <body> defaults to scheme {parser.body_scheme!r}, not {_DEFAULT_SCHEME!r}")
    if not parser.palette or parser.palette[0][0] != _DEFAULT_SCHEME:
        first = parser.palette[0][0] if parser.palette else None
        problems.append(f"{name}: the first palette entry is scheme {first!r}, not {_DEFAULT_SCHEME!r}")
    if parser.preview_in_paragraph:
        problems.append(f"{name}: a Discord preview sits inside a paragraph, so the page's Markdown rewrote it")
    problems.extend(
        f"{name}: the admonition title {title!r} runs into its text, so it needs closing punctuation"
        for title in parser.run_in_titles
        if not title.endswith(_TITLE_ENDINGS)
    )
    problems.extend(
        f"{name}: palette entry {scheme!r} has media query {media!r}, so a light OS would start in it"
        for scheme, media in parser.palette
        if media != _NO_MEDIA
    )
    problems.extend(
        f"{name}: <img src={src!r}> does not resolve to a file in the site"
        for src in parser.image_sources
        if _is_broken(src, path, site)
    )
    return problems


def _check_landing(parser: _PageParser, path: pathlib.Path, site: pathlib.Path) -> collections.abc.Sequence[str]:
    problems: list[str] = []
    if parser.live_frames < _MIN_LIVE_FRAMES:
        problems.append(f"{_LANDING_PAGE}: it has {parser.live_frames} frames of the live preview, so nothing can swap")
    if not any(
        urllib.parse.urlsplit(src).path.removeprefix("./") == _LIVE_SCRIPT and not _is_broken(src, path, site)
        for src in parser.script_sources
    ):
        problems.append(f"{_LANDING_PAGE}: it does not load {_LIVE_SCRIPT}, so the button does nothing")
    return problems


def _is_broken(src: str, page: pathlib.Path, site: pathlib.Path) -> bool:
    url = urllib.parse.urlsplit(src)
    if url.scheme or url.netloc or url.path.startswith("/"):
        return False
    target = (page.parent / urllib.parse.unquote(url.path)).resolve()
    return not (target.is_relative_to(site.resolve()) and target.is_file())


def _relative(path: pathlib.Path, site: pathlib.Path) -> str:
    return path.relative_to(site).as_posix()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argument_parser = argparse.ArgumentParser(description="Check a built documentation site.")
    argument_parser.add_argument("site", type=pathlib.Path)
    argument_parser.add_argument("--require-inventory-links", action="store_true")
    arguments = argument_parser.parse_args()
    problems = check_site(arguments.site, require_inventory_links=arguments.require_inventory_links)
    for problem in problems:
        _LOGGER.error("%s", problem)
    if problems:
        sys.exit(1)
    _LOGGER.info("site check passed")


if __name__ == "__main__":
    main()
