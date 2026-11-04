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
"""Draws Discord's dark theme: the components of a message and a small Markdown dialect."""

from __future__ import annotations

import collections.abc
import html
import re
import typing

__all__ = (
    "frame_message",
    "render_body",
    "render_markdown",
    "render_message",
    "render_reply",
    "render_thinking",
    "wrap_result",
)

_TEXT_DISPLAY: typing.Final = 10
_SEPARATOR: typing.Final = 14
_SECTION: typing.Final = 9
_THUMBNAIL: typing.Final = 11
_MEDIA_GALLERY: typing.Final = 12
_FILE: typing.Final = 13
_ROW: typing.Final = 1
_BUTTON: typing.Final = 2
_CONTAINER: typing.Final = 17
_SELECTS: typing.Final = frozenset({3, 5, 6, 7, 8})

_BUTTON_STYLES: typing.Final = {1: "primary", 2: "secondary", 3: "success", 4: "danger", 5: "link", 6: "premium"}
_LARGE_SPACING: typing.Final = 2

_ICON_IMAGE: typing.Final = (
    '<svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">'
    '<path d="M4 18l5-6 4 4 3-3 4 5M4 5h16v14H4z" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>'
)
_ICON_FILE: typing.Final = (
    '<svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">'
    '<path d="M5 2h7l4 4v12H5z" fill="none" stroke="#949ba4" stroke-width="1.5"/></svg>'
)
_ICON_CHEVRON: typing.Final = (
    '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">'
    '<path d="M3 6l5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>'
)
_ICON_EXTERNAL: typing.Final = (
    '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">'
    '<path d="M5 2h7v7M12 2L3 11" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>'
)
_ICON_EYE: typing.Final = (
    '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">'
    '<path d="M1 8s2.6-5 7-5 7 5 7 5-2.6 5-7 5-7-5-7-5z" fill="none" stroke="currentColor" stroke-width="1.4"/>'
    '<circle cx="8" cy="8" r="2" fill="currentColor"/></svg>'
)

_SLOT: typing.Final = re.compile(r"\ue000(\d+)\ue001")
_CODE_SPAN: typing.Final = re.compile(r"(`+)(.+?)\1", re.DOTALL)
_ESCAPED: typing.Final = re.compile(r"\\([\\`*_~#\[\]()>\-])")
# A slot in a URL would come back unescaped inside the href.
_LINK: typing.Final = re.compile(r"\[([^\]]+)\]\((https?://[^\s)\ue000]+)\)")
_MARKERS: typing.Final = (
    ("***", "<strong><em>", "</em></strong>"),
    ("___", "<u><em>", "</em></u>"),
    ("**", "<strong>", "</strong>"),
    ("__", "<u>", "</u>"),
    ("~~", "<s>", "</s>"),
    ("*", "<em>", "</em>"),
    ("_", "<em>", "</em>"),
)
_HEADING: typing.Final = re.compile(r"(#{1,3}) (.+)")


def render_message(
    components: collections.abc.Sequence[collections.abc.Mapping[str, object]],
    *,
    author: str = "Risa Bot",
    interactive: bool = False,
) -> str:
    """Draw the components of a message as Discord's dark theme does.

    Parameters
    ----------
    components
        The payloads of the message's top-level components, as the builders produce them.
    author
        The name of the bot that sent the message.
    interactive
        Draw buttons that carry a ``custom_id`` as real ``<button>`` elements, not inert spans.

    Returns
    -------
    :
        One block of HTML without blank lines, safe to include in a Markdown page.
    """
    return frame_message(render_body(components, interactive=interactive), author=author)


def render_body(
    components: collections.abc.Sequence[collections.abc.Mapping[str, object]],
    *,
    interactive: bool = False,
) -> str:
    """Draw the components of a message without the avatar and the author line around them."""
    return "".join(_component(node, interactive=interactive) for node in components)


