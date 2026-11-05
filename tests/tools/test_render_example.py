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
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess  # ruff:ignore[suspicious-subprocess-import]
import typing
import unittest.mock

import hikari
import msgspec
import pytest

import risa
from risa.internal import codec
from risa.internal import registry
from risa.state import anchor
from risa.state import message
from tools.docs import manifest
from tools.docs import render_example

if typing.TYPE_CHECKING:
    import contextlib

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

_RICH = """
from __future__ import annotations

import enum

import risa
import risa.di
from risa import ui


class Mood(enum.Enum):
    GOOD = 1
    BAD = 2


class Clock:
    def now(self) -> str:
        return "noon"


CALLS = []


@risa.register(name="rich")
class Rich(risa.View):
    home: int = 0
    away: int = 0
    mood: Mood = Mood.GOOD
    now: risa.Prop[str] = ""

    async def load(self, clock: Clock) -> None:
        self.now = clock.now()

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"{self.now} calls {len(CALLS)}"),
            ui.Row(
                ui.Button(self.score, label="Score"),
                ui.Button(risa.bind(self.set_mood, Mood.BAD), label="Bad"),
            ),
            ui.Row(ui.TextSelect(self.pick, ui.SelectOption("A", "a"), placeholder="Pick one")),
        )

    @risa.handler
    async def score(self, ctx: risa.ComponentContext) -> None:
        self.home += 1
        await ctx.rerender()

    @risa.handler
    async def set_mood(self, ctx: risa.ComponentContext, mood: Mood) -> None:
        self.mood = mood
        await ctx.rerender()

    @risa.handler
    async def pick(self, ctx: risa.ComponentContext) -> None:
        await ctx.rerender()


def add_dependencies(client):
    CALLS.append(client)
    client.di.registry_for(risa.di.Contexts.DEFAULT).register_value(Clock, Clock())
"""

_ENUM = """
from __future__ import annotations

import enum

import risa
from risa import ui


class Mood(enum.Enum):
    GOOD = 1
    BAD = 2


@risa.register(name="moody")
class Moody(risa.View):
    mood: Mood = Mood.GOOD

    def render(self) -> ui.Layout:
        return ui.Container(ui.TextDisplay(self.mood.name))
"""

_ROWS = """
import risa
from risa import ui


@risa.register(name="rows")
class Rows(risa.View):
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.go, label="Go"))

    @risa.handler
    async def go(self, ctx: risa.ComponentContext) -> None:
        await ctx.rerender()
"""

_UNPARSABLE = _EXAMPLE.replace(
    "import risa\n",
    "from unittest import mock\n\nimport risa\nfrom risa.internal import codec\n\n"
    "mock.patch.object(codec, 'parse_custom_id', return_value=None).start()\n",
    1,
)

_STORED = """
from __future__ import annotations

import risa
from risa import ui


@risa.register(name="stored", state=risa.InStore(ttl=60))
class Stored(risa.View):
    n: int = 0
    label: str = ""

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay("hi"),
            ui.Row(*[ui.Button(self.go, label=self.label or "Go") for _ in range(self.n + 1)]),
        )

    @risa.handler
    async def go(self, ctx: risa.ComponentContext) -> None:
        await ctx.rerender()
"""

_MIXED = """
from __future__ import annotations

import risa
from risa import ui


@risa.register(name="mixed")
class Mixed(risa.View):
    def render(self) -> ui.Layout:
        return [ui.Row(ui.Button(self.go, label="Go")), ui.Container(ui.TextDisplay("hi"))]

    @risa.handler
    async def go(self, ctx: risa.ComponentContext) -> None:
        await ctx.rerender()
"""

_LIVE = manifest.LivePreview(id="l", example="tiny_example", view="Tiny", section="view", field="count")

_FRAME = re.compile(
    r'<template data-live-frame data-search-exclude><div data-live-part="message">(?P<message>.*)</div>'
    r'<span data-live-part="value">(?P<value>.*)</span></template>',
)
_COUNT_SLOT = "<span data-live-count></span>"
_SCRIPT = pathlib.Path(__file__).parents[2] / "docs" / "javascripts" / "live-preview.js"
_RUN_SCRIPT = """
const [script, data, counts] = process.argv.slice(1);
globalThis.document$ = { subscribe() {} };
require("vm").runInThisContext(require("fs").readFileSync(script, "utf8"));
console.log(JSON.stringify(JSON.parse(counts).map((count) => risaLiveCustomId(JSON.parse(data), count))));
"""


