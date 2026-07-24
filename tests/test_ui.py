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

import typing
import unittest.mock

import hikari
import pytest

import risa
from risa import ui
from tests.helpers import RecordingStore
from tests.helpers import StubClient
from tests.helpers import find_custom_ids
from tests.helpers import interaction
from tests.helpers import mock_of


@risa.register(name="ui-picker", version=1)
class Picker(risa.View):
    chosen: typing.ClassVar[list[tuple[str, list[str]]]] = []

    @typing.override
    def render(self) -> ui.Layout:
        return (
            ui.Container(
                ui.TextDisplay("## pick things"),
                ui.Separator(divider=True, spacing=hikari.SpacingType.SMALL),
                ui.Section(
                    ui.TextDisplay("with a picture"),
                    accessory=ui.Thumbnail("https://example.invalid/t.png", description="thumb"),
                ),
                ui.Row(
                    ui.TextSelect(
                        self.choose.bind("colour"),
                        ui.SelectOption("Red"),
                        ui.SelectOption("Blue", "b", description="the cold one", default=True),
                        placeholder="pick a colour",
                        max_values=2,
                    ),
                ),
                ui.MediaGallery(
                    ui.MediaGalleryItem("https://example.invalid/1.png", description="one"),
                    ui.MediaGalleryItem("https://example.invalid/2.png", spoiler=True),
                ),
                ui.File("https://example.invalid/notes.txt", spoiler=True),
            ),
            ui.Row(
                ui.LinkButton("https://example.invalid/docs", label="Docs"),
                ui.PremiumButton(1234),
            ),
            ui.Row(ui.UserSelect(self.choose.bind("user"), placeholder="who?")),
            ui.Row(ui.RoleSelect(self.choose.bind("role"))),
            ui.Row(ui.MentionableSelect(self.choose.bind("any"))),
            ui.Row(
                ui.ChannelSelect(
                    self.choose.bind("channel"),
                    channel_types=[hikari.ChannelType.GUILD_TEXT],
                ),
            ),
        )

    @risa.handler
    async def choose(self, ctx: risa.ComponentContext, kind: str) -> None:
        type(self).chosen.append((kind, list(ctx.values)))


@pytest.fixture
def client() -> StubClient:
    built = StubClient(unittest.mock.Mock(spec=hikari.api.RESTClient), store=RecordingStore())
    built.add_view(Picker)
    return built


async def build_payloads(client: StubClient) -> list[dict[str, typing.Any]]:
    builders = await client.build(Picker())
    return [typing.cast("dict[str, typing.Any]", builder.build()[0]) for builder in builders]


async def test_every_top_level_component_builds(client: StubClient) -> None:
    payloads = await build_payloads(client)
    assert [payload["type"] for payload in payloads] == [
        int(hikari.ComponentType.CONTAINER),
        int(hikari.ComponentType.ACTION_ROW),
        int(hikari.ComponentType.ACTION_ROW),
        int(hikari.ComponentType.ACTION_ROW),
        int(hikari.ComponentType.ACTION_ROW),
        int(hikari.ComponentType.ACTION_ROW),
    ]


async def test_the_container_holds_every_inert_node(client: StubClient) -> None:
    container, *_ = await build_payloads(client)
    child_types = [child["type"] for child in container["components"]]
    assert child_types == [
        int(hikari.ComponentType.TEXT_DISPLAY),
        int(hikari.ComponentType.SEPARATOR),
        int(hikari.ComponentType.SECTION),
        int(hikari.ComponentType.ACTION_ROW),
        int(hikari.ComponentType.MEDIA_GALLERY),
        int(hikari.ComponentType.FILE),
    ]


async def test_the_separator_carries_its_options(client: StubClient) -> None:
    container, *_ = await build_payloads(client)
    separator = container["components"][1]
    assert separator["divider"] is True
    assert separator["spacing"] == int(hikari.SpacingType.SMALL)


async def test_the_thumbnail_is_the_section_accessory(client: StubClient) -> None:
    container, *_ = await build_payloads(client)
    section = container["components"][2]
    assert section["accessory"]["type"] == int(hikari.ComponentType.THUMBNAIL)
    assert section["accessory"]["description"] == "thumb"


async def test_the_text_select_serialises_its_options(client: StubClient) -> None:
    container, *_ = await build_payloads(client)
    select = container["components"][3]["components"][0]

    assert select["type"] == int(hikari.ComponentType.TEXT_SELECT_MENU)
    assert select["placeholder"] == "pick a colour"
    assert select["min_values"] == 1
    assert select["max_values"] == 2

    options = select["options"]
    assert [option["label"] for option in options] == ["Red", "Blue"]
    # An option's value defaults to its label.
    assert [option["value"] for option in options] == ["Red", "b"]
    assert options[1]["description"] == "the cold one"
    assert options[1]["default"] is True


async def test_the_media_gallery_serialises_its_items(client: StubClient) -> None:
    container, *_ = await build_payloads(client)
    gallery = container["components"][4]
    items = gallery["items"]
    assert len(items) == 2
    assert items[0]["description"] == "one"
    assert items[1]["spoiler"] is True


async def test_link_and_premium_buttons_carry_no_custom_id(client: StubClient) -> None:
    payloads = await build_payloads(client)
    link, premium = payloads[1]["components"]

    assert link["style"] == int(hikari.ButtonStyle.LINK)
    assert link["url"] == "https://example.invalid/docs"
    assert premium["style"] == int(hikari.ButtonStyle.PREMIUM)
    assert premium["sku_id"] == 1234
    assert "custom_id" not in link
    assert "custom_id" not in premium


async def test_every_select_kind_routes_under_a_custom_id(client: StubClient) -> None:
    payloads = await build_payloads(client)
    kinds = {
        int(hikari.ComponentType.USER_SELECT_MENU),
        int(hikari.ComponentType.ROLE_SELECT_MENU),
        int(hikari.ComponentType.MENTIONABLE_SELECT_MENU),
        int(hikari.ComponentType.CHANNEL_SELECT_MENU),
    }
    selects = [row["components"][0] for row in payloads[2:]]
    assert {select["type"] for select in selects} == kinds
    for select in selects:
        assert isinstance(select["custom_id"], str)


async def test_the_channel_select_carries_its_channel_types(client: StubClient) -> None:
    payloads = await build_payloads(client)
    channel_select = payloads[5]["components"][0]
    assert channel_select["channel_types"] == [int(hikari.ChannelType.GUILD_TEXT)]


async def test_a_select_dispatches_with_its_values(client: StubClient) -> None:
    payloads = await build_payloads(client)
    select_id = find_custom_ids(payloads[2])[0]

    inter = interaction(select_id)
    inter.values = ["111", "222"]
    Picker.chosen.clear()
    await client.dispatch(inter)

    assert Picker.chosen == [("user", ["111", "222"])]
    mock_of(inter.create_initial_response).assert_awaited_once_with(
        hikari.ResponseType.DEFERRED_MESSAGE_UPDATE,
        flags=hikari.UNDEFINED,
    )


def test_select_option_value_defaults_to_the_label() -> None:
    option = ui.SelectOption("Just a label")
    assert option.value == "Just a label"


def test_a_select_rejects_a_non_handler() -> None:
    async def loose(_: risa.ComponentContext) -> None: ...

    with pytest.raises(risa.NotAHandlerError):
        ui.UserSelect(loose)  # type: ignore[arg-type]
