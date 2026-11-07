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

import pytest

from tools.docs import discord_html

if typing.TYPE_CHECKING:
    import collections.abc


def _message(*components: collections.abc.Mapping[str, object], interactive: bool = False) -> str:
    return discord_html.render_message(components, interactive=interactive)


def _button(**fields: object) -> dict[str, object]:
    return {"type": 2, "style": 1, "disabled": False, "label": "Go", "custom_id": "x", **fields}


def _text(content: str) -> dict[str, object]:
    return {"type": 10, "content": content}


def test_a_message_has_the_author_the_app_tag_and_a_fixed_time() -> None:
    html = _message(_text("hi"))

    assert '<span class="dc-name">Risa Bot</span><span class="dc-app">APP</span>' in html
    assert "Today at 12:00" in html
    assert 'role="img" aria-label="Risa Bot"' in html


def test_the_content_of_a_message_comes_above_its_components() -> None:
    html = discord_html.render_message([_button()], content="Hello **there**")

    assert '<div class="dc-text"><p>Hello <strong>there</strong></p></div>' in html
    assert html.index("Hello") < html.index("dc-btn")


def test_a_message_without_content_draws_no_text_above_its_components() -> None:
    assert "dc-text" not in discord_html.render_message([_button()])


def test_a_container_draws_its_accent_colour() -> None:
    html = _message({"type": 17, "accent_color": 0xF680A1, "components": [_text("hi")]})

    assert 'class="dc-box dc-accent" style="--dc-accent:#f680a1"' in html


def test_a_container_without_an_accent_colour_has_no_bar() -> None:
    html = _message({"type": 17, "components": [_text("hi")]})

    assert "dc-accent" not in html


def test_a_container_ignores_its_spoiler_flag() -> None:
    html = _message({"type": 17, "spoiler": True, "components": [_text("hi")]})

    assert "spoiler" not in html
    assert "hi" in html


def test_a_text_display_renders_its_markdown() -> None:
    assert "<strong>3</strong>" in _message(_text("Clicked **3** times"))


def test_a_separator_with_a_divider_draws_a_line() -> None:
    assert '<div class="dc-sep"></div>' in _message({"type": 14, "divider": True, "spacing": 1})


def test_a_separator_without_a_divider_is_only_space() -> None:
    assert '<div class="dc-sep dc-sep-blank"></div>' in _message({"type": 14, "divider": False, "spacing": 1})


def test_a_large_separator_is_taller() -> None:
    assert "dc-sep-large" in _message({"type": 14, "divider": True, "spacing": 2})


def test_a_section_puts_its_text_beside_a_button_accessory() -> None:
    html = _message({"type": 9, "components": [_text("Ramen")], "accessory": _button(label="Vote")})

    assert '<div class="dc-sec"><div class="dc-sec-text"><div class="dc-text"><p>Ramen</p></div></div>' in html
    assert '<span class="dc-btn dc-primary">Vote</span>' in html


def test_a_section_draws_a_thumbnail_accessory_as_a_stand_in() -> None:
    html = _message(
        {
            "type": 9,
            "components": [_text("Risa")],
            "accessory": {"type": 11, "media": {"url": "u"}, "description": "Her"},
        },
    )

    assert 'class="dc-thumb" role="img" aria-label="Her"' in html


def test_a_media_gallery_keeps_one_tile_per_item() -> None:
    items = [{"media": {"url": "u"}}] * 3
    html = _message({"type": 12, "items": items})

    assert 'class="dc-gallery dc-gallery-3"' in html
    assert html.count('class="dc-tile"') == 3


def test_a_file_shows_its_name() -> None:
    html = _message({"type": 13, "file": {"url": "attachment://results.csv"}})

    assert '<div class="dc-file">' in html
    assert "<a>results.csv</a>" in html


def test_an_action_row_holds_its_buttons() -> None:
    html = _message({"type": 1, "components": [_button(label="A"), _button(label="B")]})

    assert html.count('class="dc-row"') == 1
    assert html.count("dc-btn") == 2


@pytest.mark.parametrize(
    ("style", "name"),
    [(1, "primary"), (2, "secondary"), (3, "success"), (4, "danger"), (5, "link"), (6, "premium")],
)
def test_every_button_style_has_its_colour_class(style: int, name: str) -> None:
    assert f'class="dc-btn dc-{name}"' in _message(_button(style=style))