def frame_message(body: str, *, author: str = "Risa Bot") -> str:
    """Put a drawn body under the avatar and the author line of a message."""
    name = html.escape(author)
    return (
        '<div class="dc">'
        f'<span class="dc-avatar" role="img" aria-label="{name}"></span>'
        '<div class="dc-main">'
        f'<div class="dc-who"><span class="dc-name">{name}</span><span class="dc-app">APP</span>'
        '<span class="dc-time">Today at 12:00</span></div>'
        f"{body}</div></div>"
    )


def render_reply(text: str, *, ephemeral: bool, author: str = "Risa Bot") -> str:
    """Draw a plain-text reply, with Discord's note under a reply only its user sees."""
    return frame_message(
        f'<div class="dc-text">{render_markdown(text)}</div>{_ephemeral_note(ephemeral=ephemeral)}',
        author=author,
    )


def render_thinking(*, ephemeral: bool, author: str = "Risa Bot") -> str:
    """Draw Discord's placeholder for a deferred answer."""
    name = html.escape(author)
    return frame_message(
        f'<p class="dc-thinking">{name} is thinking…</p>{_ephemeral_note(ephemeral=ephemeral)}',
        author=author,
    )


def wrap_result(markup: str, label: str) -> str:
    """Wrap a preview in the block a page shows under its code, headed by `label` unless it is empty."""
    heading = f'<p class="risa-result__label">{html.escape(label)}</p>' if label else ""
    return f'<div class="risa-result" data-search-exclude>{heading}{markup}</div>'


def render_markdown(text: str) -> str:
    """Turn Discord's Markdown into HTML; every character of `text` is escaped first."""
    blocks: list[str] = []
    paragraph: list[str] = []
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            index = _fence(lines, index, blocks, paragraph)
        elif line.startswith(("- ", "* ")):
            _flush(paragraph, blocks)
            items, index = _run(lines, index, ("- ", "* "))
            blocks.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ul>")
        elif line.startswith("> "):
            _flush(paragraph, blocks)
            quoted, index = _run(lines, index, ("> ",))
            blocks.append(
                '<blockquote class="dc-quote">' + "<br>".join(_inline(item) for item in quoted) + "</blockquote>"
            )
        else:
            _line(line, blocks, paragraph)
            index += 1
    _flush(paragraph, blocks)
    return "".join(blocks)


def _line(line: str, blocks: list[str], paragraph: list[str]) -> None:
    heading = _HEADING.fullmatch(line)
    if heading is not None:
        _flush(paragraph, blocks)
        blocks.append(f'<p class="dc-h{len(heading.group(1))}">{_inline(heading.group(2))}</p>')
    elif line.startswith("-# "):
        _flush(paragraph, blocks)
        blocks.append(f'<p class="dc-sub">{_inline(line[3:])}</p>')
    elif line.strip():
        paragraph.append(line)
    else:
        _flush(paragraph, blocks)


def _flush(paragraph: list[str], blocks: list[str]) -> None:
    if paragraph:
        blocks.append("<p>" + "<br>".join(_inline(line) for line in paragraph) + "</p>")
        paragraph.clear()


def _fence(lines: collections.abc.Sequence[str], start: int, blocks: list[str], paragraph: list[str]) -> int:
    _flush(paragraph, blocks)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("```")), len(lines))
    code = html.escape("\n".join(lines[start + 1 : end]), quote=False)
    blocks.append(f'<pre class="dc-code"><code>{code}</code></pre>')
    return end + 1


def _run(lines: collections.abc.Sequence[str], start: int, prefixes: tuple[str, ...]) -> tuple[list[str], int]:
    end = start
    while end < len(lines) and lines[end].startswith(prefixes):
        end += 1
    return [line[2:] for line in lines[start:end]], end


