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

import html
import itertools
import re
import typing

import msgspec
import pytest

from risa.internal import codec
from tools.docs import manifest
from tools.docs import render_example

if typing.TYPE_CHECKING:
    import pathlib

_EXAMPLE = """
import risa
from risa import ui


# --8<-- [start:view]
@risa.register(name="tiny")
class Tiny(risa.View):
    count: int = 0
    note: str = ""

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"Count {self.count}"),
            ui.Row(ui.Button(self.click, label="Go")),
        )

    @risa.handler
    async def click(self, ctx: risa.ComponentContext) -> None:
        self.count += 1
        await ctx.rerender()


# --8<-- [end:view]
"""

_LIVE = manifest.LivePreview(id="l", example="tiny_example", view="Tiny", section="view", field="count", stop=3)

_FRAME = re.compile(
    r'<div data-live-part="message">(?P<message>.*)</div>'
    r'<span data-live-part="value">(?P<value>.*)</span>'
    r'<span data-live-part="id">(?P<id>.*)</span>',
)


def _example(directory: pathlib.Path, name: str = "tiny_example") -> pathlib.Path:
    path = directory / f"{name}.py"
    path.write_text(_EXAMPLE)
    return path


def _view(preview_id: str = "v", **fields: object) -> manifest.ViewPreview:
    return manifest.ViewPreview(id=preview_id, example="tiny_example", view="Tiny", fields=fields)


def _live(**changes: str | int) -> manifest.LivePreview:
    return msgspec.structs.replace(_LIVE, **changes)


def _frames(markup: str) -> list[dict[str, str]]:
    frames = re.findall(r"<template data-live-frame data-search-exclude>(.*?)</template>", markup)
    return [typing.cast("re.Match[str]", _FRAME.fullmatch(frame)).groupdict() for frame in frames]


def _text(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def test_a_view_preview_shows_the_rendered_buttons(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_view(count=3)])

    assert "Count 3" in result["v"]
    assert '<span class="dc-btn dc-primary">Go</span>' in result["v"]


def test_two_examples_may_register_the_same_view_name(tmp_path: pathlib.Path) -> None:
    first = _example(tmp_path, "one")
    second = _example(tmp_path, "two")

    assert "Count 1" in render_example.render_previews(first, [_view(count=1)])["v"]
    assert "Count 2" in render_example.render_previews(second, [_view(count=2)])["v"]


def test_a_build_is_deterministic(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path)

    assert render_example.render_previews(example, [_view(count=3)]) == render_example.render_previews(
        example,
        [_view(count=3)],
    )


def test_an_error_preview_carries_the_error_text(tmp_path: pathlib.Path) -> None:
    preview = manifest.ErrorPreview(id="e", example="tiny_example", view="Tiny", fields={"note": "x" * 600})

    result = render_example.render_previews(_example(tmp_path), [preview])

    assert 'class="risa-error"' in result["e"]
    assert "needs" in result["e"]
    assert "durable state" in result["e"]


def test_an_error_preview_of_a_view_that_fits_fails_the_step(tmp_path: pathlib.Path) -> None:
    preview = manifest.ErrorPreview(id="e", example="tiny_example", view="Tiny", fields={"count": 1})

    with pytest.raises(render_example.RenderError, match="did not raise"):
        render_example.render_previews(_example(tmp_path), [preview])


def test_a_click_path_shows_the_real_ids_and_only_the_fragment_changes(tmp_path: pathlib.Path) -> None:
    preview = manifest.ClickPath(
        id="c",
        example="tiny_example",
        view="Tiny",
        before={"count": 3},
        after={"count": 4},
        handler="click",
    )

    result = render_example.render_previews(_example(tmp_path), [preview])["c"]

    cookies = [html.unescape(match) for match in re.findall(r'<span class="risa-id-cookie">(.*?)</span>', result)]
    tokens = [html.unescape(match) for match in re.findall(r'<span class="risa-id-handler">(.*?)</span>', result)]
    fragments = [html.unescape(match) for match in re.findall(r'<span class="risa-id-fragment">(.*?)</span>', result)]
    assert set(cookies) == {codec.make_cookie("tiny", 1)}
    assert set(tokens) == {codec.make_handler_token("click", 1)}
    assert len(fragments) == 2
    assert fragments[0] != fragments[1]