def _example(directory: pathlib.Path, name: str = "tiny_example", source: str = _EXAMPLE) -> pathlib.Path:
    path = directory / f"{name}.py"
    path.write_text(source)
    return path


def _view(preview_id: str = "v", **fields: object) -> manifest.ViewPreview:
    return manifest.ViewPreview(id=preview_id, example="tiny_example", view="Tiny", fields=fields)


def _live(**changes: str | int) -> manifest.LivePreview:
    return msgspec.structs.replace(_LIVE, **changes)


def _frame(markup: str) -> dict[str, str]:
    return typing.cast("re.Match[str]", _FRAME.search(markup)).groupdict()


def _at_sequence(number: int) -> contextlib.AbstractContextManager[object]:
    def first_anchor(_: message.MessageBackend, *, meta: registry.ViewMeta, view: risa.View) -> bytes:
        return anchor.MessageAnchor(meta.schema.fingerprint, number, meta.schema.pack_view(view)).pack()

    return unittest.mock.patch.object(message.MessageBackend, "first_anchor", first_anchor)


def _script_ids(markup: str, counts: list[int]) -> list[str]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("testing the script of the live preview needs node")
        pytest.skip("node is not installed")
    opening = typing.cast("re.Match[str]", re.search(r'<div class="risa-live" data-live-preview ([^>]*)>', markup))
    data = {
        f"live{name.capitalize()}": html.unescape(value)
        for name, value in re.findall(r'data-live-(\w+)="([^"]*)"', opening[1])
    }
    finished = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]
        [node, "-e", _RUN_SCRIPT, str(_SCRIPT), json.dumps(data), json.dumps(counts)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(finished.stdout)


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


def _spans(markup: str, name: str) -> list[str]:
    pattern = rf'<span class="risa-id-{name}">(.*?)</span>(?=<span class="risa-id-|</code>|<span class="risa-live)'
    return [_text(match) for match in re.findall(pattern, markup)]


def _click(**changes: object) -> manifest.ClickPath:
    return msgspec.structs.replace(
        manifest.ClickPath(
            id="c",
            example="tiny_example",
            view="Tiny",
            before={"count": 3},
            after={"count": 4},
            handler="click",
        ),
        **changes,
    )


def _ids(**changes: object) -> manifest.IdsDiagram:
    return msgspec.structs.replace(
        manifest.IdsDiagram(id="i", example="rich_example", view="Rich", before={"home": 3, "away": 1}),
        **changes,
    )


def _rows(markup: str) -> list[tuple[str, str]]:
    found = re.findall(r'<span class="risa-ids__name">(.*?)</span><code>(.*?)</code>', markup)
    return [(html.unescape(name), _text(code)) for name, code in found]


def test_a_click_path_shows_the_real_ids_and_only_the_fragment_changes(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_click()])["c"]

    routes = _spans(result, "route")
    states = _spans(result, "state")
    parsed = [codec.parse_custom_id(route + state) for route, state in zip(routes, states, strict=True)]
    assert len(parsed) == 2
    assert all(
        identifier is not None
        and identifier.cookie == codec.make_cookie("tiny", 1)
        and identifier.handler == codec.make_handler_token("click", 1)
        for identifier in parsed
    )
    assert states[0] != states[1]
    assert routes[0] == routes[1]


def test_a_click_path_marks_the_changed_characters_on_the_last_step_only(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_click()])["c"]

    steps = result.split('<li class="risa-flow__step">')[1:]
    assert len(steps) == 6
    assert [("risa-live__changed" in step) for step in steps] == [False] * 5 + [True]


def test_a_click_path_uses_the_words_of_the_walkthrough(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_click(before={"count": 3, "note": "x"})])["c"]

    steps = [
        (_text(title), _text(detail))
        for title, detail in re.findall(r"<strong>(.*?)</strong><span>(.*?)</span>", result)
    ]
    assert steps == [
        ("Click", "The button sends its id to the bot."),
        ("Read the id", "The id names the view and the handler."),
        ("Find the view", "risa finds Tiny, which add_view gave it."),
        ("Unpack the state", "The message holds count = 3, note = 'x'."),
        ("Run the handler", "click changes the fields."),
        ("Rerender", "The button gets a new id, with the new state."),
    ]
    assert "risa-id-plain" not in result


