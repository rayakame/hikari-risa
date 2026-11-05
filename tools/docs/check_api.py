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
"""Checks that the API reference shows the public API exactly once, and that all of it is typed in the code."""

from __future__ import annotations

import contextlib
import logging
import pathlib
import posixpath
import re
import sys
import tomllib
import typing

import griffe

if typing.TYPE_CHECKING:
    import collections.abc

__all__ = ("check_api",)

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.docs.check_api")

_PACKAGE: typing.Final[str] = "risa"
_CONFIG: typing.Final[str] = "zensical.toml"
_DOCS: typing.Final[str] = "docs"
_PAGES: typing.Final[str] = "docs/api"
_INDEX: typing.Final[str] = "docs/api/index.md"
_INTERNAL_MODULES: typing.Final[frozenset[str]] = frozenset(
    {
        "risa.dispatch",
        "risa.internal",
        "risa.state.anchor",
        "risa.state.backend",
        "risa.state.message",
        "risa.state.serde",
        "risa.state.stateless",
        "risa.state.stored",
    }
)
_ENUM_MODULE: typing.Final[str] = "enum."
# Nobody but risa calls these constructors, so their parameters need no entries.
_RISA_ONLY_CONSTRUCTORS: typing.Final[frozenset[str]] = frozenset(
    {
        "risa.binding.HandlerBinder.__init__",
        "risa.client.GatewayEnabledClient.__init__",
        "risa.client.RestEnabledClient.__init__",
        "risa.context.ComponentContext.__init__",
        "risa.context.Context.__init__",
        "risa.context.Response.__init__",
        "risa.state.schema.StateSchema.__init__",
        "risa.ui.build.BuildContext.__init__",
        "risa.ui.build.Rendered.__init__",
        "risa.view.HandlerMethod.__init__",
    }
)

_BLOCK: typing.Final[re.Pattern[str]] = re.compile(r"^::: ([\w.]+)[ \t]*\n((?:[ \t]+.*\n)*)", re.MULTILINE)
_NO_MEMBERS: typing.Final[re.Pattern[str]] = re.compile(r"^\s+members:\s*false\s*$", re.MULTILINE)
_MEMBER_LIST: typing.Final[re.Pattern[str]] = re.compile(r"^\s+members:\s*\n((?:\s+- \w+\s*\n)+)", re.MULTILINE)
_MEMBER_ITEM: typing.Final[re.Pattern[str]] = re.compile(r"- (\w+)")
_MEMBER_INLINE: typing.Final[re.Pattern[str]] = re.compile(r"^\s+members:\s*\[(.*)\]\s*$", re.MULTILINE)
_NAME: typing.Final[re.Pattern[str]] = re.compile(r"\w+")
_LINK: typing.Final[re.Pattern[str]] = re.compile(r"\]\(([^)#\s]+\.md)\)")
_SUMMARY: typing.Final[re.Pattern[str]] = re.compile(
    r"^- \[`(?P<module>[\w.]+)`\]\([^)]+\)(?:\{[^}]*\})?\n\n[ \t]+(?P<summary>.+)$",
    re.MULTILINE,
)
_TYPED_YIELDS: typing.Final[re.Pattern[str]] = re.compile(r"^Yields\n-+\n(?!:\s*$)\S", re.MULTILINE)

type _Member = griffe.Object | griffe.Alias
type _Nav = str | list[_Nav] | dict[str, _Nav]


class _Collector(logging.Handler):
    def __init__(self, messages: list[str]) -> None:
        super().__init__(logging.WARNING)
        self._messages = messages

    @typing.override
    def emit(self, record: logging.LogRecord) -> None:
        self._messages.append(record.getMessage())


