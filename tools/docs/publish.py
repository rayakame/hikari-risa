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
"""Places one built version of the documentation site into a checkout of the gh-pages branch."""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import shutil
import typing

__all__ = ("publish", "release_folder")

_LOGGER: typing.Final[logging.Logger] = logging.getLogger("tools.docs.publish")
_MAIN: typing.Final = "main"
_STABLE: typing.Final = "stable"
_TAG: typing.Final = re.compile(r"v(\d+)\.(\d+)\.\d+")
_FOLDER: typing.Final = re.compile(r"(\d+)\.(\d+)")
_REDIRECT: typing.Final = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Redirecting</title>
<link rel="canonical" href="{destination}/">
<meta http-equiv="refresh" content="0; url={destination}/">
</head>
<body>
<p><a href="{destination}/">Continue to the documentation</a></p>
</body>
</html>
"""


def release_folder(tag: str) -> str:
    """Return the folder a release tag is published under, such as ``0.2`` for ``v0.2.1``.

    Returns
    -------
    str
        The ``<major>.<minor>`` folder name.

    Raises
    ------
    ValueError
        If the tag is not of the form ``v<major>.<minor>.<patch>``.
    """
    match = _TAG.fullmatch(tag)
    if match is None:
        message = f"not a release tag of the form v<major>.<minor>.<patch>: {tag!r}"
        raise ValueError(message)
    return f"{match[1]}.{match[2]}"


def publish(site: pathlib.Path, target: pathlib.Path, *, version: str) -> None:
    """Place a built site into a gh-pages checkout and refresh its index files.

    Parameters
    ----------
    site
        The built site.
    target
        A checkout of the gh-pages branch, possibly empty.
    version
        ``main`` or a release tag such as ``v0.2.1``.

    Raises
    ------
    ValueError
        If the version is neither ``main`` nor a plain release tag.
    NotADirectoryError
        If the site is not a directory.
    """
    folder = _MAIN if version == _MAIN else release_folder(version)
    if not site.is_dir():
        message = f"site is not a directory: {site}"
        raise NotADirectoryError(message)
    target.mkdir(parents=True, exist_ok=True)
    _replace(site, target / folder)

    releases = sorted(
        (path.name for path in target.iterdir() if path.is_dir() and _FOLDER.fullmatch(path.name)),
        key=_minor_key,
        reverse=True,
    )
    if folder != _MAIN and releases[0] == folder:
        _replace(site, target / _STABLE)

    entries = [
        {"version": name, "title": name, "aliases": [_STABLE] if index == 0 else []}
        for index, name in enumerate(releases)
    ]
    if (target / _MAIN).is_dir():
        # Before the first release main stands in for stable, which keeps the theme's outdated banner away.
        entries.append({"version": _MAIN, "title": _MAIN, "aliases": [] if releases else [_STABLE]})
    (target / "versions.json").write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    default = _STABLE if releases else _MAIN
    (target / "index.html").write_text(_REDIRECT.format(destination=default), encoding="utf-8")
    not_found = target / default / "404.html"
    if not_found.is_file():
        shutil.copyfile(not_found, target / "404.html")
    (target / ".nojekyll").touch()
    _LOGGER.info("published %s as %s", version, folder)


def _replace(site: pathlib.Path, destination: pathlib.Path) -> None:
    staging = destination.with_name(f".{destination.name}.tmp")
    try:
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(site, staging)
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _minor_key(folder: str) -> tuple[int, int]:
    major, minor = folder.split(".")
    return int(major), int(minor)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    argument_parser = argparse.ArgumentParser(
        description="Publish a built documentation site into a gh-pages checkout."
    )
    argument_parser.add_argument("--site", type=pathlib.Path, required=True)
    argument_parser.add_argument("--target", type=pathlib.Path, required=True)
    argument_parser.add_argument("--version", required=True)
    arguments = argument_parser.parse_args()
    publish(arguments.site, arguments.target, version=arguments.version)


if __name__ == "__main__":
    main()
