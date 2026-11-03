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
    "banner-social.jpg": ((2064, 512), "JPEG"),
    "logo-mascot.webp": ((480, 480), "WEBP"),
    "logo-mascot-light.webp": ((480, 480), "WEBP"),
    "avatar.webp": ((128, 128), "WEBP"),
}
ORIGINALS = ("banner.png", "logo-mascot.png", "logo-mascot-light.png", "avatar.png")


@pytest.fixture
def assets(tmp_path: pathlib.Path) -> pathlib.Path:
    target = tmp_path / "assets"
    target.mkdir()
    for name in ORIGINALS:
        shutil.copy(SOURCE / name, target / name)
    return target


def _digests(directory: pathlib.Path, names: typing.Iterable[str]) -> dict[str, str]:
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}


def test_every_web_copy_has_its_fixed_size_and_format(assets: pathlib.Path) -> None:
    optimize_images.optimize(assets, assets / "web")

    for name, (size, image_format) in EXPECTED.items():
        with Image.open(assets / "web" / name) as image:
            assert image.size == size
            assert image.format == image_format


def test_the_mascots_keep_their_alpha_channel(assets: pathlib.Path) -> None:
    optimize_images.optimize(assets, assets / "web")

    for name in ("logo-mascot.webp", "logo-mascot-light.webp"):
        with Image.open(assets / "web" / name) as image:
            assert "A" in image.getbands()


def test_the_social_banner_stays_under_a_megabyte(assets: pathlib.Path) -> None:
    optimize_images.optimize(assets, assets / "web")

    assert (assets / "web" / "banner-social.jpg").stat().st_size < 1_000_000


def test_the_originals_are_left_byte_for_byte(assets: pathlib.Path) -> None:
    before = _digests(assets, ORIGINALS)

    optimize_images.optimize(assets, assets / "web")

    assert _digests(assets, ORIGINALS) == before


def test_running_twice_gives_identical_files(assets: pathlib.Path) -> None:
    first = optimize_images.optimize(assets, assets / "web")
    names = [path.name for path in first]
    before = _digests(assets / "web", names)

    optimize_images.optimize(assets, assets / "web")

    assert _digests(assets / "web", names) == before