def check_api(root: pathlib.Path) -> collections.abc.Sequence[str]:
    """Return every problem in the pages under ``docs/api`` and the annotations of the ``risa`` package in ``root``."""
    with (root / _CONFIG).open("rb") as file:
        project = tomllib.load(file)["project"]
    package = griffe.load(
        _PACKAGE,
        search_paths=[root],
        try_relative_path=False,
        allow_inspection=False,
        store_source=False,
        extensions=griffe.load_extensions(
            *_extensions(root, project["plugins"]["mkdocstrings"]["handlers"]["python"]["options"]["extensions"]),
        ),
    )
    if not isinstance(package, griffe.Module):
        return [f"{_PACKAGE}: not a module"]

    modules = {module.path: module for module in _public_modules(package)}
    pages = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((root / _PAGES).rglob("*.md"))
    }
    problems = [f"{module.path}: a public module needs an __all__" for module in modules.values() if _needs_all(module)]
    public: dict[str, _Member] = {}
    for module in modules.values():
        for name in _public_names(module):
            member = module.members.get(name)
            if member is None:
                problems.append(f"{module.path}.{name}: in __all__, but not defined")
            else:
                public[f"{module.path}.{name}"] = member

    problems.extend(_check_pages(pages, modules, public))
    problems.extend(_check_lists(pages, modules, project["nav"]))
    with _griffe_warnings() as warnings:
        for name, member in public.items():
            if _is_from_outside(member):
                continue
            target = _resolve(member)
            if not target.has_docstring:
                problems.append(f"{name}: no docstring, so its page leaves it out")
            problems.extend(_check_types(target))
    return list(dict.fromkeys([*problems, *warnings]))


def _extensions(root: pathlib.Path, names: collections.abc.Sequence[str]) -> collections.abc.Sequence[str]:
    # griffe looks for a file extension in the working directory, and the config means the project root.
    return [str(root / name) if (root / name).exists() else name for name in names]


def _public_modules(module: griffe.Module) -> collections.abc.Iterator[griffe.Module]:
    if module.name.startswith("_") or module.path in _INTERNAL_MODULES:
        return
    yield module
    for member in module.members.values():
        if isinstance(member, griffe.Module):
            yield from _public_modules(member)


def _needs_all(module: griffe.Module) -> bool:
    if module.exports is not None:
        return False
    if not module.is_init_module:
        return True
    return any(
        not name.startswith("_") and not isinstance(member, griffe.Alias | griffe.Module)
        for name, member in module.members.items()
    )


def _public_names(module: griffe.Module) -> collections.abc.Sequence[str]:
    names = _exports(module)
    if not isinstance(module.parent, griffe.Module):
        return names
    # A package that re-exports some of a module's names has chosen the public ones.
    return [name for name in names if _re_exports(module.parent, module, name)] or names


def _exports(module: griffe.Module) -> collections.abc.Sequence[str]:
    return [str(name) for name in module.exports or ()]


def _re_exports(package: griffe.Module, module: griffe.Module, name: str) -> bool:
    member = package.members.get(name)
    return isinstance(member, griffe.Alias) and member.target_path == f"{module.path}.{name}"


def _check_pages(
    pages: collections.abc.Mapping[str, str],
    modules: collections.abc.Mapping[str, griffe.Module],
    public: collections.abc.Mapping[str, _Member],
) -> collections.abc.Sequence[str]:
    problems: list[str] = []
    pages_of: dict[str, list[str]] = {name: [] for name in public}
    for page, text in pages.items():
        for block in _BLOCK.finditer(f"{text}\n"):
            module = modules.get(block[1])
            if module is None:
                problems.append(f"{page}: names {block[1]}, which is not a public module")
                continue
            for name in _shown(module, block[2], text):
                if name in pages_of:
                    pages_of[name].append(page)
                else:
                    problems.append(f"{page}: shows {name}, which is not public")
    for name, showing in pages_of.items():
        if not showing:
            problems.append(f"{name}: on no API page")
        elif len(showing) > 1:
            problems.append(f"{name}: on more than one API page: {', '.join(showing)}")
    return problems


def _shown(module: griffe.Module, options: str, page: str) -> collections.abc.Iterator[str]:
    for name in _listed(module, options):
        member = module.members.get(name)
        if member is None:
            yield f"{module.path}.{name}"
        elif not _is_from_outside(member):
            yield _resolve(member).path
        # griffe cannot document what lives outside the package, so its page carries a hand-written heading.
        elif re.search(rf"^#+ {re.escape(name)}\b", page, re.MULTILINE):
            yield f"{module.path}.{name}"


def _listed(module: griffe.Module, options: str) -> collections.abc.Sequence[str]:
    if _NO_MEMBERS.search(options):
        return []
    if listed := _MEMBER_LIST.search(options):
        return _MEMBER_ITEM.findall(listed[1])
    if inline := _MEMBER_INLINE.search(options):
        return _NAME.findall(inline[1])
    if module.exports is not None:
        return _exports(module)
    return [
        name for name, member in module.members.items() if member.is_public and not isinstance(member, griffe.Module)
    ]