def test_a_click_path_without_fields_says_the_message_holds_nothing(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_click(before={"count": 0}, after={"count": 1})])["c"]
    nothing = render_example.render_previews(_example(tmp_path), [_click(before={}, after={"count": 1})])["c"]

    assert "The message holds count = 0." in result
    assert "The message holds nothing." in nothing


def test_a_click_path_whose_fields_do_not_change_fails_the_step(tmp_path: pathlib.Path) -> None:
    with pytest.raises(render_example.RenderError, match="must change a field"):
        render_example.render_previews(_example(tmp_path), [_click(before={"count": 3}, after={"count": 3})])


def test_a_click_path_of_a_view_whose_ids_do_not_change_fails_the_step(tmp_path: pathlib.Path) -> None:
    stored = _EXAMPLE.replace('name="tiny")', 'name="tiny", state=risa.InStore(ttl=600))')

    with pytest.raises(render_example.RenderError, match="must change the fragment"):
        render_example.render_previews(_example(tmp_path, source=stored), [_click()])


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


def test_a_live_preview_has_one_frame_that_leaves_the_number_open(tmp_path: pathlib.Path) -> None:
    frame = _frame(render_example.render_previews(_example(tmp_path), [_live()])["l"])

    assert frame["message"].count(_COUNT_SLOT) == 1
    assert _text(frame["message"]) == "Count Go"
    assert frame["value"].count(_COUNT_SLOT) == 1
    assert _text(frame["value"]) == "  # now "


def test_the_message_frames_and_custom_id_stay_out_of_the_search_index(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    assert result.count("<template") == result.count("<template data-live-frame data-search-exclude>") == 1
    assert '<p class="risa-live__id" data-search-exclude>' in result
    assert '<div data-search-exclude><div class="dc">' in result


def test_the_note_of_a_live_preview_stays_in_the_search_index(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live(note="Click it.")])["l"]

    assert '<p class="risa-live__note">Click it.</p>' in result


def test_a_frame_holds_what_differs_and_not_the_author(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    assert result.count('class="dc-name"') == 1
    assert "dc-name" not in _frame(result)["message"]


def test_a_live_preview_shows_the_real_custom_id_before_the_first_click(tmp_path: pathlib.Path) -> None:
    click = _click(before={"count": 0}, after={"count": 1})

    result = render_example.render_previews(_example(tmp_path), [_live(), click])

    shown = re.search(r'<span data-live-slot="id">(.*?)</span>', result["l"])
    identifier = codec.parse_custom_id(_text(typing.cast("re.Match[str]", shown)[1]))
    assert identifier is not None
    assert identifier.cookie == codec.make_cookie("tiny", 1)
    assert identifier.handler == codec.make_handler_token("click", 1)
    assert identifier.fragment == _spans(result["c"], "state")[0]


def test_a_custom_id_is_escaped_when_its_changes_are_marked() -> None:
    assert render_example.mark_changes("a<b", "a<c") == 'a&lt;<span class="risa-live__changed">b</span>'


def test_a_longer_custom_id_marks_what_it_gained() -> None:
    assert render_example.mark_changes("abcd", "abc") == 'abc<span class="risa-live__changed">d</span>'


def test_two_builds_of_a_live_preview_are_identical(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path)

    assert render_example.render_previews(example, [_live()]) == render_example.render_previews(example, [_live()])


def test_only_a_live_preview_draws_a_real_button(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live(), _view(count=3)])

    assert "<button" not in result["v"]
    assert _frame(result["l"])["message"].count("<button") == 1


