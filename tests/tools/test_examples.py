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
"""What the pages claim about an example, held by running it."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import typing
import unittest.mock

import linkd
import pytest

import risa

if typing.TYPE_CHECKING:
    import types

_EXAMPLES: typing.Final = pathlib.Path(__file__).parents[2] / "docs" / "examples"


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, _EXAMPLES / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client() -> risa.Client:
    app = unittest.mock.Mock(rest=unittest.mock.AsyncMock())
    return risa.client_from_app(typing.cast("risa.GatewayClientAppT", app))


async def test_the_store_of_chapter_12_passes_verify_store() -> None:
    await _load("ch12_dict_store").test_dict_store()


async def test_the_wallet_of_chapter_06_opens_for_each_click_from_one_database() -> None:
    module = _load("ch06_wallet")
    client = _client()
    module.add_database(client)
    module.add_wallet(client)

    wallets: list[object] = []
    databases: list[object] = []
    for _ in range(2):
        async with (
            client.di.enter_context(risa.Contexts.DEFAULT),
            client.di.enter_context(risa.Contexts.COMPONENT) as container,
        ):
            container.add_value(risa.ComponentContext, unittest.mock.Mock(spec=risa.ComponentContext))
            wallets.append(await container.get(module.Wallet))
            databases.append(await container.get(module.Database))
    await client.di.close()

    assert wallets[0] is not wallets[1]
    assert databases[0] is databases[1]


async def test_the_function_of_chapter_06_needs_the_context_it_opens() -> None:
    module = _load("ch06_feedback")
    client = _client()
    module.add_databases(client)

    with pytest.raises(linkd.DependencyNotSatisfiableException):
        await module.post_votes()

    async with client.di.enter_context(risa.Contexts.DEFAULT):
        await module.post_votes()
    await client.di.close()

    typing.cast("unittest.mock.AsyncMock", client.rest).create_message.assert_awaited_once()
