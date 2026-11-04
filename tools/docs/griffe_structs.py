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
"""A griffe extension that gives msgspec Structs the constructor msgspec generates for them.

msgspec writes a Struct's ``__init__`` at runtime, so griffe, which only reads the source, finds
no signature to show. It affects the documentation only.
"""

from __future__ import annotations

import typing

import griffe

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("StructsExtension",)

_STRUCT: typing.Final[str] = "msgspec.Struct"
_FIELD: typing.Final[str] = "msgspec.field"
_FACTORY_DEFAULT: typing.Final[str] = "..."


class StructsExtension(griffe.Extension):
    @typing.override
    def on_package(self, *, pkg: griffe.Module, **kwargs: object) -> None:
        _visit(pkg)


def _visit(parent: griffe.Module | griffe.Class) -> None:
    if isinstance(parent, griffe.Class):
        _add_init(parent)
    for member in parent.members.values():
        if isinstance(member, griffe.Module | griffe.Class):
            _visit(member)


def _add_init(cls: griffe.Class) -> None:
    if "__init__" in cls.members or not _is_struct(cls):
        return
    fields = _fields(cls)
    if fields:
        init = griffe.Function(
            "__init__",
            lineno=0,
            endlineno=0,
            parent=cls,
            parameters=griffe.Parameters(
                griffe.Parameter("self", kind=griffe.ParameterKind.positional_or_keyword),
                *fields,
            ),
            returns="None",
        )
        cls.set_member("__init__", init)


def _is_struct(cls: griffe.Class) -> bool:
    return any(
        isinstance(base, griffe.Expr) and base.canonical_path == _STRUCT
        for ancestor in (cls, *cls.mro())
        for base in ancestor.bases
    )


def _fields(cls: griffe.Class) -> collections.abc.Sequence[griffe.Parameter]:
    positional: dict[str, griffe.Parameter] = {}
    keyword_only: dict[str, griffe.Parameter] = {}
    for ancestor in (*reversed(cls.mro()), cls):
        if not _is_struct(ancestor):
            continue
        # msgspec applies kw_only to the fields of the class that sets it, not to its subclasses.
        is_keyword_only = str(ancestor.keywords.get("kw_only")) == "True"
        kept, other = (keyword_only, positional) if is_keyword_only else (positional, keyword_only)
        kind = griffe.ParameterKind.keyword_only if is_keyword_only else griffe.ParameterKind.positional_or_keyword
        for member in ancestor.members.values():
            if isinstance(member, griffe.Attribute) and _is_field(member):
                other.pop(member.name, None)
                kept[member.name] = griffe.Parameter(
                    member.name,
                    annotation=member.annotation,
                    kind=kind,
                    default=_default(member),
                )
    return [*positional.values(), *keyword_only.values()]


def _is_field(attribute: griffe.Attribute) -> bool:
    # griffe unwraps ClassVar from the annotation and marks it by leaving this label off.
    return (
        attribute.annotation is not None
        and "property" not in attribute.labels
        and "instance-attribute" in attribute.labels
    )


def _default(attribute: griffe.Attribute) -> str | griffe.Expr | None:
    value = attribute.value
    if not (isinstance(value, griffe.ExprCall) and value.canonical_path == _FIELD):
        return value
    arguments = {
        argument.name: argument.value for argument in value.arguments if isinstance(argument, griffe.ExprKeyword)
    }
    return _FACTORY_DEFAULT if "default_factory" in arguments else arguments.get("default")