def test_a_primary_button_is_blurple_with_its_label() -> None:
    assert '<span class="dc-btn dc-primary">+1</span>' in _message(_button(label="+1"))


def test_a_link_button_carries_the_external_arrow() -> None:
    html = _message(_button(style=5, label="Docs", custom_id=None, url="https://x"))

    assert 'class="dc-btn dc-link">Docs <svg' in html


def test_a_premium_button_has_a_stand_in_label() -> None:
    assert "Subscribe" in _message({"type": 2, "style": 6, "sku_id": 1})


def test_a_disabled_button_is_dimmed() -> None:
    assert 'class="dc-btn dc-primary dc-disabled"' in _message(_button(disabled=True))


def test_a_button_emoji_precedes_its_label() -> None:
    assert ">🎉 Party<" in _message(_button(label="Party", emoji={"name": "🎉"}))


def test_buttons_are_inert_spans_by_default() -> None:
    html = _message(_button())

    assert "<button" not in html


def test_an_interactive_message_draws_real_buttons() -> None:
    html = _message(_button(), interactive=True)

    assert '<button type="button" class="dc-btn dc-primary">Go</button>' in html


def test_an_interactive_disabled_button_is_disabled() -> None:
    assert '<button type="button" class="dc-btn dc-primary dc-disabled" disabled>' in _message(
        _button(disabled=True),
        interactive=True,
    )


@pytest.mark.parametrize("kind", [3, 5, 6, 7, 8])
def test_every_select_draws_its_placeholder_closed(kind: int) -> None:
    html = _message({"type": kind, "placeholder": "Pick one"})

    assert '<div class="dc-select"><span>Pick one</span>' in html


def test_a_select_without_a_placeholder_says_make_a_selection() -> None:
    assert "<span>Make a selection</span>" in _message({"type": 5})


def test_an_unknown_component_is_an_error() -> None:
    with pytest.raises(ValueError, match="99"):
        _message({"type": 99})


def test_a_reply_is_plain_text() -> None:
    html = discord_html.render_reply("pong", ephemeral=False)

    assert "<p>pong</p>" in html
    assert "Only you can see this" not in html


def test_an_ephemeral_reply_says_only_you_can_see_it() -> None:
    html = discord_html.render_reply("pong", ephemeral=True)

    assert "Only you can see this" in html
    assert "Dismiss message" in html


def test_markdown_headings_bold_italic_code_and_lists() -> None:
    html = discord_html.render_markdown(
        "# One\n## Two\n### Three\n**b** *i* _j_ __u__ ~~s~~ `c`\n- a\n- b\n> quoted\n[t](https://x.y)",
    )

    assert '<p class="dc-h1">One</p><p class="dc-h2">Two</p><p class="dc-h3">Three</p>' in html
    assert "<strong>b</strong> <em>i</em> <em>j</em> <u>u</u> <s>s</s> <code>c</code>" in html
    assert "<ul><li>a</li><li>b</li></ul>" in html
    assert '<blockquote class="dc-quote">quoted</blockquote>' in html
    assert '<a class="dc-link" href="https://x.y">t</a>' in html


def test_a_fenced_block_keeps_its_text_literal() -> None:
    assert '<pre class="dc-code"><code>**x**\ny</code></pre>' in discord_html.render_markdown("```\n**x**\ny\n```")


def test_a_quote_joins_its_lines() -> None:
    assert '<blockquote class="dc-quote">a<br>b</blockquote>' in discord_html.render_markdown("> a\n> b")


def test_subtext_line_is_small() -> None:
    assert discord_html.render_markdown("-# Closes at noon") == '<p class="dc-sub">Closes at noon</p>'


def test_an_escaped_star_stays_a_star() -> None:
    html = discord_html.render_markdown(r"\*not italic\*")

    assert html == "<p>*not italic*</p>"


def test_html_in_text_is_escaped() -> None:
    html = discord_html.render_markdown("<script>alert(1)</script> & **<b>**")

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>&lt;b&gt;</strong>" in html


