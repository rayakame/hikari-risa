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
"""Builds the views of one example with risa and draws the previews of the documentation."""

from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import hashlib
import html
import importlib.util
import itertools
import json
import logging.handlers
import pathlib
import re
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tempfile
import textwrap
import traceback
import typing
from unittest import mock

import hikari
import msgspec
import pygments
from pygments import formatters
from pygments import lexers

import risa
from risa import errors
from risa.internal import codec
from risa.internal import registry
from risa.state import anchor
from risa.state import message
from tools.docs import discord_html
from tools.docs import manifest

__all__ = ("RenderError", "main", "mark_changes", "render_previews")

_ROOT: typing.Final = pathlib.Path(__file__).resolve().parents[2]
_ROW: typing.Final = 1
_BUTTON: typing.Final = 2
# Far from any number a view draws by itself, so finding it in the markup finds the field.
_DRAWN_COUNT: typing.Final = 918273645
_COUNT_SLOT: typing.Final = "<span data-live-count></span>"


class RenderError(Exception):
    """A preview could not be drawn."""

    def __init__(self, example: pathlib.Path, reason: str, *, preview_id: str | None = None) -> None:
        where = f"preview {preview_id!r}: " if preview_id is not None else ""
        super().__init__(f"{example}: {where}{reason}")
        self.example = example
        self.reason = reason
        self.preview_id = preview_id


def render_previews(
    example: pathlib.Path,
    previews: collections.abc.Sequence[manifest.Preview],
) -> collections.abc.Mapping[str, str]:
    """Draw the previews of one example in a process of its own.

    Each example gets a fresh view registry, so two examples may register the same view name.

    Parameters
    ----------
    example
        The example file.
    previews
        The previews to draw, which name views of that example.

    Returns
    -------
    :
        The HTML of each preview, by id.

    Raises
    ------
    RenderError
        If the example cannot be imported, a view is missing or a build fails.
    """
    with tempfile.TemporaryDirectory() as directory:
        listing = pathlib.Path(directory) / "previews.json"
        listing.write_bytes(msgspec.json.encode(list(previews)))
        done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            [sys.executable, "-m", "tools.docs.render_example", str(example), str(listing)],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    if done.returncode != 0:
        raise RenderError(example, done.stderr.strip() or "the renderer failed", preview_id=_failed(done.stdout))
    try:
        return typing.cast("collections.abc.Mapping[str, str]", json.loads(done.stdout))
    except json.JSONDecodeError as error:
        raise RenderError(example, f"the renderer's result is not JSON: {error}") from error


