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

import importlib
import secrets
import typing

import pytest

import risa
from risa import codecs
from risa.internal import wire

if typing.TYPE_CHECKING:
    import types


def rival(tag: str, name: str = "rival") -> codecs.Codec:
    return codecs.Codec(
        tag=tag,
        name=name,
        encode=codecs.BASE85.encode,
        decode=codecs.BASE85.decode,
        max_bytes=codecs.BASE85.max_bytes,
    )


@pytest.fixture
def scratch_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codecs, "_REGISTRY", dict(codecs._REGISTRY))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


@pytest.fixture
def without_radixly(monkeypatch: pytest.MonkeyPatch) -> None:
    real = importlib.import_module

    def missing(name: str, package: str | None = None) -> types.ModuleType:
        if name.startswith("radixly"):
            raise ImportError(name)
        return real(name, package)

    monkeypatch.setattr(importlib, "import_module", missing)


def test_the_codecs_are_the_public_surface() -> None:
    assert risa.codecs is codecs
    assert isinstance(codecs.BASE85, codecs.Codec)
    assert isinstance(codecs.BASE32768, codecs.Codec)
    assert isinstance(codecs.URO14, codecs.Codec)


@pytest.mark.parametrize("size", range(40))
def test_base85_survives_the_round_trip(size: int) -> None:
    data = secrets.token_bytes(size)

    assert codecs.BASE85.decode(codecs.BASE85.encode(data)) == data


@pytest.mark.parametrize("chars", range(100))
def test_base85_max_bytes_is_exact(chars: int) -> None:
    fits = codecs.BASE85.max_bytes(chars)

    assert len(codecs.BASE85.encode(bytes(fits))) <= chars
    assert len(codecs.BASE85.encode(bytes(fits + 1))) > chars


@pytest.mark.parametrize("text", ['"', "not base85 at all!", "abcdef", "一丁"])
def test_base85_refuses_text_it_did_not_write(text: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        codecs.BASE85.decode(text)


def test_the_default_codec_writes_only_wire_alphabet_characters() -> None:
    assert all(char in wire.ALPHABET for char in codecs.BASE85.encode(bytes(range(256))))


@pytest.mark.parametrize("dense", [codecs.BASE32768, codecs.URO14])
def test_a_radixly_codec_round_trips_and_more_than_doubles_the_payload(dense: codecs.Codec) -> None:
    data = secrets.token_bytes(dense.max_bytes(88))

    text = dense.encode(data)

    assert len(text) <= 88
    assert dense.decode(text) == data
    assert dense.max_bytes(88) > 2 * codecs.BASE85.max_bytes(88)


def test_a_radixly_codec_refuses_text_it_did_not_write() -> None:
    with pytest.raises(ValueError, match=r"."):
        codecs.BASE32768.decode("plain ascii")


def test_the_built_in_tags_are_distinct_wire_characters() -> None:
    tags = [codecs.BASE85.tag, codecs.BASE32768.tag, codecs.URO14.tag]

    assert len(set(tags)) == len(tags)
    assert all(len(tag) == 1 and tag in wire.ALPHABET for tag in tags)


def test_by_tag_finds_the_built_ins() -> None:
    assert codecs.by_tag(codecs.BASE85.tag) is codecs.BASE85
    assert codecs.by_tag(codecs.BASE32768.tag) is codecs.BASE32768
    assert codecs.by_tag(codecs.URO14.tag) is codecs.URO14
    assert codecs.by_tag("\x00") is None


@pytest.mark.parametrize("tag", ["", "ab", '"', "\\", "é"])
def test_a_codec_tag_is_exactly_one_wire_character(tag: str) -> None:
    with pytest.raises(ValueError, match="tag"):
        rival(tag)


def test_a_codec_needs_a_name() -> None:
    with pytest.raises(ValueError, match="name"):
        rival("~", name="")


@pytest.mark.usefixtures("scratch_registry")
def test_registering_the_same_codec_twice_is_fine() -> None:
    codecs.register(codecs.BASE85)
    codecs.register(codecs.BASE85)

    assert codecs.by_tag(codecs.BASE85.tag) is codecs.BASE85


@pytest.mark.usefixtures("scratch_registry")
def test_a_tag_cannot_be_taken_twice() -> None:
    with pytest.raises(ValueError, match="already belongs"):
        codecs.register(rival(codecs.BASE85.tag))


@pytest.mark.usefixtures("scratch_registry")
def test_a_custom_codec_becomes_readable_once_registered() -> None:
    mine = rival("~", name="mine")

    codecs.register(mine)

    assert codecs.by_tag("~") is mine


@pytest.mark.usefixtures("without_radixly")
def test_a_missing_radixly_is_named_with_its_install_line() -> None:
    with pytest.raises(risa.CodecUnavailableError) as exc_info:
        codecs.BASE32768.encode(b"x")

    assert exc_info.value.codec_name == "base32768"
    assert exc_info.value.extra == "radixly"
    assert "pip install hikari-risa[radixly]" in str(exc_info.value)


@pytest.mark.usefixtures("without_radixly")
def test_availability_is_checked_without_encoding_anything() -> None:
    codecs.ensure_available(codecs.BASE85)
    with pytest.raises(risa.CodecUnavailableError):
        codecs.ensure_available(codecs.URO14)


@pytest.mark.usefixtures("without_radixly")
def test_a_view_cannot_declare_a_codec_that_is_not_installed() -> None:
    class Dense(risa.View):
        pass

    with pytest.raises(risa.CodecUnavailableError):
        risa.register(name="codecs-missing", state=risa.InMessage(codec=codecs.BASE32768))(Dense)


@pytest.mark.usefixtures("scratch_registry")
def test_a_view_cannot_reuse_a_taken_tag() -> None:
    class Rival(risa.View):
        pass

    with pytest.raises(risa.ViewDeclarationError, match="already belongs"):
        risa.register(name="codecs-rival", state=risa.InMessage(codec=rival(codecs.BASE85.tag)))(Rival)


@pytest.mark.usefixtures("scratch_registry")
def test_a_view_registers_its_custom_codec_for_reading() -> None:
    class Custom(risa.View):
        pass

    mine = rival("~", name="mine")

    risa.register(name="codecs-custom", state=risa.InMessage(codec=mine))(Custom)

    assert codecs.by_tag("~") is mine
