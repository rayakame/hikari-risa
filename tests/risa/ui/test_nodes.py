from __future__ import annotations

import typing

import hikari
import pytest

import risa
from risa import ui
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry


class Canvas(risa.View):
    pass


@risa.register(name="test-nodes-panel")
class Panel(risa.View):
    @risa.handler
    async def press(self, _ctx: risa.ComponentContext) -> None:
        pass


class Elsewhere(risa.View):
    @risa.handler
    async def other(self, _ctx: risa.ComponentContext) -> None:
        pass


_META = registry.ViewMeta(cls=Canvas, name="test-nodes", version=1)


def meta_of(cls: type[risa.View]) -> registry.ViewMeta:
    return typing.cast("registry.ViewMeta", getattr(cls, constants.VIEW_META))


def parsed(raw: str) -> codec.CustomID:
    custom_id = codec.parse_custom_id(raw)
    assert custom_id is not None
    return custom_id


def build(layout: ui.Layout) -> typing.Sequence[hikari.api.ComponentBuilder]:
    return ui.build(layout, _META)


def payload_of(builder: hikari.api.ComponentBuilder) -> typing.Mapping[str, typing.Any]:
    payload, _attachments = builder.build()
    return payload


def test_a_single_node_layout_builds_like_a_sequence_of_one() -> None:
    single = build(ui.TextDisplay("hi"))
    listed = build([ui.TextDisplay("hi")])

    assert len(single) == len(listed) == 1
    assert payload_of(single[0]) == payload_of(listed[0])


def test_a_text_display_carries_its_content() -> None:
    (built,) = build(ui.TextDisplay("## hello"))
    payload = payload_of(built)

    assert payload["type"] == hikari.ComponentType.TEXT_DISPLAY
    assert payload["content"] == "## hello"


def test_separator_defaults() -> None:
    (built,) = build(ui.Separator())
    payload = payload_of(built)

    assert payload["type"] == hikari.ComponentType.SEPARATOR
    assert payload["divider"] is True
    assert payload["spacing"] == hikari.SpacingType.SMALL


def test_a_container_nests_its_children_in_order() -> None:
    (built,) = build(
        ui.Container(
            ui.TextDisplay("title"),
            ui.Separator(divider=False, spacing=hikari.SpacingType.LARGE),
            ui.Row(ui.LinkButton("https://example.invalid", label="open")),
            accent_color=0xFF0000,
        ),
    )
    payload = payload_of(built)

    assert payload["type"] == hikari.ComponentType.CONTAINER
    assert payload["accent_color"] == hikari.Color.of(0xFF0000)
    types = [child["type"] for child in payload["components"]]
    assert types == [
        hikari.ComponentType.TEXT_DISPLAY,
        hikari.ComponentType.SEPARATOR,
        hikari.ComponentType.ACTION_ROW,
    ]


def test_a_link_button_never_carries_a_custom_id() -> None:
    (built,) = build(ui.Row(ui.LinkButton("https://example.invalid", label="open")))
    (button,) = payload_of(built)["components"]

    assert button["style"] == hikari.ButtonStyle.LINK
    assert button["url"] == "https://example.invalid"
    assert "custom_id" not in button


def test_a_premium_button_carries_its_sku() -> None:
    (built,) = build(ui.Row(ui.PremiumButton(hikari.Snowflake(123))))
    (button,) = payload_of(built)["components"]

    assert button["style"] == hikari.ButtonStyle.PREMIUM
    assert button["sku_id"] == 123
    assert "custom_id" not in button


def test_a_section_builds_text_displays_and_its_accessory() -> None:
    (built,) = build(
        ui.Section(
            ui.TextDisplay("left"),
            accessory=ui.Thumbnail("https://example.invalid/img.png", description="alt"),
        ),
    )
    payload = payload_of(built)

    assert payload["type"] == hikari.ComponentType.SECTION
    assert [c["type"] for c in payload["components"]] == [hikari.ComponentType.TEXT_DISPLAY]
    assert payload["accessory"]["type"] == hikari.ComponentType.THUMBNAIL
    assert payload["accessory"]["description"] == "alt"


