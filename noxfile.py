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

import itertools
import json
import os
import pathlib
import re
import tomllib
import typing

import nox

if typing.TYPE_CHECKING:
    import collections.abc


nox.options.sessions = ["reformat", "ruff", "slotscheck", "pyright", "pytest", "audit", "docs"]
nox.options.default_venv_backend = "uv"


PYTHON_PATHS = ["noxfile.py", "risa/", "tests/", "tools/", "docs/examples/"]


@nox.session(reuse_venv=True)
def reformat(session: nox.Session) -> None:
    sync(session, groups=["ruff"], self=True)

    session.run("ruff", "format", *PYTHON_PATHS, *session.posargs)
    session.run("ruff", "check", *PYTHON_PATHS, "--select", "I,RUF022,RUF023", "--fix", *session.posargs)


@nox.session(name="format-check", reuse_venv=True)
def format_check(session: nox.Session) -> None:
    # Non-mutating counterpart to `reformat`, for CI.
    sync(session, groups=["ruff"], self=True)

    session.run("ruff", "format", "--check", *PYTHON_PATHS, *session.posargs)
    session.run("ruff", "check", *PYTHON_PATHS, "--select", "I,RUF022,RUF023", *session.posargs)


@nox.session(reuse_venv=True)
def ruff(session: nox.Session) -> None:
    sync(session, groups=["ruff"], self=True)

    session.run("ruff", "check", *PYTHON_PATHS, *session.posargs)


@nox.session(reuse_venv=True)
def slotscheck(session: nox.Session) -> None:
    # Verifies every class defines __slots__ and that no slotted class inherits
    # from an unslotted one, which would silently restore a per-instance __dict__.
    sync(session, groups=["slotscheck"], self=True)

    session.run("slotscheck", "-m", "risa", *session.posargs)


@nox.session(reuse_venv=True)
def pyright(session: nox.Session) -> None:
    # The pytest group is required so that `tests/` type-checks too.
    sync(session, groups=["pyright", "pytest"], self=True)

    session.run("pyright")


@nox.session(reuse_venv=True)
def pytest(session: nox.Session) -> None:
    sync(session, groups=["pytest"], self=True)

    junit = ["--junitxml=junit.xml", "-o", "junit_family=legacy"]
    try:
        session.run("coverage", "run", "-m", "pytest", *junit, *session.posargs)
    finally:
        # Codecov comments on a failed run only once it has that run's coverage too.
        session.run("coverage", "xml", "--quiet")


def declared_floors() -> dict[str, str]:
    with pathlib.Path("pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    requirements = [*project["dependencies"], *itertools.chain.from_iterable(project["optional-dependencies"].values())]
    return dict(requirement.split("~=", 1) for requirement in requirements)


@nox.session
@nox.parametrize(("python", "resolution"), [("3.12", "lowest-direct"), ("3.15", "highest")], ids=["lowest", "highest"])
def compat(session: nox.Session, resolution: str) -> None:
    # The lockfile holds hikari where lightbulb pins it, so this tests both ends of the declared ranges.
    session.install("--resolution", resolution, "--group", "pytest", "--editable", ".[postgres,radixly,redis]")
    floors = declared_floors()
    script = "import importlib.metadata as m, json, sys; print(json.dumps({n: m.version(n) for n in sys.argv[1:]}))"
    output = session.run("python", "-c", script, *floors, silent=True)
    installed: dict[str, str] = json.loads(str(output))
    session.log(", ".join(f"{name} {version}" for name, version in installed.items()))
    if resolution == "lowest-direct":
        # Another group's pin, such as lightbulb's on hikari, would otherwise lift a floor unnoticed.
        missed = {name: version for name, version in installed.items() if version != f"{floors[name]}.0"}
        if missed:
            session.error(f"the lowest resolution missed the declared floors: {missed}")
    session.run("pytest", *session.posargs)


@nox.session(reuse_venv=True)
def audit(session: nox.Session) -> None:
    # Scans uv.lock against the advisory databases. Requires uv >= 0.9; the
    # subcommand is still behind a preview flag.
    session.run("uv", "audit", "--preview-features", "audit-command", *session.posargs, external=True)


@nox.session(reuse_venv=True)
def docs(session: nox.Session) -> None:
    sync(session, groups=["docs"], self=True)

    session.run("python", "-m", "tools.docs.check_api")
    session.run("python", "-m", "tools.docs.prepare")
    session.run("zensical", "build", "--strict", "--clean")
    session.run("python", "-m", "tools.docs.embeds", "site")
    flags = ["--require-inventory-links"] if os.environ.get("CI") else []
    if os.environ.get("MIKE_DOCS_VERSION", "").startswith("pr/"):
        flags.append("--expect-noindex")
    session.run("python", "-m", "tools.docs.check_site", "site", *flags)


@nox.session(reuse_venv=True)
def release(session: nox.Session) -> None:
    if len(session.posargs) != 1:
        session.error("name the version: nox -s release -- 1.2.0")
    sync(session, groups=["changelog"])

    session.run("uv", "version", "--no-sync", *session.posargs, external=True)
    with pathlib.Path("pyproject.toml").open("rb") as file:
        version = tomllib.load(file)["project"]["version"]
    about = pathlib.Path("risa/_about.py")
    text = about.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^__version__: .*$", f'__version__: typing.Final[str] = "{version}"', text, count=1)
    about.write_text(text, encoding="utf-8")
    session.run("towncrier", "build", "--version", version, "--yes")


# uv_sync taken from: https://github.com/hikari-py/hikari/blob/master/pipelines/nox.py#L46
#
# Copyright (c) 2020 Nekokatt
# Copyright (c) 2021-present davfsa
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
def sync(
    session: nox.Session,
    /,
    *,
    self: bool = False,
    extras: collections.abc.Sequence[str] = (),
    groups: collections.abc.Sequence[str] = (),
) -> None:
    """Install session packages using `uv sync`."""
    if extras and not self:
        msg = "When specifying extras, set `self=True`."
        raise RuntimeError(msg)

    args: list[str] = []
    for extra in extras:
        args.extend(("--extra", extra))

    group_flag = "--group" if self else "--only-group"
    for group in groups:
        args.extend((group_flag, group))

    session.run_install(
        "uv",
        "sync",
        "--locked",
        *args,
        silent=True,
        env={"UV_PROJECT_ENVIRONMENT": session.virtualenv.location},
    )