def test_code_is_not_marked_up_again() -> None:
    assert "<code>**x**</code>" in discord_html.render_markdown("`**x**`")


def test_a_link_must_be_http() -> None:
    html = discord_html.render_markdown("[x](javascript:alert(1))")

    assert "<a " not in html


def test_a_quote_in_a_link_url_stays_in_the_href() -> None:
    html = discord_html.render_markdown('[x](https://a.com/"onmouseover=alert(1))')

    assert 'href="https://a.com/&quot;onmouseover=alert(1"' in html
    assert '"onmouseover' not in html


def test_the_private_use_characters_of_the_slots_in_a_text_are_harmless() -> None:
    html = discord_html.render_markdown("\ue0009\ue001 and `c`")

    assert "<code>c</code>" in html
    assert "\ue000" not in html


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("***x***", "<strong><em>x</em></strong>"),
        ("___x___", "<u><em>x</em></u>"),
        ("**a *b** c*", "<strong>a *b</strong> c*"),
        ("*a **b** c*", "<em>a <strong>b</strong> c</em>"),
    ],
)
def test_markers_nest_validly(text: str, expected: str) -> None:
    assert discord_html.render_markdown(text) == f"<p>{expected}</p>"


@pytest.mark.parametrize("text", ["a ** b", "****", "a __ b", "____", "a ~~ b", "~~~~", "**", "a * * b", "******"])
def test_a_marker_with_nothing_inside_stays_literal(text: str) -> None:
    assert discord_html.render_markdown(text) == f"<p>{text}</p>"


def test_a_link_label_keeps_its_inline_markup() -> None:
    html = discord_html.render_markdown("[a **b**](https://x.y)")

    assert '<a class="dc-link" href="https://x.y">a <strong>b</strong></a>' in html


def test_a_link_label_keeps_its_code_span() -> None:
    html = discord_html.render_markdown("[`c`](https://x.y)")

    assert '<a class="dc-link" href="https://x.y"><code>c</code></a>' in html


def test_an_escaped_character_in_a_link_url_is_restored() -> None:
    html = discord_html.render_markdown(r"[read](https://example.com/a\_\(b\))")

    assert '<a class="dc-link" href="https://example.com/a_(b)">read</a>' in html


def test_a_code_span_in_a_link_url_stays_out_of_the_href() -> None:
    html = discord_html.render_markdown('[x](https://a.com/`"onmouseover=alert(1)`)')

    assert "href=" not in html
    assert '<code>"onmouseover=alert(1)</code>' in html


def test_a_custom_emoji_is_drawn_by_name() -> None:
    assert ">:party: Go<" in _message(_button(label="Go", emoji={"id": "1", "name": "party"}))


def test_a_custom_emoji_without_a_name_is_drawn_as_a_placeholder() -> None:
    assert ">:emoji: Go<" in _message(_button(label="Go", emoji={"id": "1"}))


def test_a_section_thumbnail_draws_the_image_icon() -> None:
    html = _message({"type": 9, "components": [_text("a")], "accessory": {"type": 11, "media": {"url": "u"}}})

    assert '<span class="dc-thumb" role="img" aria-label="Image"><svg' in html


def test_a_thinking_message_is_italic_text_from_the_author() -> None:
    html = discord_html.render_thinking(ephemeral=False)

    assert '<p class="dc-thinking">Risa Bot is thinking…</p>' in html
    assert "Only you can see this" not in html


def test_an_ephemeral_thinking_message_has_the_note() -> None:
    assert "Only you can see this" in discord_html.render_thinking(ephemeral=True)


def test_a_result_wraps_its_markup_with_a_label() -> None:
    assert discord_html.wrap_result("<i>x</i>", "In Discord") == (
        '<div class="risa-result" data-search-exclude><p class="risa-result__label">In Discord</p><i>x</i></div>'
    )


def test_an_empty_label_draws_no_paragraph() -> None:
    assert discord_html.wrap_result("<i>x</i>", "") == '<div class="risa-result" data-search-exclude><i>x</i></div>'


def test_a_body_is_a_message_without_its_author_line() -> None:
    components = [{"type": 10, "content": "hello"}]

    body = discord_html.render_body(components)

    assert "dc-name" not in body
    assert discord_html.frame_message(body) == discord_html.render_message(components)
