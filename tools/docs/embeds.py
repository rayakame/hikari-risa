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
"""Writes a Discord link preview, drawn by a risa view, into the head of each page of the built site."""

from __future__ import annotations

import argparse
import html
import json
import logging
import pathlib
import re
import typing
import urllib.parse

import risa
from risa import ui
from risa.internal import registry
from risa.ui import build

__all__ = ("embed_site",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.docs.embeds")
_ACCENT: typing.Final = 0xF680A1
# Discord shows no component embed whose JSON is longer.
_LIMIT: typing.Final = 3000
_LEAD_LENGTH: typing.Final = 280
_GITHUB: typing.Final = "https://github.com/rayakame/hikari-risa"
_PYPI: typing.Final = "https://pypi.org/project/hikari-risa/"
_PAGE: typing.Final = "index.html"
_CANONICAL: typing.Final = re.compile(r'<link rel="canonical" href="([^"]+)">')
_DESCRIPTION: typing.Final = re.compile(r'<meta name="description" content="([^"]*)">')
_PATH: typing.Final = re.compile(r'<nav class="md-path".*?</nav>', re.DOTALL)
_CRUMB: typing.Final = re.compile(r'<span class="md-ellipsis">(.*?)</span>', re.DOTALL)
_HEADING: typing.Final = re.compile(r'<h1[^>]*>(.*?)(?:<a class="headerlink"|</h1>)', re.DOTALL)
_LEAD: typing.Final = re.compile(r'<p class="risa-lead">(.*?)</p>', re.DOTALL)
_FIRST_PARAGRAPH: typing.Final = re.compile(r"</h1>.*?<p>(.*?)</p>", re.DOTALL)
_CHAPTER: typing.Final = re.compile(r"(\d+)-")
_CODE: typing.Final = re.compile(r"<code\b[^>]*>|</code>")
_TAG: typing.Final = re.compile(r"<[^>]+>")


@risa.register(name="docs-link-preview")
class _LinkPreview(risa.View):
    root: str
    page: str
    version: str
    title: str
    lead: str
    eyebrow: str = ""
    link_label: str = "Open the page"
    landing: bool = False

    def render(self) -> ui.Layout:
        footer = ui.TextDisplay(f"-# hikari-risa {self.version} · This preview is a risa view")
        if self.landing:
            return ui.Container(
                ui.MediaGallery(
                    ui.MediaGalleryItem(f"{self.root}assets/images/banner.webp", description="hikari-risa")
                ),
                ui.TextDisplay(f"# {self.title}\n{self.lead}"),
                ui.TextDisplay("```sh\nuv add hikari-risa\n```"),
                ui.Separator(),
                ui.Row(
                    ui.LinkButton(self.page, label="Read the docs", emoji="📖"),
                    ui.LinkButton(_GITHUB, label="GitHub"),
                    ui.LinkButton(_PYPI, label="PyPI"),
                ),
                footer,
                accent_color=_ACCENT,
            )
        eyebrow = f"-# {self.eyebrow}\n" if self.eyebrow else ""
        return ui.Container(
            ui.Section(
                ui.TextDisplay(f"{eyebrow}## {self.title}\n{self.lead}"),
                accessory=ui.Thumbnail(f"{self.root}assets/images/mascot-sticker.webp", description="Risa"),
            ),
            ui.Separator(),
            ui.Row(ui.LinkButton(self.page, label=self.link_label), ui.LinkButton(_GITHUB, label="GitHub")),
            footer,
            accent_color=_ACCENT,
        )


def embed_site(site: pathlib.Path) -> int:
    """Write the Discord link preview of each page of a built site into the page's head.

    Returns
    -------
    :
        How many pages got one.

    Raises
    ------
    ValueError
        If a page lacks a head, a canonical link, a heading or a paragraph, or its preview is longer than
        Discord reads.
    """
    landing = site / _PAGE
    root = _canonical(landing.read_text(encoding="utf-8"), landing)
    version = urllib.parse.urlsplit(root).path.strip("/") or risa.__version__
    pages = sorted(site.rglob(_PAGE))
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if "</head>" not in text:
            message = f"no head in {page}"
            raise ValueError(message)
        script = _script(_preview(text, page, root=root, version=version, landing=page == landing), page)
        page.write_text(text.replace("</head>", f"{script}\n</head>", 1), encoding="utf-8")
    return len(pages)


def _preview(text: str, page: pathlib.Path, *, root: str, version: str, landing: bool) -> _LinkPreview:
    url = _canonical(text, page)
    heading = _HEADING.search(text)
    if heading is None:
        message = f"no heading in {page}"
        raise ValueError(message)
    if landing:
        description = _DESCRIPTION.search(text)
        lead = html.unescape(description[1]) if description else ""
        return _LinkPreview(root=root, page=url, version=version, title=_plain(heading[1]), lead=lead, landing=True)

    title = _plain(heading[1])
    path = _PATH.search(text)
    crumbs = [_plain(crumb) for crumb in _CRUMB.findall(path[0])[1:]] if path else []
    if crumbs and crumbs[-1] == title:
        crumbs.pop()
    chapter = _CHAPTER.match(urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    if chapter:
        crumbs.append(f"Chapter {int(chapter[1])}")
    lead = _LEAD.search(text) or _FIRST_PARAGRAPH.search(text)
    if lead is None:
        message = f"no paragraph in {page}"
        raise ValueError(message)
    return _LinkPreview(
        root=root,
        page=url,
        version=version,
        title=title,
        lead=_shorten(_plain(lead[1])),
        eyebrow=" \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} ".join(crumbs),
        link_label="Open the chapter" if chapter else "Open the page",
    )


def _canonical(text: str, page: pathlib.Path) -> str:
    canonical = _CANONICAL.search(text)
    if canonical is None:
        message = f"no canonical link in {page}"
        raise ValueError(message)
    return canonical[1]


def _plain(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub("", _CODE.sub("`", fragment))).split())


def _shorten(text: str) -> str:
    if len(text) <= _LEAD_LENGTH:
        return text
    sentence = text.rfind(". ", 0, _LEAD_LENGTH)
    if sentence > 0:
        return text[: sentence + 1]
    space = text.rfind(" ", 0, _LEAD_LENGTH)
    cut = text[: space if space > 0 else _LEAD_LENGTH]
    # An odd backtick would open inline code that never closes.
    if cut.count("`") % 2:
        cut = cut[: cut.rfind("`")]
    return cut.rstrip(" ,;:") + "…"


def _script(view: _LinkPreview, page: pathlib.Path) -> str:
    component = build.plan(view.render(), registry.require_meta(view)).emit(b"")[0]
    # A "<" inside the JSON could end the script element early, or keep it from ending at all.
    payload = json.dumps({"component": component.build()[0]}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c")
    size = len(payload.encode())
    if size > _LIMIT:
        message = f"the link preview of {page} is {size} bytes, more than Discord's {_LIMIT}"
        raise ValueError(message)
    return f'<script id="discord:component-embed" type="application/json">{payload}</script>'


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argument_parser = argparse.ArgumentParser(description="Write each page's Discord link preview into a built site.")
    argument_parser.add_argument("site", type=pathlib.Path)
    arguments = argument_parser.parse_args()
    _LOGGER.info("wrote the link previews of %d pages", embed_site(arguments.site))


if __name__ == "__main__":
    main()
