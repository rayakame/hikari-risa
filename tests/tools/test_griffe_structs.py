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
"""Checks the Struct extension against small modules, and against what msgspec itself generates."""

from __future__ import annotations

import inspect
import runpy
import textwrap
import typing

import griffe
import pytest

from tools.docs import griffe_structs

if typing.TYPE_CHECKING:
    import collections.abc
    import pathlib

_HEAD: typing.Final[str] = "import typing\n\nimport msgspec\n\n"


def _write(root: pathlib.Path, source: str) -> pathlib.Path:
    path = root / "pkg" / "__init__.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(_HEAD + textwrap.dedent(source), encoding="utf-8")
    return path


def _load(root: pathlib.Path, source: str) -> griffe.Module:
    _write(root, source)
    package = griffe.load(
        "pkg",
        search_paths=[root],
        try_relative_path=False,
        allow_inspection=False,
        extensions=griffe.load_extensions(griffe_structs.StructsExtension),
    )
    assert isinstance(package, griffe.Module)
    return package


def _class(root: pathlib.Path, source: str, name: str) -> griffe.Class:
    member = _load(root, source).members[name]
    assert isinstance(member, griffe.Class)
    return member


def _signature(cls: griffe.Class) -> collections.abc.Sequence[tuple[str, str | None, str | None, str]]:
    init = cls.members["__init__"]
    assert isinstance(init, griffe.Function)
    assert str(init.returns) == "None"
    return [
        (
            parameter.name,
            None if parameter.annotation is None else str(parameter.annotation),
            None if parameter.default is None else str(parameter.default),
            "*" if parameter.kind is griffe.ParameterKind.keyword_only else "",
        )
        for parameter in init.parameters
        if parameter.name != "self"
    ]


def test_a_struct_gets_an_init_with_its_fields_in_order(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Thing(msgspec.Struct, frozen=True):
            size: int
            name: str = "thing"
            note: str | None = None
        """,
        "Thing",
    )

    assert _signature(cls) == [
        ("size", "int", None, ""),
        ("name", "str", "'thing'", ""),
        ("note", "str | None", "None", ""),
    ]


def test_the_init_starts_with_self(tmp_path: pathlib.Path) -> None:
    cls = _class(tmp_path, "class Thing(msgspec.Struct):\n    size: int\n", "Thing")
    init = cls.members["__init__"]

    assert isinstance(init, griffe.Function)
    assert next(iter(init.parameters)).name == "self"


def test_a_struct_imported_by_name_counts(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        from msgspec import Struct

        class Thing(Struct):
            size: int
        """,
        "Thing",
    )

    assert _signature(cls) == [("size", "int", None, "")]


def test_base_class_fields_come_first(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Base(msgspec.Struct):
            size: int

        class Thing(Base):
            name: str = "thing"
        """,
        "Thing",
    )

    assert _signature(cls) == [("size", "int", None, ""), ("name", "str", "'thing'", "")]


def test_a_redefined_field_keeps_its_place_and_takes_the_new_default(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Base(msgspec.Struct):
            size: int
            name: str = "base"

        class Thing(Base):
            size: int = 5
            note: str = ""
        """,
        "Thing",
    )

    assert _signature(cls) == [("size", "int", "5", ""), ("name", "str", "'base'", ""), ("note", "str", "''", "")]


def test_a_class_var_is_left_out(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Thing(msgspec.Struct):
            kind: typing.ClassVar[str] = "thing"
            bare: typing.ClassVar[int]
            size: int = 1
        """,
        "Thing",
    )

    assert _signature(cls) == [("size", "int", "1", "")]


def test_what_is_not_a_field_is_left_out(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Thing(msgspec.Struct):
            size: int
            LIMIT = 3

            @property
            def doubled(self) -> int:
                return self.size * 2

            def grow(self) -> None: ...
        """,
        "Thing",
    )

    assert _signature(cls) == [("size", "int", None, "")]


def test_kw_only_makes_the_fields_keyword_only(tmp_path: pathlib.Path) -> None:
    cls = _class(tmp_path, "class Thing(msgspec.Struct, kw_only=True):\n    size: int\n", "Thing")

    assert _signature(cls) == [("size", "int", None, "*")]


def test_kw_only_on_a_base_applies_to_the_fields_of_the_base(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Base(msgspec.Struct, kw_only=True):
            size: int

        class Thing(Base):
            name: str
        """,
        "Thing",
    )

    assert _signature(cls) == [("name", "str", None, ""), ("size", "int", None, "*")]


def test_a_field_default_is_shown_as_that_default(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Thing(msgspec.Struct):
            size: int = msgspec.field(default=3)
            note: str | None = msgspec.field(default=None)
        """,
        "Thing",
    )

    assert _signature(cls) == [("size", "int", "3", ""), ("note", "str | None", "None", "")]