def test_a_live_preview_without_javascript_is_a_static_preview_of_the_first_frame(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    static = result.split("<template", 1)[0]
    assert "<button" not in static
    assert '<span class="dc-btn dc-primary">Go</span>' in static
    assert "Count 0" in static


def test_the_button_of_a_live_preview_never_runs_out(tmp_path: pathlib.Path) -> None:
    message = _frame(render_example.render_previews(_example(tmp_path), [_live()])["l"])["message"]

    assert " disabled" not in message
    assert "dc-disabled" not in message


def test_a_live_preview_that_does_not_draw_its_number_once_fails_the_step(tmp_path: pathlib.Path) -> None:
    example = tmp_path / "tiny_example.py"
    example.write_text(_EXAMPLE.replace('f"Count {self.count}"', '"Count"'))

    with pytest.raises(render_example.RenderError, match="exactly once"):
        render_example.render_previews(example, [_live()])


def test_a_live_preview_of_a_view_in_a_store_fails_the_step(tmp_path: pathlib.Path) -> None:
    example = tmp_path / "tiny_example.py"
    example.write_text(
        _EXAMPLE.replace('@risa.register(name="tiny")', '@risa.register(name="tiny", state=risa.InStore(ttl=60))')
    )

    with pytest.raises(render_example.RenderError, match="in the message"):
        render_example.render_previews(example, [_live()])


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(_EXAMPLE.replace('name="tiny"', 'name="live-two-fields"'), id="a field after the count"),
        pytest.param(
            _EXAMPLE.replace('name="tiny"', 'name="live-one-field"').replace('    note: str = ""\n', ""),
            id="the count alone",
        ),
    ],
)
async def test_the_script_writes_the_custom_id_that_client_build_writes(tmp_path: pathlib.Path, source: str) -> None:
    counts = [0, 1, 2, 99, 100, 127, 128, 255, 256, 65535, 65536, 2**24 - 1, 2**24, 2**32 - 1, 2**32, 2**53 - 1]
    example = _example(tmp_path, source=source)
    markup = render_example.render_previews(example, [_live()])["l"]
    spec = importlib.util.spec_from_file_location("live_example", example)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = unittest.mock.Mock(spec=hikari.GatewayBot)
    app.rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    app.event_manager = unittest.mock.Mock(spec=hikari.api.EventManager)
    client = risa.client_from_app(app, store=risa.MemoryStore())

    real: list[str] = []
    for count in counts:
        with _at_sequence(count % 2**24):
            payload = (await client.build(module.Tiny(count=count))).components[0].build()[0]
        real.append(payload["components"][1]["components"][0]["custom_id"])

    assert _script_ids(markup, counts) == real


def test_a_live_preview_shows_its_section_highlighted_as_a_fence_is(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    assert '<div class="language-python highlight"><pre><span></span><code>' in result
    assert '<span class="k">class</span>' in result
    assert "--8&lt;--" not in result
    assert "import risa" not in _text(result)
    assert "@risa.register" in _text(result)


def test_a_live_preview_is_one_block_without_a_result_wrapper(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path), [_live()])["l"]

    assert result.startswith('<div class="risa-live" data-live-preview data-live-')
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


def test_a_field_may_be_annotated_with_a_class_of_its_own_example(tmp_path: pathlib.Path) -> None:
    preview = manifest.ViewPreview(id="v", example="moody", view="Moody", fields={})

    result = render_example.render_previews(_example(tmp_path, "moody", _ENUM), [preview])

    assert "GOOD" in result["v"]


def test_add_dependencies_runs_once_before_the_first_build(tmp_path: pathlib.Path) -> None:
    previews = [
        manifest.ViewPreview(id="a", example="rich_example", view="Rich", fields={}),
        manifest.ViewPreview(id="b", example="rich_example", view="Rich", fields={"home": 1}),
    ]

    result = render_example.render_previews(_example(tmp_path, "rich_example", _RICH), previews)

    assert "noon calls 1" in _text(result["a"])
    assert "noon calls 1" in _text(result["b"])


def test_a_view_that_needs_an_injected_class_fails_without_add_dependencies(tmp_path: pathlib.Path) -> None:
    source = _RICH.replace("def add_dependencies", "def not_the_hook")
    preview = manifest.ViewPreview(id="a", example="rich_example", view="Rich", fields={})

    with pytest.raises(render_example.RenderError):
        render_example.render_previews(_example(tmp_path, "rich_example", source), [preview])


def test_an_ids_diagram_shows_each_component_with_its_label_and_real_id(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path, "rich_example", _RICH), [_ids()])["i"]

    rows = _rows(result)
    assert [label for label, _ in rows] == ["Score", "Bad", "Pick one"]
    for _, raw in rows:
        parsed = codec.parse_custom_id(raw)
        assert parsed is not None
        assert parsed.cookie == codec.make_cookie("rich", 1)
    assert len({raw for _, raw in rows}) == 3