def _check_lists(
    pages: collections.abc.Mapping[str, str],
    modules: collections.abc.Mapping[str, griffe.Module],
    nav: _Nav,
) -> collections.abc.Iterator[str]:
    in_nav = {f"{_DOCS}/{page}" for page in _nav_pages(nav)}
    linked = {posixpath.normpath(f"{_PAGES}/{target}") for target in _LINK.findall(pages.get(_INDEX, ""))}
    for page in pages:
        if page not in in_nav:
            yield f"{page}: not in the nav of {_CONFIG}"
        if page not in linked and page != _INDEX:
            yield f"{page}: not linked from {_INDEX}"
    for module in modules.values():
        if module.is_init_module:
            index = "/".join((_PAGES, *module.path.split(".")[1:], "index.md"))
            if module.path not in {block[1] for block in _BLOCK.finditer(f"{pages.get(index, '')}\n")}:
                yield f"{module.path}: a public package needs the index page {index}"
    for entry in _SUMMARY.finditer(pages.get(_INDEX, "")):
        module = modules.get(entry["module"])
        first_line = module.docstring.value.partition("\n")[0] if module and module.docstring else None
        if first_line not in {None, entry["summary"]}:
            yield f"{entry['module']}: {_INDEX} says {entry['summary']!r}, but its docstring starts with {first_line!r}"


def _nav_pages(entry: _Nav) -> collections.abc.Iterator[str]:
    if isinstance(entry, str):
        yield entry
    else:
        for child in entry.values() if isinstance(entry, dict) else entry:
            yield from _nav_pages(child)


def _is_from_outside(member: _Member) -> bool:
    while isinstance(member, griffe.Alias):
        if member.target_path.partition(".")[0] != _PACKAGE:
            return True
        member = member.target
    return False


def _resolve(member: _Member) -> griffe.Object:
    return member.final_target if isinstance(member, griffe.Alias) else member


@contextlib.contextmanager
def _griffe_warnings() -> collections.abc.Generator[collections.abc.Sequence[str]]:
    messages: list[str] = []
    handler = _Collector(messages)
    logger = logging.getLogger("griffe")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.propagate = True


def _check_types(member: griffe.Object) -> collections.abc.Iterator[str]:
    if isinstance(member, griffe.Function):
        yield from _check_function(member)
    elif isinstance(member, griffe.Attribute):
        yield from _check_attribute(member)
    elif isinstance(member, griffe.Class):
        yield from _check_class(member)
    yield from _check_docstring(member)


def _check_function(function: griffe.Function) -> collections.abc.Iterator[str]:
    for signature in (function, *(function.overloads or ())):
        if signature.returns is None and function.name != "__init__":
            yield f"{function.path}: no return annotation"
        for parameter in signature.parameters:
            if parameter.annotation is None and not _is_bound(signature, parameter):
                yield f"{function.path}: parameter {parameter.name} has no annotation"
    yield from _check_entries(function)


def _check_entries(function: griffe.Function) -> collections.abc.Iterator[str]:
    if function.path in _RISA_ONLY_CONSTRUCTORS or not (function.has_docstring or function.name == "__init__"):
        return
    documented = _entries(function.docstring, griffe.DocstringSectionParameters)
    if function.name == "__init__" and isinstance(function.parent, griffe.Class):
        for cls in (function.parent, *function.parent.mro()):
            for name, filled in _entries(
                cls.docstring, griffe.DocstringSectionParameters, griffe.DocstringSectionAttributes
            ).items():
                documented[name] = documented.get(name, False) or filled
    for parameter in function.parameters:
        if _is_bound(function, parameter):
            continue
        if parameter.name not in documented:
            yield f"{function.path}: parameter {parameter.name} has no entry"
        elif not documented[parameter.name]:
            yield f"{function.path}: parameter {parameter.name} has an empty entry"


def _is_bound(function: griffe.Function, parameter: griffe.Parameter) -> bool:
    # The first parameter of a method is the instance or the class, which no caller passes.
    return (
        isinstance(function.parent, griffe.Class)
        and "staticmethod" not in function.labels
        and parameter is function.parameters[0]
    )


def _entries(
    docstring: griffe.Docstring | None,
    *kinds: type[griffe.DocstringSectionParameters | griffe.DocstringSectionAttributes],
) -> dict[str, bool]:
    if docstring is None:
        return {}
    return {
        item.name.lstrip("*"): bool(item.description.strip())
        for section in docstring.parse("numpy", warnings=False)
        if isinstance(section, kinds)
        for item in section.value
    }


def _check_attribute(attribute: griffe.Attribute) -> collections.abc.Iterator[str]:
    if attribute.annotation is None:
        yield f"{attribute.path}: no annotation"
    elif str(attribute.annotation).rpartition(".")[2] == "Final":
        yield f"{attribute.path}: a bare Final names no type"