def test_a_default_factory_is_shown_as_an_ellipsis(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Thing(msgspec.Struct):
            items: list[int] = msgspec.field(default_factory=list)
        """,
        "Thing",
    )

    assert _signature(cls) == [("items", "list[int]", "...", "")]


def test_a_field_without_a_default_stays_required(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Thing(msgspec.Struct):
            size: int = msgspec.field(name="SIZE")
        """,
        "Thing",
    )

    assert _signature(cls) == [("size", "int", None, "")]


def test_a_struct_with_no_fields_gets_no_init(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Thing(msgspec.Struct):
            def grow(self) -> None: ...
        """,
        "Thing",
    )

    assert "__init__" not in cls.members


def test_a_plain_class_is_left_alone(tmp_path: pathlib.Path) -> None:
    cls = _class(tmp_path, "class Thing:\n    size: int = 1\n", "Thing")

    assert "__init__" not in cls.members


def test_fields_of_a_plain_mixin_are_left_out(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Mixin:
            __slots__ = ()
            ignored: int = 1

        class Thing(Mixin, msgspec.Struct):
            size: int
        """,
        "Thing",
    )

    assert _signature(cls) == [("size", "int", None, "")]


def test_a_struct_nested_in_a_class_gets_an_init(tmp_path: pathlib.Path) -> None:
    cls = _class(
        tmp_path,
        """
        class Outer:
            class Thing(msgspec.Struct):
                size: int
        """,
        "Outer",
    )
    nested = cls.members["Thing"]

    assert isinstance(nested, griffe.Class)
    assert _signature(nested) == [("size", "int", None, "")]


def test_a_struct_in_a_submodule_with_its_base_elsewhere_gets_an_init(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "from pkg.things import Thing as Thing\n")
    (tmp_path / "pkg" / "bases.py").write_text(
        "import msgspec\n\n\nclass Base(msgspec.Struct):\n    size: int\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "things.py").write_text(
        "from pkg import bases\n\n\nclass Thing(bases.Base):\n    name: str\n",
        encoding="utf-8",
    )
    package = griffe.load(
        "pkg",
        search_paths=[tmp_path],
        try_relative_path=False,
        allow_inspection=False,
        extensions=griffe.load_extensions(griffe_structs.StructsExtension),
    )
    assert isinstance(package, griffe.Module)
    module = package.members["things"]
    assert isinstance(module, griffe.Module)
    cls = module.members["Thing"]
    assert isinstance(cls, griffe.Class)

    assert _signature(cls) == [("size", "int", None, ""), ("name", "str", None, "")]


_SHAPES: typing.Final[collections.abc.Mapping[str, str]] = {
    "plain": """
        class Thing(msgspec.Struct):
            size: int
            name: str = ""
    """,
    "kw_only": """
        class Thing(msgspec.Struct, kw_only=True):
            name: str = ""
            size: int
    """,
    "base_then_own": """
        class Base(msgspec.Struct):
            size: int

        class Thing(Base):
            name: str = ""
    """,
    "kw_only_base_then_positional": """
        class Base(msgspec.Struct, kw_only=True):
            name: str = ""
            size: int

        class Thing(Base):
            other: int
            note: str = ""
    """,
    "positional_base_then_kw_only": """
        class Base(msgspec.Struct):
            size: int

        class Thing(Base, kw_only=True):
            name: str = ""
            other: int
    """,
    "kw_only_on_both": """
        class Base(msgspec.Struct, kw_only=True):
            size: int

        class Thing(Base, kw_only=True):
            name: str = ""
    """,
    "redefined": """
        class Base(msgspec.Struct):
            size: int
            name: str = ""

        class Thing(Base):
            size: int = 5
            note: str = ""
    """,
    "redefined_as_kw_only": """
        class Base(msgspec.Struct):
            size: int = 1
            name: str = ""

        class Thing(Base, kw_only=True):
            size: int = 5
            other: int
    """,
    "redefined_as_positional": """
        class Base(msgspec.Struct, kw_only=True):
            size: int
            name: str = ""

        class Thing(Base):
            size: int = 5
    """,
    "three_levels": """
        class Root(msgspec.Struct):
            size: int

        class Base(Root, kw_only=True):
            name: str = ""

        class Thing(Base):
            other: int = 1
    """,
    "class_var_and_field_helpers": """
        class Thing(msgspec.Struct):
            kind: typing.ClassVar[str] = "thing"
            _private: int = 0
            items: list[int] = msgspec.field(default_factory=list)
            note: str = msgspec.field(default="")
    """,
}


@pytest.mark.parametrize("source", _SHAPES.values(), ids=_SHAPES.keys())
def test_the_order_and_kinds_match_what_msgspec_generates(tmp_path: pathlib.Path, source: str) -> None:
    generated = runpy.run_path(str(_write(tmp_path, source)))["Thing"]
    expected = [
        (parameter.name, parameter.kind is inspect.Parameter.KEYWORD_ONLY)
        for parameter in inspect.signature(generated).parameters.values()
    ]

    cls = _class(tmp_path, source, "Thing")

    assert [(name, star == "*") for name, _, _, star in _signature(cls)] == expected