def _inline(text: str) -> str:
    slots: list[str] = []

    def stash(markup: str) -> str:
        slots.append(markup)
        return f"\ue000{len(slots) - 1}\ue001"

    def restore(markup: str) -> str:
        return _SLOT.sub(lambda m: restore(slots[int(m.group(1))]), markup)

    text = text.replace("\ue000", "\ufffd").replace("\ue001", "\ufffd")
    text = _CODE_SPAN.sub(lambda m: stash(f"<code>{html.escape(m.group(2).strip(), quote=False)}</code>"), text)
    text = _ESCAPED.sub(lambda m: stash(html.escape(m.group(1), quote=False)), text)
    text = html.escape(text, quote=False)
    text = _LINK.sub(lambda m: stash(_link(m.group(1), m.group(2))), text)
    return restore(_format(text))


def _link(label: str, url: str) -> str:
    safe = url.replace('"', "&quot;").replace("'", "&#x27;")
    return f'<a class="dc-link" href="{safe}">{_format(label)}</a>'


def _format(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        for marker, opening, closing in _MARKERS:
            if not text.startswith(marker, index):
                continue
            end = _closing(text, index, marker)
            if end != -1:
                out.append(opening + _format(text[index + len(marker) : end]) + closing)
                index = end + len(marker)
                break
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def _ends_word(text: str, index: int, marker: str) -> bool:
    after = index + len(marker)
    return marker != "_" or after >= len(text) or not text[after].isalnum()


def _closing(text: str, start: int, marker: str) -> int:
    inner = start + len(marker)
    if inner >= len(text) or text[inner].isspace():
        return -1
    if marker == "_" and start > 0 and text[start - 1].isalnum():
        return -1
    index = inner
    while index < len(text):
        if len(marker) == 1 and text.startswith(marker * 2, index):
            index += 2
        elif (
            text.startswith(marker, index)
            and text[inner:index].strip(marker[0])
            and not text[index - 1].isspace()
            and _ends_word(text, index, marker)
        ):
            return index
        else:
            index += 1
    return -1


def _ephemeral_note(*, ephemeral: bool) -> str:
    if not ephemeral:
        return ""
    return (
        f'<div class="dc-eph">{_ICON_EYE}<span>Only you can see this</span>'
        "<span>&middot;</span><a>Dismiss message</a></div>"
    )


def _component(node: collections.abc.Mapping[str, object], *, interactive: bool) -> str:
    kind = node.get("type")
    if kind == _CONTAINER:
        return _container(node, interactive=interactive)
    if kind == _SECTION:
        return _section(node, interactive=interactive)
    if kind == _ROW:
        return _row(node, interactive=interactive)
    if kind == _BUTTON:
        return _button(node, interactive=interactive)
    return _leaf(node)


def _leaf(node: collections.abc.Mapping[str, object]) -> str:
    kind = node.get("type")
    if kind == _TEXT_DISPLAY:
        return f'<div class="dc-text">{render_markdown(_string(node, "content"))}</div>'
    if kind == _SEPARATOR:
        return _separator(node)
    if kind == _THUMBNAIL:
        return _thumbnail(node)
    if kind == _MEDIA_GALLERY:
        return _gallery(node)
    if kind == _FILE:
        return _file(node)
    if kind in _SELECTS:
        return _select(node)
    msg = f"no drawing for the component type {kind!r}"
    raise ValueError(msg)


def _container(node: collections.abc.Mapping[str, object], *, interactive: bool) -> str:
    accent = node.get("accent_color")
    classes = "dc-box"
    style = ""
    if isinstance(accent, int):
        classes += " dc-accent"
        style = f' style="--dc-accent:#{accent:06x}"'
    inner = "".join(_component(child, interactive=interactive) for child in _nodes(node, "components"))
    return f'<div class="{classes}"{style}>{inner}</div>'


def _separator(node: collections.abc.Mapping[str, object]) -> str:
    classes = "dc-sep"
    if node.get("divider", True) is False:
        classes += " dc-sep-blank"
    if node.get("spacing") == _LARGE_SPACING:
        classes += " dc-sep-large"
    return f'<div class="{classes}"></div>'


def _section(node: collections.abc.Mapping[str, object], *, interactive: bool) -> str:
    text = "".join(_component(child, interactive=interactive) for child in _nodes(node, "components"))
    accessory = _mapping(node.get("accessory"))
    side = _component(accessory, interactive=interactive) if accessory else ""
    return f'<div class="dc-sec"><div class="dc-sec-text">{text}</div>{side}</div>'


def _thumbnail(node: collections.abc.Mapping[str, object]) -> str:
    label = html.escape(_optional(node, "description") or "Image")
    return f'<span class="dc-thumb" role="img" aria-label="{label}">{_ICON_IMAGE}</span>'


def _gallery(node: collections.abc.Mapping[str, object]) -> str:
    tiles: list[str] = []
    for index, item in enumerate(_nodes(node, "items")):
        label = html.escape(_optional(item, "description") or "Image")
        icon = _ICON_IMAGE if index == 0 else ""
        tiles.append(f'<span class="dc-tile" role="img" aria-label="{label}">{icon}</span>')
    return f'<div class="dc-gallery dc-gallery-{min(len(tiles), 3)}">{"".join(tiles)}</div>'


def _file(node: collections.abc.Mapping[str, object]) -> str:
    name = html.escape(_string(_mapping(node.get("file")), "url").rsplit("/", 1)[-1])
    return f'<div class="dc-file">{_ICON_FILE}<a>{name}</a></div>'


def _row(node: collections.abc.Mapping[str, object], *, interactive: bool) -> str:
    children = "".join(_component(child, interactive=interactive) for child in _nodes(node, "components"))
    return f'<div class="dc-row">{children}</div>'


def _button(node: collections.abc.Mapping[str, object], *, interactive: bool) -> str:
    raw_style = node.get("style")
    style = _BUTTON_STYLES.get(raw_style, "primary") if isinstance(raw_style, int) else "primary"
    classes = f"dc-btn dc-{style}"
    disabled = node.get("disabled") is True
    if disabled:
        classes += " dc-disabled"
    label = html.escape(_optional(node, "label") or ("Subscribe" if style == "premium" else ""))
    emoji = _mapping(node.get("emoji"))
    glyph = _optional(emoji, "name")
    if "id" in emoji:
        glyph = f":{glyph or 'emoji'}:"
    if glyph:
        label = f"{html.escape(glyph)} {label}".strip()
    if style == "link":
        label += f" {_ICON_EXTERNAL}"
    if interactive and "custom_id" in node:
        marker = " disabled" if disabled else ""
        return f'<button type="button" class="{classes}"{marker}>{label}</button>'
    return f'<span class="{classes}">{label}</span>'


def _select(node: collections.abc.Mapping[str, object]) -> str:
    placeholder = html.escape(_optional(node, "placeholder") or "Make a selection")
    classes = "dc-select dc-disabled" if node.get("disabled") is True else "dc-select"
    return f'<div class="{classes}"><span>{placeholder}</span>{_ICON_CHEVRON}</div>'


def _mapping(value: object) -> collections.abc.Mapping[str, object]:
    if not isinstance(value, collections.abc.Mapping):
        return {}
    pairs = typing.cast("collections.abc.Mapping[object, object]", value)
    return {key: item for key, item in pairs.items() if isinstance(key, str)}


def _nodes(
    node: collections.abc.Mapping[str, object],
    key: str,
) -> collections.abc.Sequence[collections.abc.Mapping[str, object]]:
    value = node.get(key)
    if not isinstance(value, collections.abc.Sequence) or isinstance(value, str):
        return []
    return [_mapping(child) for child in typing.cast("collections.abc.Sequence[object]", value)]


def _optional(node: collections.abc.Mapping[str, object], key: str) -> str | None:
    value = node.get(key)
    return value if isinstance(value, str) else None


def _string(node: collections.abc.Mapping[str, object], key: str) -> str:
    return _optional(node, key) or ""
