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

import logging
import pathlib
import shutil
import typing

__all__ = ("prepare",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.docs.prepare")
_SOCIAL_PREVIEW: typing.Final = "banner-social.jpg"


def prepare(root: pathlib.Path) -> None:
    """Copy the web artwork, the small logos and the favicon into ``docs/assets/images/``.

    Parameters
    ----------
    root
        The repository root.
    """
    images = root / "docs" / "assets" / "images"
    shutil.rmtree(images, ignore_errors=True)
    images.mkdir(parents=True)

    for source in sorted((root / "assets" / "web").iterdir()):
        if source.name == _SOCIAL_PREVIEW:
            continue
        shutil.copyfile(source, images / source.name)
        _LOGGER.info("copied %s", source.name)

    for name in ("logo.svg", "logo-light.svg"):
        shutil.copyfile(root / "assets" / name, images / name)
        _LOGGER.info("copied %s", name)

    shutil.copyfile(root / "assets" / "logo.svg", images / "favicon.svg")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    prepare(pathlib.Path.cwd())
