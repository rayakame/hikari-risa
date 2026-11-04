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
"""Writes the web copies of the artwork in ``assets/`` into ``assets/web/``."""

from __future__ import annotations

import logging
import pathlib
import typing

from PIL import Image

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("optimize",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.optimize_images")

_MASCOT_SIZE: typing.Final = (480, 480)
_AVATAR_SIZE: typing.Final = (128, 128)


def optimize(source: pathlib.Path, target: pathlib.Path) -> collections.abc.Sequence[pathlib.Path]:
    """Write the web copies of the artwork in ``source`` into ``target``.

    The originals are only read. Every output is stripped of metadata, so running
    this twice produces identical files.

    Parameters
    ----------
    source
        The directory holding ``banner.png``, ``social-preview.png``, ``avatar.png`` and the
        two logo PNGs.
    target
        The directory to write into; created when missing.

    Returns
    -------
    collections.abc.Sequence[pathlib.Path]
        The files written.
    """
    target.mkdir(parents=True, exist_ok=True)
    written: list[pathlib.Path] = []

    with Image.open(source / "banner.png") as banner:
        banner.load()
        written.append(_save_webp(banner, target / "banner.webp", quality=85))

    with Image.open(source / "social-preview.png") as preview:
        preview.load()
        social = target / "social-preview.png"
        preview.convert("RGB").save(social, format="PNG", optimize=True)
        written.append(social)

    written.extend(
        _resized_webp(source / f"{name}.png", target / f"{name}.webp", _MASCOT_SIZE)
        for name in ("logo-mascot", "logo-mascot-light")
    )
    written.append(_resized_webp(source / "avatar.png", target / "avatar.webp", _AVATAR_SIZE))

    for path in written:
        _LOGGER.info("wrote %s (%d bytes)", path, path.stat().st_size)
    return written


def _resized_webp(source: pathlib.Path, target: pathlib.Path, size: tuple[int, int]) -> pathlib.Path:
    with Image.open(source) as image:
        image.load()
        # Pillow's resize stub leaks an Unknown into its size parameter.
        resized = image.resize(size, Image.Resampling.LANCZOS)  # type: ignore[reportUnknownMemberType]
        return _save_webp(resized, target, quality=90)


def _save_webp(image: Image.Image, target: pathlib.Path, *, quality: int) -> pathlib.Path:
    image.save(target, format="WEBP", quality=quality, exif=b"", icc_profile=None, xmp=b"")
    return target


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = pathlib.Path(__file__).resolve().parent.parent / "assets"
    optimize(root, root / "web")


if __name__ == "__main__":
    main()
