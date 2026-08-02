from __future__ import annotations

import typing

import hikari

from risa import ui


def payload_of(builder: hikari.api.ComponentBuilder) -> typing.Mapping[str, typing.Any]:
    payload, _attachments = builder.build()
    return payload


def test_a_single_node_layout_builds_like_a_sequence_of_one() -> None:
    single = ui.build(ui.TextDisplay("hi"))
    listed = ui.build([ui.TextDisplay("hi")])

    assert len(single) == len(listed) == 1
    assert payload_of(single[0]) == payload_of(listed[0])


def test_a_text_display_carries_its_content() -> None:
    (built,) = ui.build(ui.TextDisplay("## hello"))
    payload = payload_of(built)

    assert payload["type"] == hikari.ComponentType.TEXT_DISPLAY
    assert payload["content"] == "## hello"


def test_separator_defaults() -> None:
    (built,) = ui.build(ui.Separator())
    payload = payload_of(built)

    assert payload["type"] == hikari.ComponentType.SEPARATOR
    assert payload["divider"] is True
    assert payload["spacing"] == hikari.SpacingType.SMALL


def test_a_container_nests_its_children_in_order() -> None:
    (built,) = ui.build(
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
    (built,) = ui.build(ui.Row(ui.LinkButton("https://example.invalid", label="open")))
    (button,) = payload_of(built)["components"]

    assert button["style"] == hikari.ButtonStyle.LINK
    assert button["url"] == "https://example.invalid"
    assert "custom_id" not in button


def test_a_premium_button_carries_its_sku() -> None:
    (built,) = ui.build(ui.Row(ui.PremiumButton(hikari.Snowflake(123))))
    (button,) = payload_of(built)["components"]

    assert button["style"] == hikari.ButtonStyle.PREMIUM
    assert button["sku_id"] == 123
    assert "custom_id" not in button


def test_a_section_builds_text_displays_and_its_accessory() -> None:
    (built,) = ui.build(
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
    (built,) = ui.build(
        ui.Section(
            ui.TextDisplay("left"),
            accessory=ui.LinkButton("https://example.invalid"),
        ),
    )
    payload = payload_of(built)

    assert payload["accessory"]["type"] == hikari.ComponentType.BUTTON


def test_a_media_gallery_builds_every_item() -> None:
    (built,) = ui.build(
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
    built = ui.build(
        [
            ui.Row(*[ui.LinkButton(f"https://example.invalid/{n}") for n in range(9)]),
            *[ui.TextDisplay(str(n)) for n in range(50)],
        ],
    )

    assert len(built) == 51
    assert len(payload_of(built[0])["components"]) == 9


def test_absent_optionals_are_omitted_from_the_payload() -> None:
    (built,) = ui.build(ui.Container(ui.TextDisplay("x")))
    payload = payload_of(built)

    assert "accent_color" not in payload
