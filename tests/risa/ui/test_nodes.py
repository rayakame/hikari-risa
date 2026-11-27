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

import inspect
import typing
import unittest.mock

import hikari
import pytest

import risa
from risa import codecs
from risa import ui
from risa.internal import codec
from risa.internal import constants
from risa.internal import registry
from risa.state import placement
from risa.state import schema as schema_
from risa.ui import build as build_


def emitted(layout: ui.Layout, meta: registry.ViewMeta) -> typing.Sequence[hikari.api.ComponentBuilder]:
    return build_.plan(layout, meta).emit(b"")


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


_META = registry.ViewMeta(
    cls=Canvas,
    name="test-nodes",
    version=1,
    schema=schema_.StateSchema.of(Canvas, view_name="test-nodes"),
    state=placement.DEFAULT_PLACEMENT,
)


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
    return emitted(layout, _META)


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


def test_a_file_builds_as_a_reference_to_its_attachment() -> None:
    (built,) = build(ui.File(hikari.Bytes(b"minutes", "notes.txt"), spoiler=True))
    payload, attachments = built.build()

    assert payload["type"] == hikari.ComponentType.FILE
    assert payload["file"] == {"url": "attachment://notes.txt"}
    assert payload["spoiler"] is True
    assert [attachment.filename for attachment in attachments] == ["notes.txt"]


_TEXT = ui.TextDisplay("hi")
_LINK = ui.LinkButton("https://example.com", label="Docs")
_ITEM = ui.MediaGalleryItem("https://example.com/a.png", description="a", spoiler=True)
_OPTION = ui.SelectOption("Ramen", "ramen", description="hot", emoji="🍜", default=True)


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        pytest.param(_TEXT, {"content": "hi"}, id="TextDisplay"),
        pytest.param(
            ui.Separator(divider=False, spacing=hikari.SpacingType.LARGE),
            {"divider": False, "spacing": hikari.SpacingType.LARGE},
            id="Separator",
        ),
        pytest.param(
            ui.Thumbnail("https://example.com/a.png", description="a", spoiler=True),
            {"media": "https://example.com/a.png", "description": "a", "spoiler": True},
            id="Thumbnail",
        ),
        pytest.param(
            _ITEM, {"media": "https://example.com/a.png", "description": "a", "spoiler": True}, id="MediaGalleryItem"
        ),
        pytest.param(ui.MediaGallery(_ITEM), {"items": (_ITEM,)}, id="MediaGallery"),
        pytest.param(ui.File("notes.txt", spoiler=True), {"file": "notes.txt", "spoiler": True}, id="File"),
        pytest.param(ui.Row(_LINK), {"components": (_LINK,)}, id="Row"),
        pytest.param(ui.Section(_TEXT, accessory=_LINK), {"text_displays": (_TEXT,), "accessory": _LINK}, id="Section"),
        pytest.param(
            ui.Container(_TEXT, accent_color=0xF680A1, spoiler=True),
            {"children": (_TEXT,), "accent_color": 0xF680A1, "spoiler": True},
            id="Container",
        ),
        pytest.param(
            ui.Button(Panel().press, label="Go", emoji="🚀", style=hikari.ButtonStyle.DANGER, disabled=True),
            {"label": "Go", "emoji": "🚀", "style": hikari.ButtonStyle.DANGER, "disabled": True},
            id="Button",
        ),
        pytest.param(
            ui.LinkButton("https://example.com", label="Docs", emoji="📖", disabled=True),
            {"url": "https://example.com", "label": "Docs", "emoji": "📖", "disabled": True},
            id="LinkButton",
        ),
        pytest.param(ui.PremiumButton(123, disabled=True), {"sku_id": 123, "disabled": True}, id="PremiumButton"),
        pytest.param(
            _OPTION,
            {"label": "Ramen", "value": "ramen", "description": "hot", "emoji": "🍜", "default": True},
            id="SelectOption",
        ),
        pytest.param(
            ui.TextSelect(Panel().press, _OPTION, placeholder="Pick", min_values=0, max_values=1, disabled=True),
            {"options": (_OPTION,), "placeholder": "Pick", "min_values": 0, "max_values": 1, "disabled": True},
            id="TextSelect",
        ),
        pytest.param(
            ui.ChannelSelect(Panel().press, channel_types=[hikari.ChannelType.GUILD_TEXT]),
            {"channel_types": (hikari.ChannelType.GUILD_TEXT,)},
            id="ChannelSelect",
        ),
    ],
)
def test_a_node_gives_back_what_it_was_built_with(node: object, expected: dict[str, object]) -> None:
    assert {name: getattr(node, name) for name in expected} == expected


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
    (built,) = emitted(ui.Row(ui.Button(Panel().press, label="go")), meta_of(Panel))
    (button,) = payload_of(built)["components"]

    custom_id = parsed(button["custom_id"])
    assert custom_id.cookie == meta_of(Panel).cookie
    assert custom_id.handler == Panel.press.token
    assert custom_id.fragment_index == 0
    assert not custom_id.fragment
    assert not custom_id.tail


