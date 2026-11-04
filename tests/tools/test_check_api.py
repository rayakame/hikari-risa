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
"""Checks the API reference check against tiny fake packages."""

from __future__ import annotations

import json
import pathlib
import textwrap
import typing

from tools.docs import check_api

if typing.TYPE_CHECKING:
    import collections.abc

_STRUCTS: typing.Final[pathlib.Path] = pathlib.Path(check_api.__file__).with_name("griffe_structs.py")

_FILES: typing.Final[collections.abc.Mapping[str, str]] = {
    "risa/__init__.py": """
        '''The root.'''

        from __future__ import annotations

        from risa import extra as extra
        from risa._about import __version__ as __version__
        from risa.core import *
        from risa.shapes import Circle as Circle
    """,
    "risa/kit/__init__.py": """
        '''A subpackage, whose names come from its modules.'''

        from __future__ import annotations

        from risa.kit.parts import *
    """,
    "risa/kit/parts.py": """
        '''The parts.'''

        from __future__ import annotations

        __all__ = ("part",)


        def part() -> None:
            '''Make a part.'''
    """,
    "risa/_about.py": """
        '''Private.'''

        __version__ = "1"
    """,
    "risa/core.py": """
        '''The core.'''

        from __future__ import annotations

        import enum
        import typing

        __all__ = ("LIMIT", "Mode", "Thing", "make")

        LIMIT: typing.Final[int] = 3
        '''The limit.'''


        class Mode(enum.StrEnum):
            '''How it runs.

            Attributes
            ----------
            FAST
                Quickly.
            '''

            FAST = enum.auto()


        class Thing:
            '''A thing.

            Attributes
            ----------
            size
                How big it is.
            '''

            kind: typing.ClassVar[str] = "thing"
            '''What it is.'''

            def __init__(self, size: int) -> None:
                self.size: int = size

            def __repr__(self):
                return "Thing"

            @property
            def doubled(self) -> int:
                '''Twice the size.'''
                return self.size * 2

            @classmethod
            def of(cls, size: int) -> Thing:
                '''Make one.

                Parameters
                ----------
                size
                    How big it is.

                Returns
                -------
                :
                    The thing.
                '''
                return cls(size)

            def _hidden(self, size):
                return size


        def make(size: int) -> Thing:
            '''Make a thing.

            Parameters
            ----------
            size
                How big it is.
            '''
            return Thing(size)


        def _hidden(size):
            return size
    """,
    "risa/shapes.py": """
        '''The shapes, of which the package exports fewer than the module does.'''

        from __future__ import annotations

        __all__ = ("Circle", "plan")


        class Circle:
            '''A circle.'''


        def plan(circle):
            return circle
    """,
    "risa/extra.py": """
        '''A module its package does not re-export.'''

        from __future__ import annotations

        __all__ = ("register",)


        def register(name: str) -> None:
            '''Register a name.

            Parameters
            ----------
            name
                The name.
            '''
    """,
    "risa/internal/__init__.py": "'''Not part of the API.'''",
    "risa/internal/wire.py": """
        '''Not part of the API.'''

        __all__ = ("wire",)


        def wire(value):
            return value
    """,
    "docs/api/index.md": """
        # API

        ::: risa
            options:
              members: false
    """,
    "docs/api/kit/index.md": """
        # risa.kit

        ::: risa.kit
            options:
              members: false
    """,
    "docs/api/kit/parts.md": """
        # risa.kit.parts

        ::: risa.kit.parts
    """,
    "docs/api/core.md": """
        # risa.core

        ::: risa.core
    """,
    "docs/api/shapes.md": """
        # risa.shapes

        ::: risa.shapes
            options:
              show_root_heading: false
              members:
                - Circle
    """,
    "docs/api/extra.md": """
        # risa.extra

        ::: risa.extra
    """,
}


def _extra_with(body: str, *, names: str = "register") -> str:
    code = textwrap.dedent(body)
    return f"""'''The extra module.'''

from __future__ import annotations

import typing

__all__ = ("{names}",)

{code}
"""