def test_an_ids_diagram_splits_each_id_into_route_state_and_tail(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path, "rich_example", _RICH), [_ids()])["i"]

    codes = re.findall(r'<span class="risa-ids__name">.*?</span><code>(.*?)</code>', result)
    for code in codes:
        raw = _text(code)
        parsed = codec.parse_custom_id(raw)
        assert parsed is not None
        assert [_text(m) for m in re.findall(r'<span class="risa-id-route">(.*?)</span>', code)] == [
            raw[: len(raw) - len(parsed.tail) - len(parsed.fragment)],
        ]
        assert [_text(m) for m in re.findall(r'<span class="risa-id-state">(.*?)</span>', code)] == [
            parsed.fragment,
        ]
        assert "".join(_text(m) for m in re.findall(r'<span class="risa-id-arg">(.*?)</span>', code)) == parsed.tail
    assert any(codec.parse_custom_id(_text(code)).tail for code in codes)  # type: ignore[union-attr]


def test_an_ids_diagram_titles_a_group_by_its_field_values(tmp_path: pathlib.Path) -> None:
    both = render_example.render_previews(
        _example(tmp_path, "rich_example", _RICH),
        [_ids(after={"home": 4, "away": 1}), _ids(id="d", before={})],
    )

    titles = re.findall(r'<p class="risa-ids__title">(.*?)</p>', both["i"])
    assert titles == ["home = 3, away = 1", "home = 4, away = 1"]
    assert re.findall(r'<p class="risa-ids__title">(.*?)</p>', both["d"]) == ["The defaults"]


def test_an_ids_diagram_marks_what_the_second_group_changed(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "rich_example", _RICH)
    alone = render_example.render_previews(example, [_ids()])["i"]
    both = render_example.render_previews(example, [_ids(after={"home": 4, "away": 1})])["i"]

    assert "risa-live__changed" not in alone
    first, second = both.split('<p class="risa-ids__title">')[1:]
    assert "risa-live__changed" not in first
    assert "risa-live__changed" in second
    assert len(_rows(first)) == len(_rows(second)) == 3


def test_the_legend_names_the_argument_only_when_an_id_has_one(tmp_path: pathlib.Path) -> None:
    rich = render_example.render_previews(_example(tmp_path, "rich_example", _RICH), [_ids()])["i"]
    tiny = render_example.render_previews(
        _example(tmp_path),
        [manifest.IdsDiagram(id="t", example="tiny_example", view="Tiny", before={})],
    )["t"]

    assert "bound argument" in _text(rich.split('class="risa-ids__legend"')[1])
    legend = _text(tiny.split('class="risa-ids__legend"')[1])
    assert "bound argument" not in legend
    assert "route" in legend.lower() or "routes" in legend.lower()
    assert "state" in legend.lower()


def test_an_ids_diagram_is_wrapped_with_its_label(tmp_path: pathlib.Path) -> None:
    result = render_example.render_previews(_example(tmp_path, "rich_example", _RICH), [_ids()])["i"]

    assert result.startswith(
        '<div class="risa-result" data-search-exclude><p class="risa-result__label">What the buttons hold</p>',
    )


def test_a_custom_id_that_does_not_parse_fails_the_step(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "unparsable", _UNPARSABLE)
    preview = manifest.IdsDiagram(id="i", example="unparsable", view="Tiny", before={})

    with pytest.raises(render_example.RenderError, match="custom_id"):
        render_example.render_previews(example, [preview])


def test_the_content_of_a_view_preview_is_drawn_above_its_rows(tmp_path: pathlib.Path) -> None:
    preview = manifest.ViewPreview(id="v", example="rows_example", view="Rows", fields={}, content="Pick **one**")

    result = render_example.render_previews(_example(tmp_path, "rows_example", _ROWS), [preview])["v"]

    assert '<div class="dc-text"><p>Pick <strong>one</strong></p></div>' in result
    assert result.index("Pick") < result.index("dc-btn")


def test_content_on_a_view_that_is_not_all_rows_fails_the_step(tmp_path: pathlib.Path) -> None:
    preview = manifest.ViewPreview(id="v", example="tiny_example", view="Tiny", fields={}, content="Hello")

    with pytest.raises(render_example.RenderError, match="content"):
        render_example.render_previews(_example(tmp_path), [preview])