def test_a_reply_preview_is_drawn_without_an_example(tmp_path: pathlib.Path) -> None:
    preview = manifest.ReplyPreview(id="r", text="pong", ephemeral=True)

    result = render_example.render_previews(_example(tmp_path), [preview])

    assert "Only you can see this" in result["r"]


def test_an_unknown_view_name_fails_the_step(tmp_path: pathlib.Path) -> None:
    preview = manifest.ViewPreview(id="v", example="tiny_example", view="Nope", fields={})

    with pytest.raises(render_example.RenderError, match="Nope"):
        render_example.render_previews(_example(tmp_path), [preview])


def test_a_failed_render_names_the_preview_and_the_error(tmp_path: pathlib.Path) -> None:
    example = tmp_path / "boom.py"
    example.write_text(_EXAMPLE.replace('f"Count {self.count}"', 'f"{1 // self.count}"'))
    previews = [_view("fine", count=1), _view("broken", count=0)]

    with pytest.raises(render_example.RenderError) as raised:
        render_example.render_previews(example, previews)

    assert raised.value.preview_id == "broken"
    assert "'broken'" in str(raised.value)
    assert "ZeroDivisionError" in str(raised.value)
    assert "Traceback" in raised.value.reason


def test_a_stray_print_in_an_example_does_not_corrupt_the_result(tmp_path: pathlib.Path) -> None:
    example = tmp_path / "chatty.py"
    example.write_text("print('hello')\n" + _EXAMPLE.replace("count: int = 0", "count: int = (print('again') or 0)"))

    assert "Count 1" in render_example.render_previews(example, [_view(count=1)])["v"]


def test_a_thinking_preview_is_drawn_without_an_example(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [manifest.ThinkingPreview(id="t")])

    assert "is thinking" in result["t"]


def test_every_preview_comes_wrapped_with_its_label(tmp_path: pathlib.Path) -> None:
    preview = manifest.ViewPreview(id="v", example="tiny_example", view="Tiny", fields={}, label="After a click")

    result = render_example.render_previews(_example(tmp_path), [preview])["v"]

    assert result.startswith(
        '<div class="risa-result" data-search-exclude><p class="risa-result__label">After a click</p><div class="dc">',
    )
    assert result.endswith("</div>")


def test_a_live_preview_has_one_frame_per_value(tmp_path: pathlib.Path) -> None:
    frames = _frames(render_example.render_previews(_example(tmp_path), [_live(stop=3)])["l"])

    assert [_text(frame["message"]) for frame in frames] == [f"Count {value}Go" for value in range(4)]
    assert [_text(frame["value"]) for frame in frames] == ["", "  # now 1", "  # now 2", "  # now 3"]


def test_the_message_frames_and_custom_id_stay_out_of_the_search_index(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live(stop=3)])["l"]

    assert result.count("<template") == result.count("<template data-live-frame data-search-exclude>") == 4
    assert '<p class="risa-live__id" data-search-exclude>' in result
    assert '<div data-search-exclude><div class="dc">' in result


def test_the_note_of_a_live_preview_stays_in_the_search_index(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live(stop=3, note="Click it.")])["l"]

    assert '<p class="risa-live__note">Click it.</p>' in result


def test_a_frame_holds_what_differs_and_not_the_author(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live(stop=3)])["l"]

    assert result.count('class="dc-name"') == 1
    assert all("dc-name" not in frame["message"] for frame in _frames(result))


def test_the_frames_show_the_real_custom_ids(tmp_path: pathlib.Path) -> None:
    click = manifest.ClickPath(
        id="c",
        example="tiny_example",
        view="Tiny",
        before={"count": 1},
        after={"count": 2},
        handler="click",
    )

    result = render_example.render_previews(_example(tmp_path), [_live(), click])

    parsed = [codec.parse_custom_id(_text(frame["id"])) for frame in _frames(result["l"])]
    fragments = [html.unescape(match) for match in re.findall(r'risa-id-fragment">(.*?)</span>', result["c"])]
    assert all(
        identifier is not None
        and identifier.cookie == codec.make_cookie("tiny", 1)
        and identifier.handler == codec.make_handler_token("click", 1)
        for identifier in parsed
    )
    assert [typing.cast("codec.CustomID", identifier).fragment for identifier in parsed[1:3]] == fragments