def _problems(
    root: pathlib.Path,
    changes: collections.abc.Mapping[str, str | None],
    *,
    not_in_nav: collections.abc.Collection[str] = (),
    not_linked: collections.abc.Collection[str] = (),
    extensions: collections.abc.Sequence[str] = ("griffe_inherited_docstrings",),
) -> collections.abc.Sequence[str]:
    files = {name: textwrap.dedent(text) for name, text in {**_FILES, **changes}.items() if text is not None}
    pages = sorted(name for name in files if name.startswith("docs/api/"))
    nav = [name.removeprefix("docs/") for name in pages if name not in not_in_nav]
    links = [
        f"- [page]({name.removeprefix('docs/api/')})\n"
        for name in pages
        if name != "docs/api/index.md" and name not in not_linked
    ]
    files["docs/api/index.md"] += "\n" + "".join(links)
    files["zensical.toml"] = (
        f'[project]\nnav = [{{ "API" = {json.dumps(nav)} }}]\n\n'
        f"[project.plugins.mkdocstrings.handlers.python.options]\nextensions = {json.dumps(extensions)}\n"
    )
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return check_api.check_api(root)


def test_a_fully_typed_package_has_no_problems(tmp_path: pathlib.Path) -> None:
    assert _problems(tmp_path, {}) == []


def test_a_public_name_without_a_docstring_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str) -> None: ...
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register: no docstring, so its page leaves it out"
    ]


def test_an_unannotated_parameter_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name) -> None:
            '''Register a name.

            Parameters
            ----------
            name
                The name.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: parameter name has no annotation"]


def test_a_missing_return_annotation_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str):
            '''Register a name.

            Parameters
            ----------
            name
                The name.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: no return annotation"]


def test_an_unannotated_init_parameter_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.

            Parameters
            ----------
            name
                The name.
            '''

            def __init__(self, name):
                self._name: str = name
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register.__init__: parameter name has no annotation",
    ]


def test_an_unannotated_method_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.'''

            def run(self, name):
                '''Run.

                Parameters
                ----------
                name
                    The name.
                '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register.run: no return annotation",
        "risa.extra.register.run: parameter name has no annotation",
    ]


def test_an_unannotated_overload_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        @typing.overload
        def register(name) -> None: ...

        @typing.overload
        def register(name: int) -> None: ...

        def register(name: int | str) -> None:
            '''Register a name.

            Parameters
            ----------
            name
                The name.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: parameter name has no annotation"]


def test_an_unannotated_attribute_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        register = 3
        '''A number.'''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: no annotation"]


def test_an_unannotated_instance_attribute_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.

            Attributes
            ----------
            name
                The name.
            '''

            def __init__(self, name: str) -> None:
                self.name = name
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register.name: no annotation"]


def test_an_attribute_named_only_in_a_docstring_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.

            Attributes
            ----------
            name
                The name.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register: the docstring names attribute name, which does not exist",
    ]


def test_an_enum_attributes_entry_with_no_member_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        import enum

        class register(enum.Enum):
            '''Register a name.

            Attributes
            ----------
            NAME
                A name.
            GONE
                No longer here.
            '''

            NAME = 1
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register: the docstring names attribute GONE, which does not exist",
    ]


def test_an_unannotated_property_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.'''

            @property
            def name(self):
                '''The name.'''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register.name: no annotation"]


def test_a_bare_final_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        register: typing.Final = 3
        '''A number.'''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: a bare Final names no type"]


