from __future__ import annotations

import typing
import unittest.mock

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


@risa.register(name="test-nodes-rival")
class Rival(risa.View):
    @risa.handler
    async def press(self, _ctx: risa.ComponentContext) -> None:
        pass


@risa.register(name="test-nodes-panel-sub")
class SubPanel(Panel):
    pass


@risa.register(name="test-nodes-wired")
class WiredPanel(risa.View):
    @risa.handler
    async def vote(self, _ctx: risa.ComponentContext, option: str, count: int = 1) -> None:
        pass


_META = registry.ViewMeta(cls=Canvas, name="test-nodes", version=1)


def meta_of(cls: type[risa.View]) -> registry.ViewMeta:
    return typing.cast("registry.ViewMeta", getattr(cls, constants.VIEW_META))


def engine(handler: object) -> risa.HandlerBinder:
    assert isinstance(handler, risa.HandlerBinder)
    return handler


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
    assert custom_id.cookie == meta_of(Panel).cookie
    assert custom_id.handler == Panel.press.token
    assert custom_id.fragment_index == 0
    assert not custom_id.fragment
    assert not custom_id.tail


def test_a_bare_handler_and_its_bind_build_identically() -> None:
    (bare,) = ui.build(ui.Row(ui.Button(Panel().press, label="go")), meta_of(Panel))
    (bound,) = ui.build(ui.Row(ui.Button(engine(Panel().press).bind(), label="go")), meta_of(Panel))

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


def test_a_colliding_token_from_another_view_is_still_rejected() -> None:
    assert Rival.press.token == Panel.press.token

    with pytest.raises(risa.LayoutError) as exc_info:
        ui.build(ui.Row(ui.Button(Rival().press)), meta_of(Panel))

    assert "belongs to view 'Rival'" in exc_info.value.reason


def test_an_inherited_handler_builds_on_the_subclass_view() -> None:
    (built,) = ui.build(ui.Row(ui.Button(SubPanel().press)), meta_of(SubPanel))
    (button,) = payload_of(built)["components"]

    custom_id = parsed(button["custom_id"])
    assert custom_id.cookie == meta_of(SubPanel).cookie
    assert custom_id.handler == Panel.press.token


def test_a_hand_built_bound_handler_falls_back_to_the_token_check() -> None:
    unrecognised = "zz"
    unknown = risa.Binding(handler_id="ghost", version=1, token=unrecognised, payload="")

    with pytest.raises(risa.LayoutError) as exc_info:
        ui.build(ui.Row(ui.Button(unknown)), meta_of(Panel))

    assert "is not on this view" in exc_info.value.reason


def test_an_oversized_payload_overflows_the_custom_id() -> None:
    oversized = risa.Binding(handler_id="press", version=1, token=Panel.press.token, payload="x" * 90)

    with pytest.raises(risa.CustomIdOverflowError):
        ui.build(ui.Row(ui.Button(oversized)), meta_of(Panel))


def test_something_that_is_not_a_handler_is_rejected() -> None:
    with pytest.raises(risa.NotAHandlerError) as exc_info:
        ui.Button("close")  # type: ignore[reportArgumentType]

    assert exc_info.value.type_name == "str"


def test_a_partial_bakes_its_args_into_the_custom_id_tail() -> None:
    view = WiredPanel()

    (built,) = ui.build(ui.Row(ui.Button(risa.bind(view.vote, "Red", 5), label="Red")), meta_of(WiredPanel))
    (button,) = payload_of(built)["components"]

    assert parsed(button["custom_id"]).tail == engine(view.vote).bind("Red", 5).payload


def test_nested_partials_flatten_into_one_binding() -> None:
    view = WiredPanel()

    (curried,) = ui.build(ui.Row(ui.Button(risa.bind(risa.bind(view.vote, "Red"), 5))), meta_of(WiredPanel))
    (direct,) = ui.build(ui.Row(ui.Button(risa.bind(view.vote, "Red", 5))), meta_of(WiredPanel))

    assert payload_of(curried) == payload_of(direct)


def test_a_partial_of_something_else_is_rejected() -> None:
    with pytest.raises(risa.NotAHandlerError) as exc_info:
        ui.Button(risa.bind(print))  # type: ignore[reportArgumentType]

    assert exc_info.value.type_name == "builtin_function_or_method"


def test_bad_bound_values_fail_at_the_render_site() -> None:
    with pytest.raises(risa.ArgBindError) as exc_info:
        ui.Button(risa.bind(WiredPanel().vote, 5))  # type: ignore[reportArgumentType]

    assert exc_info.value.parameter == "option"
    assert "expected a str" in str(exc_info.value)


def test_a_bare_handler_with_required_args_fails_at_render() -> None:
    with pytest.raises(risa.ArgBindError) as exc_info:
        ui.Button(WiredPanel().vote)

    assert exc_info.value.parameter == "option"
    assert "required" in str(exc_info.value)


def test_a_legal_frame_can_still_overflow_the_custom_id() -> None:
    with pytest.raises(risa.CustomIdOverflowError) as exc_info:
        ui.build(ui.Row(ui.Button(risa.bind(WiredPanel().vote, "x" * 70))), meta_of(WiredPanel))

    assert "Row[0] > Button[0]" in exc_info.value.view_name
    assert "vote" in exc_info.value.view_name


def rendered_text() -> ui.Rendered:
    return ui.Rendered(build(ui.TextDisplay("hi")))