def _failed(output: str) -> str | None:
    try:
        failed = json.loads(output)["failed"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    return failed if isinstance(failed, str) else None


def main(argv: collections.abc.Sequence[str]) -> int:
    example, listing = (pathlib.Path(argument) for argument in argv)
    previews = msgspec.json.decode(listing.read_bytes(), type=collections.abc.Sequence[manifest.Preview])
    current: list[str] = []
    try:
        with contextlib.redirect_stdout(sys.stderr):
            rendered = asyncio.run(_render_all(example, previews, current))
    except Exception:  # ruff:ignore[blind-except]
        traceback.print_exc()
        sys.stdout.write(json.dumps({"failed": current[-1] if current else None}))
        return 1
    sys.stdout.write(json.dumps(rendered, sort_keys=True))
    return 0


async def _render_all(
    example: pathlib.Path,
    previews: collections.abc.Sequence[manifest.Preview],
    current: list[str],
) -> dict[str, str]:
    module = _import(example)
    counter = itertools.count()

    def token_bytes(length: int) -> bytes:
        return hashlib.sha256(str(next(counter)).encode()).digest()[:length]

    with mock.patch.object(anchor.secrets, "token_bytes", token_bytes):
        client = _client()
        add_dependencies = getattr(module, "add_dependencies", None)
        if add_dependencies is not None:
            add_dependencies(client)
        rendered: dict[str, str] = {}
        for preview in previews:
            current.append(preview.id)
            if isinstance(preview, manifest.LivePreview):
                rendered[preview.id] = await _live(client, module, example, preview)
            else:
                rendered[preview.id] = discord_html.wrap_result(
                    await _draw(client, module, example, preview),
                    preview.label,
                )
    return rendered


def _import(example: pathlib.Path) -> object:
    spec = importlib.util.spec_from_file_location(f"docs_example_{example.stem}", example)
    if spec is None or spec.loader is None:
        raise RenderError(example, "it cannot be imported")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client() -> risa.Client:
    app = mock.Mock(spec=hikari.GatewayBot)
    app.rest = mock.Mock(spec=hikari.api.RESTClient)
    app.event_manager = mock.Mock(spec=hikari.api.EventManager)
    return risa.client_from_app(app, store=risa.MemoryStore(), key_prefix="risa")


async def _draw(client: risa.Client, module: object, example: pathlib.Path, preview: manifest.StaticPreview) -> str:
    if isinstance(preview, manifest.ReplyPreview):
        return discord_html.render_reply(preview.text, ephemeral=preview.ephemeral)
    if isinstance(preview, manifest.ThinkingPreview):
        return discord_html.render_thinking(ephemeral=preview.ephemeral)
    return await _draw_view(client, _view(client, module, example, preview.view), example, preview)


async def _draw_view(
    client: risa.Client,
    cls: type[risa.View],
    example: pathlib.Path,
    preview: manifest.ViewPreview
    | manifest.ErrorPreview
    | manifest.LogPreview
    | manifest.ClickPath
    | manifest.IdsDiagram,
) -> str:
    if isinstance(preview, manifest.ViewPreview):
        payloads = await _payloads(client, cls, preview.fields)
        if preview.content and any(payload.get("type") != _ROW for payload in payloads):
            raise RenderError(
                example,
                f"the view {preview.view!r} is not rows alone, and Discord refuses content beside Components V2",
            )
        return discord_html.render_message(payloads, content=preview.content)
    if isinstance(preview, manifest.ErrorPreview):
        try:
            await client.build(cls(**preview.fields))
        except errors.RisaError as error:
            return f'<pre class="risa-error"><code>{html.escape(str(error))}</code></pre>'
        raise RenderError(example, f"the build of preview {preview.id!r} did not raise")
    if isinstance(preview, manifest.LogPreview):
        return _startup_warnings(cls, example, preview)
    if isinstance(preview, manifest.IdsDiagram):
        return await _ids(client, cls, example, preview)
    return await _click_path(client, cls, example, preview)


def _view(client: risa.Client, module: object, example: pathlib.Path, name: str) -> type[risa.View]:
    cls = getattr(module, name, None)
    if not (isinstance(cls, type) and issubclass(cls, risa.View)):
        raise RenderError(example, f"it has no view named {name!r}")
    client.add_view(cls)
    return cls


def _startup_warnings(cls: type[risa.View], example: pathlib.Path, preview: manifest.LogPreview) -> str:
    captured = logging.handlers.BufferingHandler(capacity=16)
    logger = logging.getLogger("risa")
    logger.addHandler(captured)
    try:
        # A client warns once per store, and the shared client may have warned already.
        _client().add_view(cls)
    finally:
        logger.removeHandler(captured)
    if not captured.buffer:
        raise RenderError(example, f"adding the view {preview.view!r} logged no warning")
    text = "\n".join(record.getMessage() for record in captured.buffer)
    return f'<pre class="risa-error"><code>{html.escape(text)}</code></pre>'


async def _payloads(
    client: risa.Client,
    cls: type[risa.View],
    fields: collections.abc.Mapping[str, object],
) -> collections.abc.Sequence[collections.abc.Mapping[str, object]]:
    built = await client.build(cls(**fields))
    return [component.build()[0] for component in built.components]


async def _click_path(
    client: risa.Client,
    cls: type[risa.View],
    example: pathlib.Path,
    preview: manifest.ClickPath,
) -> str:
    meta = registry.require_meta(cls)
    token = next((key for key, record in meta.handlers.items() if record.handler_id == preview.handler), None)
    if token is None:
        raise RenderError(example, f"the view {preview.view!r} has no handler {preview.handler!r}")
    if preview.before == preview.after:
        raise RenderError(example, f"the click path {preview.id!r} must change a field")
    with _same_keys(preview.id):
        payloads_before = await _payloads(client, cls, preview.before)
        with _sequence(1):
            payloads_after = await _payloads(client, cls, preview.after)
    raw_before, before = _routed_id(payloads_before, token, example)
    raw_after, after = _routed_id(payloads_after, token, example)
    if (before.cookie, before.handler) != (after.cookie, after.handler) or before.fragment == after.fragment:
        raise RenderError(example, f"the click path {preview.id!r} must change the fragment and nothing else")
    fields = ", ".join(f"{name} = {value!r}" for name, value in preview.before.items())
    steps = (
        ("Click", "The button sends its id to the bot.", _id_code(raw_before, raw_before, example)),
        ("Read the id", "The id names the view and the handler.", ""),
        ("Find the view", f"risa finds {preview.view}, which add_view gave it.", ""),
        ("Unpack the state", f"The message holds {fields or 'nothing'}.", ""),
        ("Run the handler", f"{preview.handler} changes the fields.", ""),
        ("Rerender", "The button gets a new id, with the new state.", _id_code(raw_after, raw_before, example)),
    )
    return _diagram(steps)


def _same_keys(preview_id: str) -> contextlib.AbstractContextManager[object]:
    """Hand every state anchor of a diagram the same bytes, as the replicated key of a real click is."""

    def token_bytes(length: int) -> bytes:
        return hashlib.sha256(preview_id.encode()).digest()[:length]

    return mock.patch.object(anchor.secrets, "token_bytes", token_bytes)


def _sequence(number: int) -> contextlib.AbstractContextManager[object]:
    """Build a message-state anchor at sequence number `number`, which `number` clicks leave behind."""

    def first_anchor(_: message.MessageBackend, *, meta: registry.ViewMeta, view: risa.View) -> bytes:
        return anchor.MessageAnchor(meta.schema.fingerprint, number, meta.schema.pack_view(view)).pack()

    return mock.patch.object(message.MessageBackend, "first_anchor", first_anchor)


def _routed_id(
    payloads: collections.abc.Sequence[collections.abc.Mapping[str, object]],
    token: str,
    example: pathlib.Path,
) -> tuple[str, codec.CustomID]:
    for _, raw in _labelled_ids(payloads):
        parsed = codec.parse_custom_id(raw)
        if parsed is not None and parsed.handler == token:
            return raw, parsed
    raise RenderError(example, "no component routes to the handler")


def _labelled_ids(value: object) -> collections.abc.Iterator[tuple[str, str]]:
    if isinstance(value, collections.abc.Mapping):
        pairs = typing.cast("collections.abc.Mapping[str, object]", value)
        raw = pairs.get("custom_id")
        if isinstance(raw, str):
            label = pairs.get("label") or pairs.get("placeholder")
            if not isinstance(label, str):
                label = "Button" if pairs.get("type") == _BUTTON else "Select"
            yield label, raw
        for item in pairs.values():
            yield from _labelled_ids(item)
    elif isinstance(value, list):
        for item in typing.cast("list[object]", value):
            yield from _labelled_ids(item)


def _split(raw: str, example: pathlib.Path) -> tuple[str, str, str]:
    parsed = codec.parse_custom_id(raw)
    if parsed is None:
        raise RenderError(example, f"the custom_id {raw!r} is not one risa wrote, so it cannot be split")
    end = len(raw) - len(parsed.tail)
    start = end - len(parsed.fragment)
    return raw[:start], raw[start:end], raw[end:]


def _id_code(raw: str, previous: str, example: pathlib.Path) -> str:
    route, state, tail = _split(raw, example)
    changed = mark_changes(state, _split(previous, example)[1])
    argument = f'<span class="risa-id-arg">{html.escape(tail, quote=False)}</span>' if tail else ""
    return (
        f'<code><span class="risa-id-route">{html.escape(route, quote=False)}</span>'
        f'<span class="risa-id-state">{changed}</span>{argument}</code>'
    )


def _diagram(steps: collections.abc.Sequence[tuple[str, str, str]]) -> str:
    cards = "".join(
        f'<li class="risa-flow__step"><span class="risa-flow__n">{number}</span>'
        f"<strong>{html.escape(title)}</strong><span>{html.escape(detail)}</span>{extra}</li>"
        for number, (title, detail, extra) in enumerate(steps, start=1)
    )
    return f'<ol class="risa-flow">{cards}</ol>'


async def _ids(client: risa.Client, cls: type[risa.View], example: pathlib.Path, preview: manifest.IdsDiagram) -> str:
    with _same_keys(preview.id):
        before = list(_labelled_ids(await _payloads(client, cls, preview.before)))
        after = None
        if preview.after is not None:
            with _sequence(1):
                after = list(_labelled_ids(await _payloads(client, cls, preview.after)))
    groups = [_ids_group(preview.before, before, before, example, title=preview.title)]
    shown = list(before)
    if after is not None:
        if len(after) != len(before):
            raise RenderError(
                example,
                f"the ids diagram {preview.id!r} has {len(before)} components before and {len(after)} after, "
                "so their ids cannot be compared by position",
            )
        groups.append(_ids_group(preview.after or {}, after, before, example))
        shown += after
    stored = isinstance(registry.require_meta(cls).state, risa.InStore)
    legend = [
        '<span class="risa-id-route">Yellow</span> routes the click to its view and handler.',
        f'<span class="risa-id-state">Pink</span> is the {"key to the state in the store" if stored else "state"}.',
    ]
    if any(_split(raw, example)[2] for _, raw in shown):
        legend.append('<span class="risa-id-arg">Lavender</span> is the bound argument.')
    return f'<div class="risa-ids">{"".join(groups)}<p class="risa-ids__legend">{" ".join(legend)}</p></div>'


def _ids_group(
    fields: collections.abc.Mapping[str, object],
    rows: collections.abc.Sequence[tuple[str, str]],
    previous: collections.abc.Sequence[tuple[str, str]],
    example: pathlib.Path,
    *,
    title: str = "",
) -> str:
    title = title or ", ".join(f"{name} = {value!r}" for name, value in fields.items()) or "The defaults"
    lines = "".join(
        f'<div class="risa-ids__row"><span class="risa-ids__name">{html.escape(label)}</span>'
        f"{_id_code(raw, old, example)}</div>"
        for (label, raw), (_, old) in zip(rows, previous, strict=True)
    )
    return f'<p class="risa-ids__title">{html.escape(title)}</p>{lines}'


async def _live(client: risa.Client, module: object, example: pathlib.Path, preview: manifest.LivePreview) -> str:
    code = _section(example, preview.section)
    marked = _mark_lines(
        code,
        _line_of(code, f"{preview.field}:", example),
        _line_of(code, f"self.{preview.field} +=", example),
        f"__live-{preview.id}",
    )
    cls = _view(client, module, example, preview.view)
    with _sequence(0):
        first = await _payloads(client, cls, {preview.field: 0})
        second = await _payloads(client, cls, {preview.field: 1})
        drawn = await _payloads(client, cls, {preview.field: _DRAWN_COUNT})
    identifier = _only_custom_id(first, example)
    attributes = _custom_id_attributes(identifier, _only_custom_id(second, example), example)
    message = discord_html.frame_message(f'<div data-live-slot="message">{discord_html.render_body(first)}</div>')
    note = f'<p class="risa-live__note">{html.escape(preview.note, quote=False)}</p>' if preview.note else ""
    return (
        f'<div class="risa-live" data-live-preview {attributes}>'
        f'{marked}<div class="risa-live__side"><div data-search-exclude>{message}</div>'
        '<p class="risa-live__id" data-search-exclude><span class="risa-live__id-label">custom_id</span> '
        f'<span data-live-slot="id">{html.escape(identifier, quote=False)}</span></p>'
        f"{note}</div>{_live_frame(drawn, preview.field, example)}</div>"
    )


def _section(example: pathlib.Path, name: str) -> str:
    escaped = re.escape(name)
    found = re.search(
        rf"^[^\n]*--8<-- \[start:{escaped}\][^\n]*\n(.*?)^[^\n]*--8<-- \[end:{escaped}\]",
        example.read_text(encoding="utf-8"),
        re.DOTALL | re.MULTILINE,
    )
    if found is None:
        raise RenderError(example, f"it has no section {name!r}")
    return textwrap.dedent(found.group(1)).strip("\n")


def _line_of(code: str, start: str, example: pathlib.Path) -> int:
    for number, line in enumerate(code.split("\n"), start=1):
        if line.lstrip().startswith(start):
            return number
    raise RenderError(example, f"the section of its live preview has no line starting with {start!r}")


def _mark_lines(code: str, field_line: int, change_line: int, anchor: str) -> str:
    formatter: formatters.HtmlFormatter[str] = formatters.HtmlFormatter(
        cssclass="language-python highlight",
        linespans=anchor,
        wrapcode=True,
    )
    lexer = lexers.get_lexer_by_name("python")  # type: ignore[reportUnknownMemberType]
    highlighted = pygments.highlight(code, lexer, formatter).strip()
    change = f'<span id="{anchor}-{change_line}"'
    before, field, after = highlighted.replace(f"{change}>", f"{change} data-live-tint>").partition(
        f'<span id="{anchor}-{field_line}">',
    )
    line, newline, rest = after.partition("\n")
    return f'{before}{field}{line}<span class="risa-live__value" data-live-slot="value"></span>{newline}{rest}'


def _only_custom_id(
    payloads: collections.abc.Sequence[collections.abc.Mapping[str, object]],
    example: pathlib.Path,
) -> str:
    identifiers = [raw for _, raw in _labelled_ids(payloads)]
    if len(identifiers) != 1:
        raise RenderError(example, f"a live preview needs exactly one custom_id, its view has {len(identifiers)}")
    return identifiers[0]


def _custom_id_attributes(zero: str, one: str, example: pathlib.Path) -> str:
    """Write what the page's script needs to work out the `custom_id` of any count, from the ids of 0 and 1."""
    before, after = _message_anchor(zero, example).payload, _message_anchor(one, example).payload
    changed = [index for index, (old, new) in enumerate(zip(before, after, strict=True)) if old != new]
    if len(changed) != 1:
        raise RenderError(example, "the field of a live preview must change one byte of its view's state")
    attributes = {
        "prefix": zero[: codec.HEADER_LENGTH - codec.FRAGMENT_LEN_WIDTH],
        "fingerprint": _message_anchor(zero, example).schema_fp,
        "before": before[: changed[0]].hex(),
        "after": before[changed[0] + 1 :].hex(),
    }
    return " ".join(f'data-live-{name}="{html.escape(value)}"' for name, value in attributes.items())


def _message_anchor(raw: str, example: pathlib.Path) -> anchor.MessageAnchor:
    parsed = codec.parse_custom_id(raw)
    if parsed is None or parsed.tail or parsed.codec.name != "base85":
        raise RenderError(example, "a live preview needs a custom_id in base85 without bound arguments")
    state = anchor.parse(parsed.codec.decode(parsed.fragment))
    if not isinstance(state, anchor.MessageAnchor):
        raise RenderError(example, "a live preview needs a view with its state in the message")
    return state


def _live_frame(
    payloads: collections.abc.Sequence[collections.abc.Mapping[str, object]],
    field: str,
    example: pathlib.Path,
) -> str:
    body = discord_html.render_body(payloads, interactive=True)
    if body.count(str(_DRAWN_COUNT)) != 1:
        raise RenderError(example, f"a live preview must draw its field {field!r} exactly once")
    return (
        "<template data-live-frame data-search-exclude>"
        f'<div data-live-part="message">{body.replace(str(_DRAWN_COUNT), _COUNT_SLOT)}</div>'
        f'<span data-live-part="value"><span class="c1">  # now {_COUNT_SLOT}</span></span>'
        "</template>"
    )


def mark_changes(current: str, previous: str) -> str:
    """Escape a custom_id, wrapping every run of characters that differ from `previous` at their position."""
    marked: list[str] = []
    for changed, run in itertools.groupby(
        enumerate(current),
        key=lambda pair: previous[pair[0] : pair[0] + 1] != pair[1],
    ):
        text = html.escape("".join(char for _, char in run), quote=False)
        marked.append(f'<span class="risa-live__changed">{text}</span>' if changed else text)
    return "".join(marked)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