def test_a_subscripted_final_is_fine(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        register: typing.Final[int] = 3
        '''A number.'''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_an_enum_member_needs_no_annotation(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        import enum

        class register(enum.Enum):
            '''Register a name.

            Attributes
            ----------
            NAME
                A name.
            '''

            NAME = 1
            '''A name.'''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_an_inherited_attribute_named_in_a_docstring_is_typed(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class Base:
            name: str = "name"


        class register(Base):
            '''Register a name.

            Attributes
            ----------
            name
                The name.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_a_type_alias_needs_no_annotation(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        type register = int | str
        '''A name.'''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_a_return_type_in_a_docstring_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str) -> int:
            '''Register a name.

            Parameters
            ----------
            name
                The name.

            Returns
            -------
            int
                The number.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: the docstring types its return"]


def test_a_parameter_type_in_a_docstring_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str) -> None:
            '''Register a name.

            Parameters
            ----------
            name : str
                The name.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: the docstring types parameter name"]


def test_an_attribute_type_in_a_docstring_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.

            Attributes
            ----------
            name : str
                The name.
            '''

            name: str = "name"
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: the docstring types attribute name"]


def test_a_parameters_entry_naming_nothing_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str) -> None:
            '''Register a name.

            Parameters
            ----------
            name
                The name.
            nickname
                Gone from the signature.
            '''
    """)

    problems = _problems(tmp_path, {"risa/extra.py": extra})

    assert len(problems) == 1
    assert "Parameter 'nickname' does not appear in the function signature" in problems[0]


def test_a_class_parameters_entry_naming_nothing_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.

            Parameters
            ----------
            name
                The name.
            nickname
                Gone from the signature.
            '''

            def __init__(self, name: str) -> None:
                self._name = name
    """)

    problems = _problems(tmp_path, {"risa/extra.py": extra})

    assert len(problems) == 1
    assert "Parameter 'nickname' does not appear in the function signature" in problems[0]


def test_a_typed_yields_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str) -> typing.Iterator[int]:
            '''Register a name.

            Parameters
            ----------
            name
                The name.

            Yields
            ------
            int
                A number.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: the docstring types what it yields"]


def test_an_untyped_yields_is_fine(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str) -> typing.Iterator[int]:
            '''Register a name.

            Parameters
            ----------
            name
                The name.

            Yields
            ------
            :
                A number.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_a_method_without_a_docstring_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.'''

            def run(self) -> None: ...
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register.run: no docstring and no entry in its class's Attributes, so its page leaves it out",
    ]


def test_a_property_without_a_docstring_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.'''

            @property
            def name(self) -> str:
                return ""
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register.name: no docstring and no entry in its class's Attributes, so its page leaves it out",
    ]


