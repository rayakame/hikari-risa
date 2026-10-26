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
import typing

import hikari
import msgspec

import risa
from risa import codecs
from risa.internal import codec
from risa.internal import reader
from risa.state import anchor

if typing.TYPE_CHECKING:
    import types

    import pytest

POLL = codec.make_cookie("poll", 1)
HELP = codec.make_cookie("help", 1)


def encoded(index: int, fragment: str, *, cookie: str = POLL, wire_codec: codecs.Codec = codecs.BASE85) -> str:
    return codec.CustomID(
        cookie=cookie,
        handler=codec.make_handler_token("vote", 1),
        fragment_index=index,
        fragment=fragment,
        tail="",
        codec=wire_codec,
    ).encode()


def custom_id(
    index: int,
    data: bytes,
    *,
    cookie: str = POLL,
    wire_codec: codecs.Codec = codecs.BASE85,
) -> str:
    return encoded(index, wire_codec.encode(data), cookie=cookie, wire_codec=wire_codec)


def button(raw: str | None) -> hikari.ButtonComponent:
    return hikari.ButtonComponent(
        type=hikari.ComponentType.BUTTON,
        id=0,
        style=hikari.ButtonStyle.PRIMARY if raw else hikari.ButtonStyle.LINK,
        label="click",
        emoji=None,
        custom_id=raw,
        url=None if raw else "https://example.com",
        is_disabled=False,
    )


def select(raw: str) -> hikari.TextSelectMenuComponent:
    return hikari.TextSelectMenuComponent(
        type=hikari.ComponentType.TEXT_SELECT_MENU,
        id=0,
        custom_id=raw,
        placeholder=None,
        min_values=1,
        max_values=1,
        is_disabled=False,
        options=[],
    )


def row(*children: hikari.PartialComponent) -> hikari.ActionRowComponent[hikari.PartialComponent]:
    return hikari.ActionRowComponent(type=hikari.ComponentType.ACTION_ROW, id=0, components=list(children))


def text(content: str) -> hikari.TextDisplayComponent:
    return hikari.TextDisplayComponent(type=hikari.ComponentType.TEXT_DISPLAY, id=0, content=content)


def section(accessory: hikari.ButtonComponent) -> hikari.SectionComponent:
    return hikari.SectionComponent(
        type=hikari.ComponentType.SECTION,
        id=0,
        components=[text("option")],
        accessory=accessory,
    )


def container(*children: hikari.ContainerTypesT) -> hikari.ContainerComponent:
    return hikari.ContainerComponent(
        type=hikari.ComponentType.CONTAINER,
        id=0,
        accent_color=None,
        is_spoiler=False,
        components=list(children),
    )


def test_a_row_yields_every_fragment_by_its_index() -> None:
    message = [row(button(custom_id(0, b"ab")), button(custom_id(1, b"cd")), button(custom_id(2, b"ef")))]

    assert reader.fragments(message, cookie=POLL) == {0: b"ab", 1: b"cd", 2: b"ef"}


def test_leaves_inside_containers_sections_and_selects_are_found() -> None:
    message = [
        container(text("## poll"), row(button(custom_id(0, b"ab"))), section(button(custom_id(1, b"cd")))),
        row(select(custom_id(2, b"ef"))),
    ]

    assert reader.fragments(message, cookie=POLL) == {0: b"ab", 1: b"cd", 2: b"ef"}


def test_foreign_ids_and_link_buttons_are_skipped() -> None:
    message = [row(button("miru:vote:3"), button(None), button(custom_id(0, b"ab")))]

    assert reader.fragments(message, cookie=POLL) == {0: b"ab"}


def test_another_views_components_are_skipped() -> None:
    message = [
        row(button(custom_id(0, b"poll-0")), button(custom_id(1, b"poll-1"))),
        row(button(custom_id(0, b"help-0", cookie=HELP)), button(custom_id(1, b"help-1", cookie=HELP))),
    ]

    assert reader.fragments(message, cookie=POLL) == {0: b"poll-0", 1: b"poll-1"}
    assert reader.fragments(message, cookie=HELP) == {0: b"help-0", 1: b"help-1"}


def test_empty_fragments_keep_their_index() -> None:
    message = [row(button(custom_id(0, b"ab")), button(custom_id(1, b"")))]

    assert reader.fragments(message, cookie=POLL) == {0: b"ab", 1: b""}


def test_a_message_without_the_cookie_yields_an_empty_mapping() -> None:
    message = [row(button(custom_id(0, b"ab", cookie=HELP)))]

    assert reader.fragments(message, cookie=POLL) == {}


def test_a_repeated_index_with_the_same_bytes_is_harmless() -> None:
    parked = [row(button(custom_id(0, b"ab")), button(custom_id(1, b"")), button(custom_id(1, b"")))]
    doubled = [row(button(custom_id(0, b"ab"))), row(button(custom_id(0, b"ab")))]

    assert reader.fragments(parked, cookie=POLL) == {0: b"ab", 1: b""}
    assert reader.fragments(doubled, cookie=POLL) == {0: b"ab"}


def test_a_repeated_index_with_different_bytes_reads_as_torn() -> None:
    message = [row(button(custom_id(0, b"first"))), row(button(custom_id(0, b"second")))]

    assert reader.fragments(message, cookie=POLL) is None


def test_an_undecodable_fragment_reads_as_torn() -> None:
    message = [row(button(custom_id(0, b"ab")), button(encoded(1, "x")))]

    assert reader.fragments(message, cookie=POLL) is None


def test_each_fragment_is_read_in_its_own_components_codec() -> None:
    message = [
        row(
            button(custom_id(0, b"dense", wire_codec=codecs.BASE32768)),
            button(custom_id(1, b"ascii", wire_codec=codecs.BASE85)),
        ),
    ]

    assert reader.fragments(message, cookie=POLL) == {0: b"dense", 1: b"ascii"}


def test_a_fragment_in_an_uninstalled_codec_names_the_codec(monkeypatch: pytest.MonkeyPatch) -> None:
    message = [row(button(custom_id(0, b"dense", wire_codec=codecs.BASE32768)))]
    real = importlib.import_module

    def missing(name: str, package: str | None = None) -> types.ModuleType:
        if name.startswith("radixly"):
            raise ImportError(name)
        return real(name, package)

    monkeypatch.setattr(importlib, "import_module", missing)
    result = reader.fragments(message, cookie=POLL)

    assert isinstance(result, risa.CodecUnavailableError)
    assert result.codec_name == "base32768"
    assert result.extra == "radixly"


def test_a_carved_anchor_gathers_back_from_the_message() -> None:
    raw = anchor.MessageAnchor("aB7", 4, msgspec.msgpack.encode(["Ship it?", {"Red": 3, "Blue": 1}])).pack()
    pieces = anchor.carve(raw, [12, 12, 89], wire_codec=codecs.BASE85, view_name="poll")
    message = [container(row(*(button(encoded(index, piece)) for index, piece in enumerate(pieces))))]

    found = reader.fragments(message, cookie=POLL)

    assert found is not None
    assert not isinstance(found, risa.CodecUnavailableError)
    assert anchor.gather(found) == raw