def test_rendered_is_a_mapping_of_send_kwargs() -> None:
    rendered = rendered_text()

    assert dict(rendered) == {"components": rendered.components}
    assert list(rendered) == ["components"]
    assert len(rendered.components) == 1


async def test_rendered_spreads_into_a_rest_send_call() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)

    await rest.create_message(hikari.Snowflake(123), **rendered)

    rest.create_message.assert_awaited_once_with(hikari.Snowflake(123), components=rendered.components)


async def test_send_to_sends_the_components() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)

    await rendered.send_to(rest, hikari.Snowflake(123))

    rest.create_message.assert_awaited_once_with(hikari.Snowflake(123), components=rendered.components)


async def test_respond_to_answers_with_the_initial_message_create() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    interaction = unittest.mock.Mock(spec=hikari.ComponentInteraction)

    await rendered.respond_to(rest, interaction, ephemeral=True)

    call = rest.create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.MESSAGE_CREATE
    assert call.kwargs["components"] == rendered.components
    assert call.kwargs["flags"] is hikari.MessageFlag.EPHEMERAL


async def test_content_alongside_a_rendered_view_is_rejected_with_the_v2_message() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)

    with pytest.raises(TypeError, match="TextDisplay"):
        await rendered.send_to(rest, hikari.Snowflake(123), content="hi")  # type: ignore[reportArgumentType]

    rest.create_message.assert_not_called()


async def test_embeds_alongside_a_rendered_view_are_rejected_too() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    interaction = unittest.mock.Mock(spec=hikari.ComponentInteraction)

    with pytest.raises(TypeError, match="embeds"):
        await rendered.respond_to(rest, interaction, embeds=[])  # type: ignore[reportArgumentType]

    rest.create_interaction_response.assert_not_called()


def test_a_text_select_routes_and_serializes_its_options() -> None:
    (built,) = ui.build(
        ui.Row(
            ui.TextSelect(
                Panel().press,
                ui.SelectOption("Red", description="warm"),
                ui.SelectOption("Blue", "b", default=True),
                placeholder="pick",
                max_values=2,
            ),
        ),
        meta_of(Panel),
    )
    (select,) = payload_of(built)["components"]

    assert select["type"] == hikari.ComponentType.TEXT_SELECT_MENU
    custom_id = parsed(select["custom_id"])
    assert custom_id.cookie == meta_of(Panel).cookie
    assert custom_id.handler == Panel.press.token
    assert select["placeholder"] == "pick"
    assert select["min_values"] == 1
    assert select["max_values"] == 2

    first, second = select["options"]
    assert first["label"] == "Red"
    assert first["value"] == "Red"
    assert first["description"] == "warm"
    assert second["value"] == "b"
    assert second["default"] is True


@pytest.mark.parametrize(
    ("node_type", "component_type"),
    [
        (ui.UserSelect, hikari.ComponentType.USER_SELECT_MENU),
        (ui.RoleSelect, hikari.ComponentType.ROLE_SELECT_MENU),
        (ui.MentionableSelect, hikari.ComponentType.MENTIONABLE_SELECT_MENU),
    ],
)
def test_an_entity_select_emits_its_type_and_routes(
    node_type: type[ui.UserSelect | ui.RoleSelect | ui.MentionableSelect],
    component_type: hikari.ComponentType,
) -> None:
    (built,) = ui.build(ui.Row(node_type(Panel().press, placeholder="who")), meta_of(Panel))
    (select,) = payload_of(built)["components"]

    assert select["type"] == component_type
    assert select["placeholder"] == "who"
    assert parsed(select["custom_id"]).handler == Panel.press.token


def test_a_channel_select_defaults_to_no_filter() -> None:
    (built,) = ui.build(ui.Row(ui.ChannelSelect(Panel().press, disabled=True)), meta_of(Panel))
    (select,) = payload_of(built)["components"]

    assert select["type"] == hikari.ComponentType.CHANNEL_SELECT_MENU
    assert select["channel_types"] == []
    assert select["disabled"] is True


def test_a_channel_select_filters_channel_types() -> None:
    (built,) = ui.build(
        ui.Row(ui.ChannelSelect(Panel().press, channel_types=[hikari.ChannelType.GUILD_TEXT])),
        meta_of(Panel),
    )
    (select,) = payload_of(built)["components"]

    assert select["channel_types"] == [hikari.ChannelType.GUILD_TEXT]


def test_selects_count_in_tree_order_with_buttons() -> None:
    built = ui.build(
        [
            ui.Row(ui.Button(Panel().press), ui.UserSelect(Panel().press)),
            ui.Row(ui.TextSelect(Panel().press, ui.SelectOption("x"))),
        ],
        meta_of(Panel),
    )

    first, second = payload_of(built[0])["components"]
    (third,) = payload_of(built[1])["components"]
    indices = [parsed(component["custom_id"]).fragment_index for component in (first, second, third)]
    assert indices == [0, 1, 2]


def test_a_foreign_handler_on_a_select_is_rejected_with_its_path() -> None:
    layout = ui.Row(ui.UserSelect(Elsewhere().other))

    with pytest.raises(risa.LayoutError) as exc_info:
        ui.build(layout, meta_of(Panel))

    assert exc_info.value.path == "Row[0] > UserSelect[0]"


def test_a_select_rejects_something_that_is_not_a_handler() -> None:
    with pytest.raises(risa.NotAHandlerError) as exc_info:
        ui.UserSelect("nope")  # type: ignore[reportArgumentType]

    assert exc_info.value.type_name == "str"