def test_an_attribute_without_a_docstring_or_an_entry_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.'''

            name: str = ""
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register.name: no docstring and no entry in its class's Attributes, so its page leaves it out",
    ]


def test_an_enum_member_without_a_docstring_or_an_entry_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        import enum

        class register(enum.Enum):
            '''Register a name.'''

            NAME = 1
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register.NAME: no docstring and no entry in its class's Attributes, so its page leaves it out",
    ]


def test_a_method_that_inherits_its_docstring_is_kept(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class Base:
            def run(self) -> None:
                '''Run.'''

        class register(Base):
            '''Register a name.'''

            def run(self) -> None: ...
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_an_inherited_returns_that_contradicts_the_signature_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class Base:
            def run(self) -> int:
                '''Run.

                Returns
                -------
                :
                    A number.
                '''
                return 1

        class register(Base):
            '''Register a name.'''

            def run(self) -> bool:
                return True
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register.run: inherits a Returns of int, but returns bool",
    ]


def test_an_inherited_returns_that_agrees_with_the_signature_is_fine(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class Base:
            def run(self) -> int | None:
                '''Run.

                Returns
                -------
                :
                    A number.
                '''
                return 1

        class register(Base):
            '''Register a name.'''

            def run(self) -> int | None:
                return 2
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_the_extensions_of_the_config_are_loaded(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str) -> None: ...
    """)
    extension = """
        import griffe

        class Documenting(griffe.Extension):
            def on_package(self, *, pkg, **kwargs):
                function = pkg["extra"]["register"]
                text = "Added.\\n\\nParameters\\n----------\\nname\\n    The name.\\n"
                function.docstring = griffe.Docstring(text, parent=function)
    """

    problems = _problems(
        tmp_path,
        {"risa/extra.py": extra, "documenting.py": extension},
        extensions=["griffe_inherited_docstrings", "documenting.py"],
    )

    assert problems == []


def test_a_public_name_on_no_page_is_reported(tmp_path: pathlib.Path) -> None:
    assert _problems(tmp_path, {"docs/api/extra.md": "# risa.extra"}) == ["risa.extra.register: on no API page"]


def test_a_name_on_two_pages_is_reported(tmp_path: pathlib.Path) -> None:
    problems = _problems(tmp_path, {"docs/api/again.md": "::: risa.extra\n"})

    assert problems == ["risa.extra.register: on more than one API page: docs/api/again.md, docs/api/extra.md"]


def test_a_package_page_that_repeats_its_members_is_reported(tmp_path: pathlib.Path) -> None:
    problems = _problems(tmp_path, {"docs/api/index.md": "::: risa\n"})

    assert "risa.core.make: on more than one API page: docs/api/core.md, docs/api/index.md" in problems


def test_a_page_naming_an_internal_module_is_reported(tmp_path: pathlib.Path) -> None:
    problems = _problems(tmp_path, {"docs/api/wire.md": "::: risa.internal.wire\n"})

    assert problems == ["docs/api/wire.md: names risa.internal.wire, which is not a public module"]


def test_a_page_naming_a_missing_module_is_reported(tmp_path: pathlib.Path) -> None:
    problems = _problems(tmp_path, {"docs/api/gone.md": "::: risa.gone\n"})

    assert problems == ["docs/api/gone.md: names risa.gone, which is not a public module"]


def test_a_name_the_package_does_not_export_is_reported_on_the_page(tmp_path: pathlib.Path) -> None:
    problems = _problems(tmp_path, {"docs/api/shapes.md": "::: risa.shapes\n"})

    assert problems == ["docs/api/shapes.md: shows risa.shapes.plan, which is not public"]


def test_a_name_listed_on_the_page_must_be_public(tmp_path: pathlib.Path) -> None:
    page = """
        ::: risa.shapes
            options:
              members:
                - Circle
                - plan
    """

    assert _problems(tmp_path, {"docs/api/shapes.md": page}) == [
        "docs/api/shapes.md: shows risa.shapes.plan, which is not public",
    ]


def test_a_listed_name_that_does_not_exist_is_reported(tmp_path: pathlib.Path) -> None:
    page = """
        ::: risa.shapes
            options:
              members:
                - Circle
                - Square
    """

    assert _problems(tmp_path, {"docs/api/shapes.md": page}) == [
        "docs/api/shapes.md: shows risa.shapes.Square, which is not public",
    ]


def test_a_page_without_a_final_newline_is_read(tmp_path: pathlib.Path) -> None:
    assert _problems(tmp_path, {"docs/api/extra.md": "::: risa.extra"}) == []


def test_an_inline_member_list_is_read(tmp_path: pathlib.Path) -> None:
    page = """
        ::: risa.shapes
            options:
              members: [Circle]
    """

    assert _problems(tmp_path, {"docs/api/shapes.md": page}) == []


def test_a_name_in_an_inline_member_list_must_be_public(tmp_path: pathlib.Path) -> None:
    page = """
        ::: risa.shapes
            options:
              members: [Circle, plan]
    """

    assert _problems(tmp_path, {"docs/api/shapes.md": page}) == [
        "docs/api/shapes.md: shows risa.shapes.plan, which is not public",
    ]


def test_an_empty_inline_member_list_shows_nothing(tmp_path: pathlib.Path) -> None:
    page = """
        ::: risa.shapes
            options:
              members: []
    """

    assert _problems(tmp_path, {"docs/api/shapes.md": page}) == ["risa.shapes.Circle: on no API page"]


def test_a_module_with_no_page_and_no_re_export_is_reported(tmp_path: pathlib.Path) -> None:
    orphan = """
        '''A module nothing re-exports and no page shows.'''

        from __future__ import annotations

        __all__ = ("thing",)


        def thing() -> None:
            '''A thing.'''
    """

    assert _problems(tmp_path, {"risa/orphan.py": orphan}) == ["risa.orphan.thing: on no API page"]


def test_a_page_not_in_the_nav_is_reported(tmp_path: pathlib.Path) -> None:
    assert _problems(tmp_path, {}, not_in_nav=["docs/api/extra.md"]) == [
        "docs/api/extra.md: not in the nav of zensical.toml",
    ]


def test_a_page_not_linked_from_the_index_is_reported(tmp_path: pathlib.Path) -> None:
    assert _problems(tmp_path, {}, not_linked=["docs/api/extra.md"]) == [
        "docs/api/extra.md: not linked from docs/api/index.md",
    ]


def test_a_public_package_without_an_index_page_is_reported(tmp_path: pathlib.Path) -> None:
    assert _problems(tmp_path, {"docs/api/kit/index.md": None}) == [
        "risa.kit: a public package needs the index page docs/api/kit/index.md",
    ]


def test_an_index_page_that_names_something_else_is_reported(tmp_path: pathlib.Path) -> None:
    assert _problems(tmp_path, {"docs/api/kit/index.md": "# risa.kit\n"}) == [
        "risa.kit: a public package needs the index page docs/api/kit/index.md",
    ]


def test_a_package_that_defines_names_without_all_is_reported(tmp_path: pathlib.Path) -> None:
    kit = """
        '''A subpackage that defines a name itself.'''

        from __future__ import annotations

        from risa.kit.parts import *


        def helper() -> None:
            '''Help.'''
    """

    assert _problems(tmp_path, {"risa/kit/__init__.py": kit}) == ["risa.kit: a public module needs an __all__"]


def test_a_package_with_only_private_definitions_needs_no_all(tmp_path: pathlib.Path) -> None:
    kit = """
        '''A subpackage with a private helper.'''

        from __future__ import annotations

        from risa.kit.parts import *


        def _helper() -> None: ...
    """

    assert _problems(tmp_path, {"risa/kit/__init__.py": kit}) == []


def test_a_name_exported_from_another_module_is_no_re_export(tmp_path: pathlib.Path) -> None:
    root = """
        '''The root.'''

        from __future__ import annotations

        from risa import extra as extra
        from risa.core import *
        from risa.other import *
        from risa.shapes import Circle as Circle
    """
    other = """
        '''Another module.'''

        from __future__ import annotations

        __all__ = ("register",)


        def register() -> None:
            '''Register nothing.'''
    """

    problems = _problems(
        tmp_path,
        {"risa/__init__.py": root, "risa/other.py": other, "docs/api/other.md": "::: risa.other\n"},
    )

    assert problems == []


def test_a_public_module_without_all_is_reported(tmp_path: pathlib.Path) -> None:
    problems = _problems(tmp_path, {"risa/loose.py": "'''A module with no __all__.'''"})

    assert problems == ["risa.loose: a public module needs an __all__"]


def test_a_name_in_all_that_is_not_defined_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("", names="gone")

    assert "risa.extra.gone: in __all__, but not defined" in _problems(tmp_path, {"risa/extra.py": extra})


def test_a_name_from_outside_the_package_is_covered_by_a_heading(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("from outside import register as register")
    page = """
        # risa.extra

        ::: risa.extra

        ## register { #risa.extra.register }

        Comes from elsewhere.
    """

    assert _problems(tmp_path, {"risa/extra.py": extra, "docs/api/extra.md": page}) == []


def test_a_name_from_outside_the_package_needs_a_heading(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("from outside import register as register")

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: on no API page"]


def test_a_name_from_outside_the_package_re_exported_again_is_still_from_outside(tmp_path: pathlib.Path) -> None:
    root = """
        '''The root.'''

        from __future__ import annotations

        from risa.core import *
        from risa.extra import *
        from risa.shapes import Circle as Circle
    """
    extra = _extra_with("from outside import register as register")

    problems = _problems(
        tmp_path, {"risa/__init__.py": root, "risa/extra.py": extra, "docs/api/index.md": "::: risa\n"}
    )

    assert "risa.extra.register: on no API page" in problems


def test_a_heading_covers_only_a_name_from_outside_the_package(tmp_path: pathlib.Path) -> None:
    page = """
        ## register { #risa.extra.register }

        A hand-written section.
    """

    assert _problems(tmp_path, {"docs/api/extra.md": page}) == ["risa.extra.register: on no API page"]


def test_a_public_parameter_without_an_entry_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str, *, loud: bool = False) -> None:
            '''Register a name.

            Parameters
            ----------
            name
                The name.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: parameter loud has no entry"]