def test_a_button_is_a_valid_section_accessory() -> None:
    (built,) = build(
        ui.Section(
            ui.TextDisplay("left"),
            accessory=ui.LinkButton("https://example.invalid"),
        ),
    )
    payload = payload_of(built)

    assert payload["accessory"]["type"] == hikari.ComponentType.BUTTON


def test_a_media_gallery_builds_every_item() -> None:
    (built,) = build(
        ui.MediaGallery(
            ui.MediaGalleryItem("https://example.invalid/a.png"),
            ui.MediaGalleryItem("https://example.invalid/b.png", spoiler=True),
        ),
    )
    payload = payload_of(built)

    assert payload["type"] == hikari.ComponentType.MEDIA_GALLERY
    assert len(payload["items"]) == 2
    assert payload["items"][1]["spoiler"] is True


def test_discords_numeric_limits_are_not_policed() -> None:
    built = build(
        [
            ui.Row(*[ui.LinkButton(f"https://example.invalid/{n}") for n in range(9)]),
            *[ui.TextDisplay(str(n)) for n in range(50)],
        ],
    )

    assert len(built) == 51
    assert len(payload_of(built[0])["components"]) == 9


def test_absent_optionals_are_omitted_from_the_payload() -> None:
    (built,) = build(ui.Container(ui.TextDisplay("x")))
    payload = payload_of(built)

    assert "accent_color" not in payload


def test_a_button_routes_through_a_risa_custom_id() -> None:
    (built,) = ui.build(ui.Row(ui.Button(Panel().press, label="go")), meta_of(Panel))
    (button,) = payload_of(built)["components"]

    custom_id = parsed(button["custom_id"])
    assert custom_id.cookie == meta_of(Panel).key
    assert custom_id.handler == Panel.press.token
    assert custom_id.fragment_index == 0
    assert not custom_id.fragment
    assert not custom_id.tail


def test_a_bare_handler_and_its_bind_build_identically() -> None:
    (bare,) = ui.build(ui.Row(ui.Button(Panel().press, label="go")), meta_of(Panel))
    (bound,) = ui.build(ui.Row(ui.Button(Panel().press.bind(), label="go")), meta_of(Panel))

    assert payload_of(bare) == payload_of(bound)


def test_fragment_indices_follow_tree_order() -> None:
    built = ui.build(
        [
            ui.Row(ui.Button(Panel().press), ui.Button(Panel().press)),
            ui.Section(ui.TextDisplay("x"), accessory=ui.Button(Panel().press)),
        ],
        meta_of(Panel),
    )

    first, second = payload_of(built[0])["components"]
    accessory = payload_of(built[1])["accessory"]
    indices = [parsed(component["custom_id"]).fragment_index for component in (first, second, accessory)]
    assert indices == [0, 1, 2]


def test_a_foreign_handler_is_rejected_with_its_path() -> None:
    layout = ui.Container(ui.Row(ui.LinkButton("https://example.invalid"), ui.Button(Elsewhere().other)))

    with pytest.raises(risa.LayoutError) as exc_info:
        ui.build(layout, meta_of(Panel))

    assert exc_info.value.path == "Container[0] > Row[0] > Button[1]"
    assert "other" in exc_info.value.reason


def test_an_oversized_payload_overflows_the_custom_id() -> None:
    oversized = risa.BoundHandler(handler_id="press", version=1, token=Panel.press.token, payload="x" * 90)

    with pytest.raises(risa.CustomIdOverflowError):
        ui.build(ui.Row(ui.Button(oversized)), meta_of(Panel))


def test_something_that_is_not_a_handler_is_rejected() -> None:
    with pytest.raises(risa.NotAHandlerError) as exc_info:
        ui.Button("close")  # type: ignore[reportArgumentType]

    assert exc_info.value.type_name == "str"