def test_a_bare_handler_and_its_bind_build_identically() -> None:
    (bare,) = emitted(ui.Row(ui.Button(Panel().press, label="go")), meta_of(Panel))
    (bound,) = emitted(ui.Row(ui.Button(engine(Panel().press).bind(), label="go")), meta_of(Panel))

    assert payload_of(bare) == payload_of(bound)


def test_fragment_indices_follow_tree_order() -> None:
    built = emitted(
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
        emitted(layout, meta_of(Panel))

    assert exc_info.value.path == "Container[0] > Row[0] > Button[1]"
    assert "other" in exc_info.value.reason


def test_a_colliding_token_from_another_view_is_still_rejected() -> None:
    assert Rival.press.token == Panel.press.token

    with pytest.raises(risa.LayoutError) as exc_info:
        emitted(ui.Row(ui.Button(Rival().press)), meta_of(Panel))

    assert "belongs to view 'Rival'" in exc_info.value.reason


def test_an_inherited_handler_builds_on_the_subclass_view() -> None:
    (built,) = emitted(ui.Row(ui.Button(SubPanel().press)), meta_of(SubPanel))
    (button,) = payload_of(built)["components"]

    custom_id = parsed(button["custom_id"])
    assert custom_id.cookie == meta_of(SubPanel).cookie
    assert custom_id.handler == Panel.press.token


def test_a_hand_built_bound_handler_falls_back_to_the_token_check() -> None:
    unrecognised = "zz"
    unknown = risa.Binding(
        handler_id="ghost", version=1, token=unrecognised, fingerprint="", frames=(), func_name="ghost"
    )

    with pytest.raises(risa.LayoutError) as exc_info:
        emitted(ui.Row(ui.Button(unknown)), meta_of(Panel))

    assert "is not on this view" in exc_info.value.reason


def test_an_oversized_payload_overflows_the_custom_id() -> None:
    oversized = risa.Binding(
        handler_id="press",
        version=1,
        token=Panel.press.token,
        fingerprint="zz",
        frames=(risa.Frame("a", b"x" * 60), risa.Frame("b", b"x" * 60)),
        func_name="press",
    )

    with pytest.raises(risa.CustomIdOverflowError):
        emitted(ui.Row(ui.Button(oversized)), meta_of(Panel))


def test_something_that_is_not_a_handler_is_rejected() -> None:
    with pytest.raises(risa.NotAHandlerError) as exc_info:
        ui.Button("close")  # type: ignore[reportArgumentType]

    assert exc_info.value.type_name == "str"


def test_a_partial_bakes_its_args_into_the_custom_id_tail() -> None:
    view = WiredPanel()

    (built,) = emitted(ui.Row(ui.Button(risa.bind(view.vote, "Red", 5), label="Red")), meta_of(WiredPanel))
    (button,) = payload_of(built)["components"]

    bound = engine(view.vote).bind("Red", 5)
    expected = bound.fingerprint + codec.pack_frames([codecs.BASE85.encode(frame.data) for frame in bound.frames])
    assert parsed(button["custom_id"]).tail == expected


def test_a_dense_view_writes_its_tag_and_its_args_in_its_codec() -> None:
    @risa.register(name="test-nodes-dense", version=1, state=risa.InMessage(codec=codecs.BASE32768))
    class Dense(risa.View):
        @risa.handler
        async def pick(self, _ctx: risa.ComponentContext, option: str) -> None:
            pass

    view = Dense()
    (built,) = emitted(ui.Row(ui.Button(risa.bind(view.pick, "Red" * 10), label="x")), meta_of(Dense))
    (button,) = payload_of(built)["components"]

    custom_id = parsed(button["custom_id"])
    assert button["custom_id"][1] == codecs.BASE32768.tag
    assert custom_id.codec is codecs.BASE32768
    frames = codec.unpack_frames(custom_id.tail[codec.FINGERPRINT_LENGTH :])
    assert frames is not None
    assert codecs.BASE32768.decode(frames[0]) == b"Red" * 10
    assert len(frames[0]) < len(codecs.BASE85.encode(b"Red" * 10))


def test_nested_partials_flatten_into_one_binding() -> None:
    view = WiredPanel()

    (curried,) = emitted(ui.Row(ui.Button(risa.bind(risa.bind(view.vote, "Red"), 5))), meta_of(WiredPanel))
    (direct,) = emitted(ui.Row(ui.Button(risa.bind(view.vote, "Red", 5))), meta_of(WiredPanel))

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
        emitted(ui.Row(ui.Button(risa.bind(WiredPanel().vote, "x" * 70))), meta_of(WiredPanel))

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

    sent = await rendered.send_to(rest, hikari.Snowflake(123))

    assert sent is rest.create_message.return_value
    rest.create_message.assert_awaited_once_with(
        hikari.Snowflake(123),
        hikari.UNDEFINED,
        components=rendered.components,
    )


async def test_send_to_passes_the_other_arguments_of_hikaris_call_on() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)

    await rendered.send_to(
        rest,
        hikari.Snowflake(123),
        user_mentions=True,
        reply=hikari.Snowflake(7),
        flags=hikari.MessageFlag.SUPPRESS_NOTIFICATIONS,
    )

    rest.create_message.assert_awaited_once_with(
        hikari.Snowflake(123),
        hikari.UNDEFINED,
        components=rendered.components,
        user_mentions=True,
        reply=hikari.Snowflake(7),
        flags=hikari.MessageFlag.SUPPRESS_NOTIFICATIONS,
    )