def test_the_legend_of_a_stored_view_calls_the_pink_part_a_key(tmp_path: pathlib.Path) -> None:
    stored = render_example.render_previews(
        _example(tmp_path, "stored_example", _STORED),
        [manifest.IdsDiagram(id="s", example="stored_example", view="Stored", before={})],
    )["s"]
    tiny = render_example.render_previews(
        _example(tmp_path),
        [manifest.IdsDiagram(id="t", example="tiny_example", view="Tiny", before={})],
    )["t"]

    stored_legend = _text(stored.split('class="risa-ids__legend"')[1])
    assert "Pink is the key" in stored_legend
    assert "Pink is the state" not in stored_legend
    assert "Pink is the state" in _text(tiny.split('class="risa-ids__legend"')[1])


def test_a_log_preview_shows_what_add_view_warns(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "stored_example", _STORED)
    preview = manifest.LogPreview(id="w", example="stored_example", view="Stored", label="In your log")

    result = render_example.render_previews(example, [preview])["w"]

    assert '<pre class="risa-error">' in result
    assert "In your log" in result
    assert "view stored keeps its state in store &#x27;default&#x27;, which is a MemoryStore" in result


def test_a_log_preview_comes_out_even_after_another_preview_added_the_view(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "stored_example", _STORED)
    previews = [
        manifest.IdsDiagram(id="i", example="stored_example", view="Stored", before={}),
        manifest.LogPreview(id="w", example="stored_example", view="Stored"),
    ]

    assert "MemoryStore" in render_example.render_previews(example, previews)["w"]


def test_a_log_preview_of_a_view_that_warns_about_nothing_fails_the_step(tmp_path: pathlib.Path) -> None:
    preview = manifest.LogPreview(id="w", example="tiny_example", view="Tiny")

    with pytest.raises(render_example.RenderError, match="logged no warning"):
        render_example.render_previews(_example(tmp_path), [preview])


def test_a_click_path_of_a_stored_view_fails_since_the_click_keeps_the_key(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "stored_example", _STORED)
    preview = _click(example="stored_example", view="Stored", before={"n": 1}, after={"n": 2}, handler="go")

    with pytest.raises(render_example.RenderError, match="must change the fragment"):
        render_example.render_previews(example, [preview])


def test_an_ids_diagram_of_a_stored_view_shows_one_key_and_marks_nothing(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "stored_example", _STORED)
    preview = manifest.IdsDiagram(
        id="i",
        example="stored_example",
        view="Stored",
        before={"n": 0},
        after={"n": 0, "label": "Go"},
    )

    result = render_example.render_previews(example, [preview])["i"]

    first, second = result.split('<p class="risa-ids__title">')[1:]
    assert _rows(first) == _rows(second)
    assert "risa-live__changed" not in result


def test_two_renders_of_a_stored_view_diagram_are_identical(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "stored_example", _STORED)
    preview = manifest.IdsDiagram(id="i", example="stored_example", view="Stored", before={}, after={"n": 0})

    assert render_example.render_previews(example, [preview]) == render_example.render_previews(example, [preview])


def test_an_ids_diagram_can_name_its_state_instead_of_listing_it(tmp_path: pathlib.Path) -> None:
    preview = manifest.IdsDiagram(id="i", example="tiny", view="Tiny", before={"count": 3}, title="Three clicks")

    result = render_example.render_previews(_example(tmp_path), [preview])["i"]

    assert "Three clicks" in result
    assert "count = 3" not in result


def test_an_ids_diagram_whose_groups_differ_in_size_fails_the_step(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "stored_example", _STORED)
    preview = manifest.IdsDiagram(id="uneven", example="stored_example", view="Stored", before={}, after={"n": 2})

    with pytest.raises(render_example.RenderError, match=r"'uneven'.*components"):
        render_example.render_previews(example, [preview])


def test_a_label_a_field_value_and_content_are_escaped(tmp_path: pathlib.Path) -> None:
    example = _example(tmp_path, "stored_example", _STORED)
    ids = manifest.IdsDiagram(
        id="i",
        example="stored_example",
        view="Stored",
        before={"label": "a<b&c"},
    )
    rows = manifest.ViewPreview(id="v", example="rows_example", view="Rows", fields={}, content="1 < 2 & 3")

    result = render_example.render_previews(example, [ids])["i"]
    drawn = render_example.render_previews(_example(tmp_path, "rows_example", _ROWS), [rows])["v"]

    assert "a&lt;b&amp;c" in result
    assert "a<b" not in result
    assert "label = &#x27;a&lt;b&amp;c&#x27;" in result
    assert "<p>1 &lt; 2 &amp; 3</p>" in drawn


