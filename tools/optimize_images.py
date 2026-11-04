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
from PIL import ImageDraw
from PIL import ImageFilter

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("STICKER_PADDING", "optimize")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.optimize_images")

_MASCOT_SIZE: typing.Final = (480, 480)
_AVATAR_SIZE: typing.Final = (128, 128)
_STICKER_HEIGHT: typing.Final = 1600
_STICKER_EDGE_WIDTH: typing.Final = 22
STICKER_PADDING: typing.Final = _STICKER_EDGE_WIDTH * 3
_OPAQUE_ABOVE: typing.Final = 40
_GROWN_ABOVE: typing.Final = 22
_FILL_MARK: typing.Final = 128
_FIGURE_MIN_SHARE: typing.Final = 0.5


def optimize(source: pathlib.Path, target: pathlib.Path) -> collections.abc.Sequence[pathlib.Path]:
    """Write the web copies of the artwork in ``source`` into ``target``.

    The originals are only read. Every output is stripped of metadata, so running
    this twice produces identical files.

    Parameters
    ----------
    source
        The directory holding ``banner.png``, ``social-preview.png``, ``avatar.png``,
        ``mascot.png`` and the two logo PNGs.
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
    written.extend(
        [
            _resized_webp(source / "avatar.png", target / "avatar.webp", _AVATAR_SIZE),
            _sticker(source / "mascot.png", target / "mascot-sticker.webp"),
        ]
    )

    for path in written:
        _LOGGER.info("wrote %s (%d bytes)", path, path.stat().st_size)
    return written


def _resized_webp(source: pathlib.Path, target: pathlib.Path, size: tuple[int, int]) -> pathlib.Path:
    with Image.open(source) as image:
        image.load()
        # Pillow's resize stub leaks an Unknown into its size parameter.
        resized = image.resize(size, Image.Resampling.LANCZOS)  # type: ignore[reportUnknownMemberType]
        return _save_webp(resized, target, quality=90)


def _sticker(source: pathlib.Path, target: pathlib.Path) -> pathlib.Path:
    with Image.open(source) as image:
        mascot = image.convert("RGBA")

    pad = STICKER_PADDING
    canvas = Image.new("RGBA", (mascot.width + 2 * pad, mascot.height + 2 * pad), (0, 0, 0, 0))
    canvas.paste(mascot, (pad, pad))

    solid = _remap(canvas.getchannel("A"), _threshold(_OPAQUE_ABOVE))
    body = _figure(solid)
    grown = _remap(body.filter(ImageFilter.GaussianBlur(_STICKER_EDGE_WIDTH * 0.9)), _threshold(_GROWN_ABOVE))
    # A die-cut sticker has no holes: everything the outside cannot reach is part of it.
    ImageDraw.floodfill(grown, (0, 0), _FILL_MARK)
    grown = _remap(grown, [0 if value == _FILL_MARK else 255 for value in range(256)])

    sticker = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    sticker.putalpha(grown.filter(ImageFilter.GaussianBlur(1.1)))
    sticker.alpha_composite(canvas)

    width = round(sticker.width * _STICKER_HEIGHT / sticker.height)
    # Pillow's resize stub leaks an Unknown into its size parameter.
    resized = sticker.resize((width, _STICKER_HEIGHT), Image.Resampling.LANCZOS)  # type: ignore[reportUnknownMemberType]
    return _save_webp(resized, target, quality=90)


def _remap(image: Image.Image, table: list[int]) -> Image.Image:
    # Pillow's point stub leaks an Unknown into its lookup-table parameter.
    return image.point(table)  # type: ignore[reportUnknownMemberType]


def _threshold(limit: int) -> list[int]:
    return [255 if value > limit else 0 for value in range(256)]


def _figure(solid: Image.Image) -> Image.Image:
    left, top, right, bottom = solid.getbbox() or (0, 0, 0, 0)
    seed = _nearest_opaque(solid, (left + right) // 2, (top + bottom) // 2)

    marked = solid.copy()
    ImageDraw.floodfill(marked, seed, _FILL_MARK)
    histogram = marked.histogram()
    if histogram[_FILL_MARK] < _FIGURE_MIN_SHARE * (histogram[_FILL_MARK] + histogram[255]):
        message = f"the region around {seed} is not the figure: it covers under half of the opaque pixels"
        raise ValueError(message)
    return _remap(marked, [255 if value == _FILL_MARK else 0 for value in range(256)])


def _nearest_opaque(solid: Image.Image, x: int, y: int) -> tuple[int, int]:
    for radius in range(max(solid.size)):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                px, py = x + dx, y + dy
                if 0 <= px < solid.width and 0 <= py < solid.height and solid.getpixel((px, py)):
                    return px, py
    message = "the image has no opaque pixel"
    raise ValueError(message)


def _save_webp(image: Image.Image, target: pathlib.Path, *, quality: int) -> pathlib.Path:
    image.save(target, format="WEBP", quality=quality, exif=b"", icc_profile=None, xmp=b"")
    return target


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = pathlib.Path(__file__).resolve().parent.parent / "assets"
    optimize(root, root / "web")


if __name__ == "__main__":
    main()