async def test_what_the_send_helpers_pass_on_fits_the_signatures_of_hikaris_calls() -> None:
    rendered = ui.render(ui.Row(ui.LinkButton("https://example.com", label="Docs")))
    rest = unittest.mock.create_autospec(hikari.api.RESTClient, instance=True)
    interaction = unittest.mock.Mock(spec=hikari.CommandInteraction)

    await rendered.send_to(rest, hikari.Snowflake(123), "hi", embeds=[], reply=hikari.Snowflake(7), mentions_reply=True)
    await rendered.respond_to(rest, interaction, "hi", ephemeral=True, embeds=[], role_mentions=True)

    rest.create_message.assert_awaited_once()
    rest.create_interaction_response.assert_awaited_once()


@pytest.mark.parametrize(
    ("call", "options", "own"),
    [
        ("create_message", build_._MessageOptions, {"channel", "content"}),  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
        (
            "create_interaction_response",
            build_._ResponseOptions,  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
            {"interaction", "token", "response_type", "content", "flags"},
        ),
    ],
    ids=["send_to", "respond_to"],
)
def test_the_send_helpers_know_every_argument_of_hikaris_calls(call: str, options: type, own: set[str]) -> None:
    taken = set(inspect.signature(getattr(hikari.api.RESTClient, call)).parameters)

    assert taken - {"self", "component", "components"} - own <= set(options.__annotations__)


