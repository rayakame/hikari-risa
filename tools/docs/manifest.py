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
"""Reads the manifest of the documentation's previews."""

from __future__ import annotations

import collections.abc  # ruff:ignore[typing-only-standard-library-import]
import re
import typing

import msgspec

if typing.TYPE_CHECKING:
    import pathlib

__all__ = (
    "ClickPath",
    "ErrorPreview",
    "IdsDiagram",
    "LivePreview",
    "LogPreview",
    "ManifestError",
    "Preview",
    "ReplyPreview",
    "StaticPreview",
    "ThinkingPreview",
    "ViewPreview",
    "load_manifest",
)


class ManifestError(Exception):
    """The preview manifest is not valid."""

    def __init__(self, path: pathlib.Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


_ID: typing.Final = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


class ViewPreview(msgspec.Struct, frozen=True, tag_field="kind", tag="view"):
    id: str
    example: str
    view: str
    fields: dict[str, object]
    label: str = "In Discord"
    content: str = ""


class ReplyPreview(msgspec.Struct, frozen=True, tag_field="kind", tag="reply"):
    id: str
    text: str
    ephemeral: bool
    label: str = "In Discord"


class ThinkingPreview(msgspec.Struct, frozen=True, tag_field="kind", tag="thinking"):
    id: str
    label: str = "In Discord"
    ephemeral: bool = False


class ErrorPreview(msgspec.Struct, frozen=True, tag_field="kind", tag="error"):
    id: str
    example: str
    view: str
    fields: dict[str, object]
    label: str = "In Discord"


class LogPreview(msgspec.Struct, frozen=True, tag_field="kind", tag="log"):
    id: str
    example: str
    view: str
    label: str = "In your log"


class ClickPath(msgspec.Struct, frozen=True, tag_field="kind", tag="click"):
    id: str
    example: str
    view: str
    before: dict[str, object]
    after: dict[str, object]
    handler: str
    label: str = "In Discord"


class IdsDiagram(msgspec.Struct, frozen=True, tag_field="kind", tag="ids"):
    id: str
    example: str
    view: str
    before: dict[str, object]
    after: dict[str, object] | None = None
    label: str = "What the buttons hold"
    title: str = ""


class LivePreview(msgspec.Struct, frozen=True, tag_field="kind", tag="live"):
    id: str
    example: str
    view: str
    section: str
    field: str
    note: str = ""


type StaticPreview = ViewPreview | ReplyPreview | ThinkingPreview | ErrorPreview | LogPreview | ClickPath | IdsDiagram
type Preview = StaticPreview | LivePreview


class _Manifest(msgspec.Struct, frozen=True):
    preview: collections.abc.Sequence[Preview] = ()


def load_manifest(path: pathlib.Path) -> collections.abc.Sequence[Preview]:
    """Read the previews of a manifest, checking ids and examples.

    Parameters
    ----------
    path
        The TOML file. Examples are file stems next to it.

    Returns
    -------
    :
        The previews in file order.

    Raises
    ------
    ManifestError
        If the file is not valid, an id repeats, or an example has no file.
    """
    try:
        previews = msgspec.toml.decode(path.read_bytes(), type=_Manifest).preview
    except OSError as error:
        raise ManifestError(path, f"it cannot be read: {error}") from error
    except (msgspec.ValidationError, msgspec.DecodeError) as error:
        raise ManifestError(path, str(error)) from error

    seen: set[str] = set()
    for preview in previews:
        if _ID.fullmatch(preview.id) is None:
            raise ManifestError(path, f"the preview id {preview.id!r} must be lowercase words joined by single hyphens")
        if preview.id in seen:
            raise ManifestError(path, f"the preview id {preview.id!r} is used twice")
        seen.add(preview.id)
        if (
            not isinstance(preview, ReplyPreview | ThinkingPreview)
            and not (path.parent / f"{preview.example}.py").is_file()
        ):
            raise ManifestError(
                path, f"preview {preview.id!r} names the example {preview.example!r}, which has no file"
            )
    return previews
