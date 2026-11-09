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
"""Stages the generated inputs of the documentation site before it is built."""

from __future__ import annotations

import collections
import logging
import pathlib
import shutil
import typing

from tools.docs import discord_html
from tools.docs import manifest
from tools.docs import render_example

__all__ = ("prepare",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.docs.prepare")


def prepare(root: pathlib.Path) -> None:
    """Stage the artwork in ``docs/assets/images/`` and draw the previews into ``docs/generated/previews/``.

    The artwork is the web artwork, the small logos and the favicon. Every preview of
    ``docs/examples/previews.toml`` becomes one HTML file; files of previews that left
    the manifest are removed.

    Parameters
    ----------
    root
        The repository root.
    """
    images = root / "docs" / "assets" / "images"
    shutil.rmtree(images, ignore_errors=True)
    images.mkdir(parents=True)

    for source in sorted((root / "assets" / "web").iterdir()):
        shutil.copyfile(source, images / source.name)
        _LOGGER.info("copied %s", source.name)

    for name in ("logo.svg", "logo-light.svg"):
        shutil.copyfile(root / "assets" / name, images / name)
        _LOGGER.info("copied %s", name)

    shutil.copyfile(root / "assets" / "logo.svg", images / "favicon.svg")

    _write_previews(root)


def _write_previews(root: pathlib.Path) -> None:
    examples = root / "docs" / "examples"
    previews = manifest.load_manifest(examples / "previews.toml")

    drawn: dict[str, str] = {}
    by_example: dict[str, list[manifest.Preview]] = collections.defaultdict(list)
    for preview in previews:
        if isinstance(preview, manifest.ReplyPreview):
            drawn[preview.id] = discord_html.wrap_result(
                discord_html.render_reply(preview.text, ephemeral=preview.ephemeral),
                preview.label,
            )
        elif isinstance(preview, manifest.ThinkingPreview):
            drawn[preview.id] = discord_html.wrap_result(
                discord_html.render_thinking(ephemeral=preview.ephemeral),
                preview.label,
            )
        else:
            by_example[preview.example].append(preview)
    for example, group in by_example.items():
        drawn.update(render_example.render_previews(examples / f"{example}.py", group))

    target = root / "docs" / "generated" / "previews"
    target.mkdir(parents=True, exist_ok=True)
    for stale in target.glob("*.html"):
        if stale.name.removesuffix(".html") not in drawn:
            stale.unlink()
            _LOGGER.info("removed %s", stale.name)
    for preview_id, markup in drawn.items():
        (target / f"{preview_id}.html").write_text(markup + "\n")
        _LOGGER.info("drew %s", preview_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    prepare(pathlib.Path.cwd())