async def test_rows_alone_carry_content_and_an_embed_through_send_to() -> None:
    rendered = ui.render(ui.Row(ui.LinkButton("https://example.com", label="Docs")))
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    embed = hikari.Embed(title="Docs")

    await rendered.send_to(rest, hikari.Snowflake(123), "Read the docs", embed=embed)

    rest.create_message.assert_awaited_once_with(
        hikari.Snowflake(123),
        "Read the docs",
        components=rendered.components,
        embed=embed,
    )


async def test_rows_alone_carry_content_through_respond_to() -> None:
    rendered = ui.render(ui.Row(ui.LinkButton("https://example.com", label="Docs")))
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    interaction = unittest.mock.Mock(spec=hikari.CommandInteraction)

    await rendered.respond_to(rest, interaction, "Read the docs")

    call = rest.create_interaction_response.await_args
    assert call is not None
    assert call.args == (interaction.id, interaction.token, hikari.ResponseType.MESSAGE_CREATE, "Read the docs")


async def test_respond_to_passes_the_other_arguments_on_and_returns_what_hikari_returns() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    interaction = unittest.mock.Mock(spec=hikari.CommandInteraction)

    answered = await rendered.respond_to(rest, interaction, user_mentions=True, tts=True)

    assert answered is rest.create_interaction_response.return_value
    call = rest.create_interaction_response.await_args
    assert call is not None
    assert call.args == (interaction.id, interaction.token, hikari.ResponseType.MESSAGE_CREATE, hikari.UNDEFINED)
    assert call.kwargs == {
        "components": rendered.components,
        "flags": hikari.UNDEFINED,
        "user_mentions": True,
        "tts": True,
    }


@pytest.mark.parametrize(
    ("ephemeral", "flags", "sent"),
    [
        (True, hikari.UNDEFINED, hikari.MessageFlag.EPHEMERAL),
        (False, hikari.MessageFlag.SUPPRESS_EMBEDS, hikari.MessageFlag.SUPPRESS_EMBEDS),
        (
            True,
            hikari.MessageFlag.SUPPRESS_EMBEDS,
            hikari.MessageFlag.SUPPRESS_EMBEDS | hikari.MessageFlag.EPHEMERAL,
        ),
        (True, 4096, hikari.MessageFlag.SUPPRESS_NOTIFICATIONS | hikari.MessageFlag.EPHEMERAL),
    ],
    ids=["ephemeral", "flags", "both", "int"],
)
async def test_respond_to_adds_ephemeral_to_the_flags_it_is_given(
    ephemeral: bool,  # ruff:ignore[boolean-type-hint-positional-argument]
    flags: hikari.UndefinedType | int | hikari.MessageFlag,
    sent: hikari.MessageFlag,
) -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    interaction = unittest.mock.Mock(spec=hikari.CommandInteraction)

    await rendered.respond_to(rest, interaction, ephemeral=ephemeral, flags=flags)

    call = rest.create_interaction_response.await_args
    assert call is not None
    assert call.kwargs["flags"] == sent


@pytest.mark.parametrize(
    "beside",
    [
        {"content": "hi"},
        {"embed": hikari.Embed(title="x")},
        {"embeds": [hikari.Embed(title="x")]},
        {"content": ""},
        {"embed": None},
        {"embeds": []},
    ],
    ids=["content", "embed", "embeds", "empty-content", "no-embed", "empty-embeds"],
)
async def test_text_and_embeds_beside_components_v2_are_refused_before_anything_is_sent(
    beside: dict[str, typing.Any],
) -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    interaction = unittest.mock.Mock(spec=hikari.CommandInteraction)
    (name,) = beside

    with pytest.raises(TypeError, match=f"Components V2.*{name} cannot ride"):
        await rendered.send_to(rest, hikari.Snowflake(123), **beside)
    with pytest.raises(TypeError, match=f"Components V2.*{name} cannot ride"):
        await rendered.respond_to(rest, interaction, **beside)

    rest.create_message.assert_not_called()
    rest.create_interaction_response.assert_not_called()