def _check_class(cls: griffe.Class) -> collections.abc.Iterator[str]:
    listed = _attributes_section_names(cls)
    for name, member in cls.members.items():
        if isinstance(member, griffe.Alias) or (name != "__init__" and name.startswith("_")):
            continue
        if name != "__init__" and not member.has_docstring and name not in listed:
            yield f"{member.path}: no docstring and no entry in its class's Attributes, so its page leaves it out"
        if not (_is_enum(cls) and isinstance(member, griffe.Attribute)):
            yield from _check_types(member)


def _attributes_section_names(cls: griffe.Class) -> collections.abc.Set[str]:
    if cls.docstring is None:
        return frozenset()
    return {
        item.name
        for section in cls.docstring.parse("numpy", warnings=False)
        if isinstance(section, griffe.DocstringSectionAttributes)
        for item in section.value
    }


def _is_enum(cls: griffe.Class) -> bool:
    # The typing spec forbids annotating an enum member: its type is the enum.
    return any(
        (base.canonical_path if isinstance(base, griffe.Expr) else base).startswith(_ENUM_MODULE) for base in cls.bases
    )


def _check_docstring(member: griffe.Object) -> collections.abc.Iterator[str]:
    docstring = member.docstring
    if docstring is None:
        return
    if _TYPED_YIELDS.search(docstring.value):
        yield f"{member.path}: the docstring types what it yields"
    # The annotation rules already report a parameter griffe would call untyped.
    for section in docstring.parse("numpy", warn_missing_types=False):
        if isinstance(section, griffe.DocstringSectionReturns) and isinstance(member, griffe.Function):
            yield from _check_returns(member, docstring, section)
        # griffe_inherited_docstrings shares the object, so the rest was checked where it was written.
        elif docstring.parent is member:
            if isinstance(section, griffe.DocstringSectionParameters) and isinstance(
                member, griffe.Function | griffe.Class
            ):
                yield from _typed_parameters(member, section)
            elif isinstance(section, griffe.DocstringSectionAttributes) and isinstance(member, griffe.Class):
                yield from _check_documented_attributes(member, section)


# griffe hands out the signature's own annotation for what a docstring leaves untyped, so identity tells them apart.
def _typed_parameters(
    member: griffe.Function | griffe.Class,
    section: griffe.DocstringSectionParameters,
) -> collections.abc.Iterator[str]:
    for item in section.value:
        declared = member.parameters[item.name].annotation if item.name in member.parameters else None
        if item.annotation is not None and item.annotation is not declared:
            yield f"{member.path}: the docstring types parameter {item.name}"


def _check_documented_attributes(
    cls: griffe.Class,
    section: griffe.DocstringSectionAttributes,
) -> collections.abc.Iterator[str]:
    for item in section.value:
        declared = cls.all_members.get(item.name)
        attribute = _resolve(declared) if declared is not None else None
        annotation = attribute.annotation if isinstance(attribute, griffe.Attribute) else None
        if declared is None:
            yield f"{cls.path}: the docstring names attribute {item.name}, which does not exist"
        elif item.annotation is None and not _is_enum(cls):
            yield f"{cls.path}.{item.name}: no annotation"
        elif item.annotation is not annotation:
            yield f"{cls.path}: the docstring types attribute {item.name}"


def _check_returns(
    function: griffe.Function,
    docstring: griffe.Docstring,
    section: griffe.DocstringSectionReturns,
) -> collections.abc.Iterator[str]:
    written_on = docstring.parent
    declared = written_on.returns if isinstance(written_on, griffe.Function) else None
    for item in section.value:
        if item.annotation is not declared:
            yield f"{function.path}: the docstring types its return"
        elif _canonical(declared) != _canonical(function.returns):
            yield f"{function.path}: inherits a Returns of {declared}, but returns {function.returns}"


def _canonical(annotation: griffe.Expr | str | None) -> str:
    if isinstance(annotation, griffe.ExprName | griffe.ExprAttribute):
        return annotation.canonical_path
    if isinstance(annotation, griffe.Expr):
        return "".join(_canonical(part) for part in annotation.iterate(flat=False))
    return str(annotation)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    problems = check_api(pathlib.Path(__file__).parents[2])
    for problem in problems:
        _LOGGER.error("%s", problem)
    if problems:
        sys.exit(1)
    _LOGGER.info("API check passed")


if __name__ == "__main__":
    main()
