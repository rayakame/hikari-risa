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
from tools.docs import discord_html
from tools.docs import manifest

__all__ = ("RenderError", "main", "mark_changes", "render_previews")

_ROOT: typing.Final = pathlib.Path(__file__).resolve().parents[2]


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
    cls = _view(client, module, example, preview.view)
    if isinstance(preview, manifest.ViewPreview):
        return discord_html.render_message(await _payloads(client, cls, preview.fields))
    if isinstance(preview, manifest.ErrorPreview):
        try:
            await client.build(cls(**preview.fields))
        except errors.RisaError as error:
            return f'<pre class="risa-error"><code>{html.escape(str(error))}</code></pre>'
        raise RenderError(example, f"the build of preview {preview.id!r} did not raise")
    return await _click_path(client, cls, example, preview)


def _view(client: risa.Client, module: object, example: pathlib.Path, name: str) -> type[risa.View]:
    cls = getattr(module, name, None)
    if not (isinstance(cls, type) and issubclass(cls, risa.View)):
        raise RenderError(example, f"it has no view named {name!r}")
    client.add_view(cls)
    return cls


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
    before = _custom_id(await _payloads(client, cls, preview.before), token, example)
    after = _custom_id(await _payloads(client, cls, preview.after), token, example)
    if (before.cookie, before.handler) != (after.cookie, after.handler) or before.fragment == after.fragment:
        raise RenderError(example, f"the click path {preview.id!r} must change the fragment and nothing else")
    fields = ", ".join(f"{name} = {value!r}" for name, value in preview.before.items())
    steps = (
        ("Click", "The button sends its id to the bot.", _show(before)),
        ("Read the id", "The cookie names the view, the token the handler.", _parts(before)),
        ("Find the view", f"The cookie routes to {preview.view}.", ""),
        ("Unpack the state", f"The fragment holds {fields or 'nothing'}.", ""),
        ("Run the handler", f"{preview.handler} changes the fields.", ""),
        ("Rerender", "Only the fragment of the new id differs.", _show(after)),
    )
    return _diagram(steps)


def _custom_id(
    payloads: collections.abc.Sequence[collections.abc.Mapping[str, object]],
    token: str,
    example: pathlib.Path,
) -> codec.CustomID:
    for raw in _custom_ids(payloads):
        parsed = codec.parse_custom_id(raw)
        if parsed is not None and parsed.handler == token:
            return parsed
    raise RenderError(example, "no component routes to the handler")


def _custom_ids(value: object) -> collections.abc.Iterator[str]:
    if isinstance(value, collections.abc.Mapping):
        pairs = typing.cast("collections.abc.Mapping[object, object]", value)
        for key, item in pairs.items():
            if key == "custom_id" and isinstance(item, str):
                yield item
            else:
                yield from _custom_ids(item)
    elif isinstance(value, list):
        for item in typing.cast("list[object]", value):
            yield from _custom_ids(item)


def _show(identifier: codec.CustomID) -> str:
    return (
        '<code class="risa-id">'
        f'<span class="risa-id-cookie">{html.escape(identifier.cookie)}</span>'
        f'<span class="risa-id-handler">{html.escape(identifier.handler)}</span>'
        f'<span class="risa-id-fragment">{html.escape(identifier.fragment)}</span>'
        f"</code>"
    )


def _parts(identifier: codec.CustomID) -> str:
    return (
        f'<code class="risa-id-plain">cookie {html.escape(identifier.cookie)}'
        f" &middot; handler {html.escape(identifier.handler)}</code>"
    )


def _diagram(steps: collections.abc.Sequence[tuple[str, str, str]]) -> str:
    cards = "".join(
        f'<li class="risa-click__step"><span class="risa-click__n">{number}</span>'
        f"<strong>{html.escape(title)}</strong><span>{html.escape(detail)}</span>{extra}</li>"
        for number, (title, detail, extra) in enumerate(steps, start=1)
    )
    return f'<ol class="risa-click">{cards}</ol>'


async def _live(client: risa.Client, module: object, example: pathlib.Path, preview: manifest.LivePreview) -> str:
    code = _section(example, preview.section)
    marked = _mark_lines(
        code,
        _line_of(code, f"{preview.field}:", example),
        _line_of(code, f"self.{preview.field} +=", example),
        f"__live-{preview.id}",
    )
    cls = _view(client, module, example, preview.view)
    builds = [await _payloads(client, cls, {preview.field: value}) for value in range(preview.stop + 1)]
    identifiers = [_only_custom_id(payloads, example) for payloads in builds]
    frames = "".join(
        _live_frame(
            _spent(payloads) if value == preview.stop else payloads,
            value,
            mark_changes(identifier, identifiers[max(value - 1, 0)]),
        )
        for value, (payloads, identifier) in enumerate(zip(builds, identifiers, strict=True))
    )
    message = discord_html.frame_message(f'<div data-live-slot="message">{discord_html.render_body(builds[0])}</div>')
    note = f'<p class="risa-live__note">{html.escape(preview.note, quote=False)}</p>' if preview.note else ""
    return (
        '<div class="risa-live" data-live-preview>'
        f'{marked}<div class="risa-live__side"><div data-search-exclude>{message}</div>'
        '<p class="risa-live__id" data-search-exclude><span class="risa-live__id-label">custom_id</span> '
        f'<span data-live-slot="id">{html.escape(identifiers[0], quote=False)}</span></p>'
        f"{note}</div>{frames}</div>"
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
    identifiers = list(_custom_ids(payloads))
    if len(identifiers) != 1:
        raise RenderError(example, f"a live preview needs exactly one custom_id, its view has {len(identifiers)}")
    return identifiers[0]


def _spent(
    payloads: collections.abc.Sequence[collections.abc.Mapping[str, object]],
) -> collections.abc.Sequence[collections.abc.Mapping[str, object]]:
    return [typing.cast("collections.abc.Mapping[str, object]", _disabled(payload)) for payload in payloads]


def _disabled(value: object) -> object:
    if isinstance(value, collections.abc.Mapping):
        pairs = typing.cast("collections.abc.Mapping[object, object]", value)
        disabled = {key: _disabled(item) for key, item in pairs.items()}
        if "custom_id" in pairs:
            disabled["disabled"] = True
        return disabled
    if isinstance(value, list):
        return [_disabled(item) for item in typing.cast("list[object]", value)]
    return value


def _live_frame(
    payloads: collections.abc.Sequence[collections.abc.Mapping[str, object]],
    value: int,
    identifier: str,
) -> str:
    inline = f'<span class="c1">  # now {value}</span>' if value else ""
    return (
        "<template data-live-frame data-search-exclude>"
        f'<div data-live-part="message">{discord_html.render_body(payloads, interactive=True)}</div>'
        f'<span data-live-part="value">{inline}</span>'
        f'<span data-live-part="id">{identifier}</span>'
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