async def test_the_refusal_names_everything_that_cannot_ride_along() -> None:
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)

    with pytest.raises(TypeError, match="; content, embeds cannot ride"):
        await rendered_text().send_to(rest, hikari.Snowflake(123), "hi", embeds=[hikari.Embed(title="x")])


async def test_one_component_that_is_no_row_makes_the_message_components_v2() -> None:
    rendered = ui.render([ui.Row(ui.LinkButton("https://example.com", label="Docs")), ui.TextDisplay("hi")])
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)

    with pytest.raises(TypeError, match="content cannot ride"):
        await rendered.send_to(rest, hikari.Snowflake(123), "Read the docs")

    rest.create_message.assert_not_called()


async def test_an_argument_left_undefined_is_not_refused_beside_components_v2() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    interaction = unittest.mock.Mock(spec=hikari.CommandInteraction)

    await rendered.send_to(rest, hikari.Snowflake(123), hikari.UNDEFINED, embeds=hikari.UNDEFINED)
    await rendered.respond_to(rest, interaction, hikari.UNDEFINED, embed=hikari.UNDEFINED)

    rest.create_message.assert_awaited_once()
    rest.create_interaction_response.assert_awaited_once()


async def test_content_alongside_a_rendered_view_is_rejected_with_the_v2_message() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)

    with pytest.raises(TypeError, match="TextDisplay"):
        await rendered.send_to(rest, hikari.Snowflake(123), content="hi")

    rest.create_message.assert_not_called()


async def test_embeds_alongside_a_rendered_view_are_rejected_too() -> None:
    rendered = rendered_text()
    rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    interaction = unittest.mock.Mock(spec=hikari.ComponentInteraction)

    with pytest.raises(TypeError, match="embeds"):
        await rendered.respond_to(rest, interaction, embeds=[])

    rest.create_interaction_response.assert_not_called()


def test_a_text_select_routes_and_serializes_its_options() -> None:
    (built,) = emitted(
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
    (built,) = emitted(ui.Row(node_type(Panel().press, placeholder="who")), meta_of(Panel))
    (select,) = payload_of(built)["components"]

    assert select["type"] == component_type
    assert select["placeholder"] == "who"
    assert parsed(select["custom_id"]).handler == Panel.press.token


def test_a_channel_select_defaults_to_no_filter() -> None:
    (built,) = emitted(ui.Row(ui.ChannelSelect(Panel().press, disabled=True)), meta_of(Panel))
    (select,) = payload_of(built)["components"]

    assert select["type"] == hikari.ComponentType.CHANNEL_SELECT_MENU
    assert select["channel_types"] == []
    assert select["disabled"] is True


def test_a_channel_select_filters_channel_types() -> None:
    (built,) = emitted(
        ui.Row(ui.ChannelSelect(Panel().press, channel_types=[hikari.ChannelType.GUILD_TEXT])),
        meta_of(Panel),
    )
    (select,) = payload_of(built)["components"]

    assert select["channel_types"] == [hikari.ChannelType.GUILD_TEXT]


def test_selects_count_in_tree_order_with_buttons() -> None:
    built = emitted(
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
        emitted(layout, meta_of(Panel))

    assert exc_info.value.path == "Row[0] > UserSelect[0]"


def test_a_select_rejects_something_that_is_not_a_handler() -> None:
    with pytest.raises(risa.NotAHandlerError) as exc_info:
        ui.UserSelect("nope")  # type: ignore[reportArgumentType]

    assert exc_info.value.type_name == "str"
