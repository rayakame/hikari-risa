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

import hashlib
import pathlib
import shutil
import typing

import pytest
from PIL import Image

from tools import optimize_images

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "assets"

EXPECTED: dict[str, tuple[tuple[int, int], str]] = {
    "banner.webp": ((2064, 512), "WEBP"),
    "social-preview.png": ((1280, 640), "PNG"),
    "logo-mascot.webp": ((480, 480), "WEBP"),
    "logo-mascot-light.webp": ((480, 480), "WEBP"),
    "avatar.webp": ((128, 128), "WEBP"),
}
STICKER_HEIGHT = 1600
STICKER_PADDING = optimize_images.STICKER_PADDING
ORIGINALS = ("banner.png", "social-preview.png", "logo-mascot.png", "logo-mascot-light.png", "avatar.png", "mascot.png")


with Image.open(SOURCE / "mascot.png") as _mascot:
    MASCOT_HEIGHT = _mascot.height


def _copy_originals(target: pathlib.Path) -> pathlib.Path:
    target.mkdir()
    for name in ORIGINALS:
        shutil.copy(SOURCE / name, target / name)
    return target


@pytest.fixture
def assets(tmp_path: pathlib.Path) -> pathlib.Path:
    return _copy_originals(tmp_path / "assets")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    target = _copy_originals(tmp_path_factory.mktemp("built") / "assets")
    optimize_images.optimize(target, target / "web")
    return target


def _digests(directory: pathlib.Path, names: typing.Iterable[str]) -> dict[str, str]:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}


def test_every_web_copy_has_its_fixed_size_and_format(built: pathlib.Path) -> None:
    for name, (size, image_format) in EXPECTED.items():
        with Image.open(built / "web" / name) as image:
            assert image.size == size
            assert image.format == image_format


def test_the_mascots_keep_their_alpha_channel(built: pathlib.Path) -> None:
    for name in ("logo-mascot.webp", "logo-mascot-light.webp"):
        with Image.open(built / "web" / name) as image:
            assert "A" in image.getbands()


def test_the_social_preview_stays_under_githubs_megabyte(built: pathlib.Path) -> None:
    assert (built / "web" / "social-preview.png").stat().st_size < 1_000_000


def test_the_social_preview_carries_no_metadata(built: pathlib.Path) -> None:
    with Image.open(built / "web" / "social-preview.png") as image:
        assert not image.info.keys() - {"dpi"}


def test_the_originals_are_left_byte_for_byte(built: pathlib.Path) -> None:
    assert _digests(built, ORIGINALS) == _digests(SOURCE, ORIGINALS)


def test_running_twice_gives_identical_files(built: pathlib.Path, assets: pathlib.Path) -> None:
    second = optimize_images.optimize(assets, assets / "web")
    names = [path.name for path in second]

    assert _digests(assets / "web", names) == _digests(built / "web", names)


def _sticker_at(sticker: Image.Image, x: int, y: int) -> tuple[int, int, int, int]:
    scale = STICKER_HEIGHT / (MASCOT_HEIGHT + 2 * STICKER_PADDING)
    return sticker.getpixel((round((x + STICKER_PADDING) * scale), round((y + STICKER_PADDING) * scale)))  # type: ignore[return-value]


def test_the_sticker_is_1600_pixels_tall_with_alpha(built: pathlib.Path) -> None:
    with Image.open(built / "web" / "mascot-sticker.webp") as image:
        assert image.format == "WEBP"
        assert image.height == STICKER_HEIGHT
        assert "A" in image.getbands()
        assert not image.info.keys() - {"background", "loop"}


def test_the_sticker_edge_is_white_and_surrounds_the_figure(built: pathlib.Path) -> None:
    with Image.open(built / "web" / "mascot-sticker.webp") as image:
        sticker = image.convert("RGBA")
    # The figure's left edge at row 1200 is x=90; 6 px further out is transparent in the artwork.
    assert min(_sticker_at(sticker, 84, 1200)) >= 250


def test_loose_petals_get_no_edge(built: pathlib.Path) -> None:
    with Image.open(built / "web" / "mascot-sticker.webp") as image:
        sticker = image.convert("RGBA")
    # A petal floating in the upper left; its topmost opaque pixel is (268, 215).
    assert _sticker_at(sticker, 268, 208)[3] == 0