def test_content_on_a_row_beside_a_container_fails_the_step(tmp_path: pathlib.Path) -> None:
    preview = manifest.ViewPreview(id="v", example="mixed_example", view="Mixed", fields={}, content="Hello")

    with pytest.raises(render_example.RenderError, match="content"):
        render_example.render_previews(_example(tmp_path, "mixed_example", _MIXED), [preview])


_CLICKED = """
import risa
from risa import ui


# --8<-- [start:view]
@risa.register(name="clicked")
class Clicked(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.bump, label=f"Count {self.count}"))

    @risa.handler
    async def bump(self, ctx: risa.ComponentContext) -> None:
        self.count += 1
        await ctx.rerender()


# --8<-- [end:view]
"""


_INTERACTION_SECRET: typing.Final = "interaction-token"  # ruff:ignore[hardcoded-password-string]


async def _ids_after_clicks(source: pathlib.Path, clicks: int) -> list[str]:
    spec = importlib.util.spec_from_file_location("clicked_example", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = unittest.mock.Mock(spec=hikari.GatewayBot)
    app.rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
    app.rest.create_interaction_response = unittest.mock.AsyncMock()
    app.event_manager = unittest.mock.Mock(spec=hikari.api.EventManager)
    client = risa.client_from_app(app, store=risa.MemoryStore())
    client.add_view(module.Clicked)

    payload = (await client.build(module.Clicked())).components[0].build()[0]
    identifiers = [payload["components"][0]["custom_id"]]
    for _ in range(clicks):
        row = hikari.ActionRowComponent(
            type=hikari.ComponentType.ACTION_ROW,
            id=0,
            components=[
                hikari.ButtonComponent(
                    type=hikari.ComponentType.BUTTON,
                    id=0,
                    style=hikari.ButtonStyle.PRIMARY,
                    label=None,
                    emoji=None,
                    custom_id=identifiers[-1],
                    url=None,
                    is_disabled=False,
                ),
            ],
        )
        interaction = unittest.mock.Mock(
            spec=hikari.ComponentInteraction,
            id=hikari.Snowflake(900),
            token=_INTERACTION_SECRET,
            application_id=hikari.Snowflake(42),
            custom_id=identifiers[-1],
            values=(),
            channel_id=hikari.Snowflake(7),
            guild_id=None,
            member=None,
        )
        interaction.message = unittest.mock.Mock(
            spec=hikari.Message,
            id=hikari.Snowflake(1),
            channel_id=hikari.Snowflake(7),
            components=[row],
            flags=hikari.MessageFlag.NONE,
        )
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
        call = app.rest.create_interaction_response.await_args
        assert call is not None
        answer = call.kwargs
        rendered = (answer.get("components") or [answer["component"]])[0].build()[0]
        identifiers.append(rendered["components"][0]["custom_id"])
    return identifiers


async def test_the_diagrams_show_the_ids_a_real_click_leaves(tmp_path: pathlib.Path) -> None:
    source = _example(tmp_path, "clicked_example", _CLICKED)
    click = manifest.ClickPath(
        id="c",
        example="clicked_example",
        view="Clicked",
        before={"count": 0},
        after={"count": 1},
        handler="bump",
    )
    live = manifest.LivePreview(
        id="l",
        example="clicked_example",
        view="Clicked",
        section="view",
        field="count",
    )
    ids = manifest.IdsDiagram(
        id="i", example="clicked_example", view="Clicked", before={"count": 0}, after={"count": 1}
    )

    drawn = render_example.render_previews(source, [click, live, ids])
    real = await _ids_after_clicks(source, 3)

    steps = drawn["c"].split('<li class="risa-flow__step">')
    shown = ["".join(_spans(step, "route") + _spans(step, "state")) for step in (steps[1], steps[6])]
    assert shown == real[:2]
    assert _script_ids(drawn["l"], [0, 1, 2, 3]) == real
    assert [identifier for _, identifier in _rows(drawn["i"])] == real[:2]