def test_a_star_parameter_entry_counts(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(*names: str, **options: int) -> None:
            '''Register names.

            Parameters
            ----------
            *names
                The names.
            **options
                How to register them.
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_an_inherited_docstring_documents_its_parameters(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class Base:
            def run(self, name: str) -> None:
                '''Run.

                Parameters
                ----------
                name
                    The name.
                '''

        class register(Base):
            '''Register a name.'''

            def run(self, name: str) -> None: ...
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == []


def test_a_struct_field_in_attributes_counts(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        import msgspec

        class register(msgspec.Struct):
            '''Register a name.

            Attributes
            ----------
            name
                The name.
            '''

            name: str
            size: int = 0
    """)

    problems = _problems(
        tmp_path,
        {"risa/extra.py": extra, "griffe_structs.py": _STRUCTS.read_text(encoding="utf-8")},
        extensions=["griffe_inherited_docstrings", "griffe_structs.py"],
    )

    assert "risa.extra.register.__init__: parameter name has no entry" not in problems
    assert "risa.extra.register.__init__: parameter size has no entry" in problems


def test_an_exempt_constructor_is_not_reported(tmp_path: pathlib.Path) -> None:
    client = """
        '''The clients.'''

        from __future__ import annotations

        __all__ = ("GatewayEnabledClient",)


        class GatewayEnabledClient:
            '''A client, made by risa.'''

            def __init__(self, app: int) -> None:
                self._app: int = app
    """

    assert _problems(tmp_path, {"risa/client.py": client, "docs/api/client.md": "::: risa.client\n"}) == []


def test_only_the_bound_first_parameter_of_a_method_is_exempt(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        class register:
            '''Register a name.'''

            def add(self, cls: type) -> None:
                '''Add.'''

            @staticmethod
            def make(self: int) -> None:
                '''Make.'''

            @classmethod
            def of(cls) -> None:
                '''Of.'''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == [
        "risa.extra.register.add: parameter cls has no entry",
        "risa.extra.register.make: parameter self has no entry",
    ]


def test_an_entry_without_a_description_is_reported(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        def register(name: str) -> None:
            '''Register a name.

            Parameters
            ----------
            name
            '''
    """)

    assert _problems(tmp_path, {"risa/extra.py": extra}) == ["risa.extra.register: parameter name has an empty entry"]


def test_a_struct_field_in_the_attributes_of_an_ancestor_counts(tmp_path: pathlib.Path) -> None:
    extra = _extra_with("""
        import msgspec

        class Base(msgspec.Struct):
            '''A base.

            Attributes
            ----------
            name
                The name.
            '''

            name: str

        class register(Base):
            '''Register a name.'''
    """)

    problems = _problems(
        tmp_path,
        {"risa/extra.py": extra, "griffe_structs.py": _STRUCTS.read_text(encoding="utf-8")},
        extensions=["griffe_inherited_docstrings", "griffe_structs.py"],
    )

    assert "risa.extra.register.__init__: parameter name has no entry" not in problems


def test_the_exemption_covers_one_constructor_not_its_module(tmp_path: pathlib.Path) -> None:
    client = """
        '''The clients.'''

        from __future__ import annotations

        __all__ = ("GatewayEnabledClient", "OtherClient")


        class GatewayEnabledClient:
            '''A client, made by risa.'''

            def __init__(self, app: int) -> None:
                self._app: int = app


        class OtherClient:
            '''Another client.'''

            def __init__(self, app: int) -> None:
                self._app: int = app
    """

    assert _problems(tmp_path, {"risa/client.py": client, "docs/api/client.md": "::: risa.client\n"}) == [
        "risa.client.OtherClient.__init__: parameter app has no entry",
    ]


def test_an_inherited_returns_naming_the_same_type_through_a_module_is_fine(tmp_path: pathlib.Path) -> None:
    base = """
        '''The base.'''

        from __future__ import annotations

        __all__ = ("Base", "Entry")


        class Entry:
            '''An entry.'''


        class Base:
            '''A base.'''

            def run(self) -> Entry | None:
                '''Run.

                Returns
                -------
                :
                    An entry.
                '''
    """
    extra = _extra_with("""
        from risa import base

        class register(base.Base):
            '''Register a name.'''

            def run(self) -> base.Entry | None:
                return None
    """)

    problems = _problems(
        tmp_path, {"risa/base.py": base, "docs/api/base.md": "::: risa.base\n", "risa/extra.py": extra}
    )

    assert problems == []