def test_a_frame_marks_the_characters_that_changed_since_the_previous_one(tmp_path: pathlib.Path) -> None:
    frames = _frames(render_example.render_previews(_example(tmp_path), [_live()])["l"])

    assert "risa-live__changed" not in frames[0]["id"]
    for before, after in itertools.pairwise(frames):
        old, new = _text(before["id"]), _text(after["id"])
        changed = "".join(
            html.unescape(run) for run in re.findall(r'<span class="risa-live__changed">(.*?)</span>', after["id"])
        )
        assert changed
        assert changed == "".join(char for index, char in enumerate(new) if old[index : index + 1] != char)


def test_a_custom_id_is_escaped_in_a_frame() -> None:
    assert render_example.mark_changes("a<b", "a<c") == 'a&lt;<span class="risa-live__changed">b</span>'


def test_a_longer_custom_id_marks_what_it_gained() -> None:
    assert render_example.mark_changes("abcd", "abc") == 'abc<span class="risa-live__changed">d</span>'


def test_two_builds_of_a_live_preview_are_identical(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path)

    assert render_example.render_previews(example, [_live()]) == render_example.render_previews(example, [_live()])


def test_only_a_live_preview_draws_a_real_button(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live(), _view(count=3)])

    assert "<button" not in result["v"]
    assert all(frame["message"].count("<button") == 1 for frame in _frames(result["l"]))


def test_a_live_preview_without_javascript_is_a_static_preview_of_the_first_frame(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    static = result.split("<template", 1)[0]
    assert "<button" not in static
    assert '<span class="dc-btn dc-primary">Go</span>' in static
    assert "Count 0" in static


def test_only_the_last_frame_disables_the_button(tmp_path: pathlib.Path) -> None:
    frames = _frames(render_example.render_previews(_example(tmp_path), [_live(stop=2)])["l"])

    assert [" disabled>" in frame["message"] for frame in frames] == [False, False, True]
    assert "dc-disabled" in frames[-1]["message"]


def test_a_live_preview_shows_its_section_highlighted_as_a_fence_is(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    assert '<div class="language-python highlight"><pre><span></span><code>' in result
    assert '<span class="k">class</span>' in result
    assert "--8&lt;--" not in result
    assert "import risa" not in _text(result)
    assert "@risa.register" in _text(result)


def test_a_live_preview_is_one_block_without_a_result_wrapper(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    assert result.startswith('<div class="risa-live" data-live-preview>')
    assert result.endswith("</div>")
    assert "risa-result" not in result
    assert "\n\n" not in result


def test_a_live_preview_marks_the_lines_of_the_field(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    lines = result.split("</template>")[0].split("\n")
    field = next(line for line in lines if _text(line).strip().startswith("count:"))
    change = next(line for line in lines if _text(line).strip().startswith("self.count +="))
    assert field.endswith('<span class="risa-live__value" data-live-slot="value"></span>')
    assert "data-live-tint" in change
    assert result.count("data-live-tint") == 1
    assert result.count('data-live-slot="value"') == 1


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"field": "note"}, r"self\.note \+="),
        ({"section": "gone"}, "gone"),
    ],
)
def test_a_live_preview_that_cannot_find_its_lines_fails_the_step(
    tmp_path: pathlib.Path,
    changes: dict[str, str],
    missing: str,
) -> None:
    with pytest.raises(render_example.RenderError, match=missing):
        render_example.render_previews(_example(tmp_path), [_live(**changes)])


def test_a_live_preview_whose_field_line_is_gone_fails_the_step(tmp_path: pathlib.Path) -> None:
    example = tmp_path / "tiny_example.py"
    example.write_text(_EXAMPLE.replace("    count: int = 0\n", "    count = 0\n"))

    with pytest.raises(render_example.RenderError, match="count:"):
        render_example.render_previews(example, [_live()])


def test_a_live_preview_needs_exactly_one_button(tmp_path: pathlib.Path) -> None:
    example = tmp_path / "tiny_example.py"
    example.write_text(
        _EXAMPLE.replace(
            'ui.Row(ui.Button(self.click, label="Go"))',
            'ui.Row(ui.Button(self.click, label="Go"), ui.Button(self.click, label="Again"))',
        ),
    )

    with pytest.raises(render_example.RenderError, match="exactly one"):
        render_example.render_previews(example, [_live()])


def test_a_live_preview_carries_its_note_escaped(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live(note="Click <it>.")])["l"]

    assert '<p class="risa-live__note">Click &lt;it&gt;.</p>' in result


def test_a_live_preview_without_a_note_draws_no_paragraph(tmp_path: pathlib.Path) -> None:
    assert "risa-live__note" not in render_example.render_previews(_example(tmp_path), [_live()])["l"]
