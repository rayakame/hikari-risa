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

import typing
import unittest.mock

import hikari
import linkd
import pytest

import risa
from risa.internal import constants
from risa.internal import registry


class GatewayApp:
    def __init__(self) -> None:
        self.rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
        self.event_manager = unittest.mock.Mock(spec=hikari.api.EventManager)


class RestApp:
    def __init__(self) -> None:
        self.rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
        self.interaction_server = unittest.mock.Mock(spec=hikari.api.InteractionServer)


class LightbulbStub:
    def __init__(self, app: object) -> None:
        self.app = app
        self.di = linkd.DependencyInjectionManager()


class Database: ...


@risa.register(name="client-panel")
class Panel(risa.View):
    label: str = "hi"


@risa.register(name="client-elsewhere")
class Elsewhere(risa.View):
    pass


def gateway_app() -> risa.GatewayClientAppT:
    return typing.cast("risa.GatewayClientAppT", GatewayApp())


def rest_app() -> risa.RestClientAppT:
    return typing.cast("risa.RestClientAppT", RestApp())


def routes(built: risa.Client, cls: type[risa.View]) -> bool:
    meta = getattr(cls, constants.VIEW_META)
    assert isinstance(meta, registry.ViewMeta)
    return built._resolve(meta.key) is not None  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


@pytest.fixture
def client() -> risa.GatewayEnabledClient:
    return risa.client_from_app(gateway_app())


def test_an_application_with_an_event_manager_gets_the_gateway_client() -> None:
    assert isinstance(risa.client_from_app(gateway_app()), risa.GatewayEnabledClient)


def test_an_application_with_an_interaction_server_gets_the_rest_client() -> None:
    assert isinstance(risa.client_from_app(rest_app()), risa.RestEnabledClient)


def test_the_gateway_client_listens_for_component_interactions() -> None:
    app = GatewayApp()
    risa.client_from_app(typing.cast("risa.GatewayClientAppT", app))

    app.event_manager.subscribe.assert_called_once()
    assert app.event_manager.subscribe.call_args.args[0] is hikari.ComponentInteractionCreateEvent


def test_the_rest_client_registers_an_interaction_listener() -> None:
    app = RestApp()
    risa.client_from_app(typing.cast("risa.RestClientAppT", app))

    app.interaction_server.set_listener.assert_called_once()
    assert app.interaction_server.set_listener.call_args.args[0] is hikari.ComponentInteraction


def test_the_client_keeps_the_application_it_was_built_from() -> None:
    app = gateway_app()
    assert risa.client_from_app(app).app is app


async def test_the_bots_own_types_are_injectable() -> None:
    app = gateway_app()
    built = risa.client_from_app(app)

    async with built.di.enter_context(risa.Contexts.DEFAULT) as container:
        assert await container.get(hikari.api.RESTClient) is app.rest
        assert await container.get(hikari.api.EventManager) is app.event_manager


async def test_a_dependency_registered_on_the_client_reaches_a_callback(
    client: risa.GatewayEnabledClient,
) -> None:
    database = Database()
    client.di.registry_for(risa.Contexts.DEFAULT).register_value(Database, database)

    @linkd.inject
    def ask(db: Database = risa.INJECTED) -> Database:
        return db

    async with client.di.enter_context(risa.Contexts.DEFAULT):
        assert await ask() is database


async def test_the_component_scope_sees_what_the_default_one_holds(
    client: risa.GatewayEnabledClient,
) -> None:
    database = Database()
    client.di.registry_for(risa.Contexts.DEFAULT).register_value(Database, database)

    async with (
        client.di.enter_context(risa.Contexts.DEFAULT),
        client.di.enter_context(risa.Contexts.COMPONENT) as container,
    ):
        assert await container.get(Database) is database


def test_a_lightbulb_client_shares_its_dependency_manager() -> None:
    lightbulb = LightbulbStub(GatewayApp())
    built = risa.client_from_lightbulb(typing.cast("risa.LightbulbClient", lightbulb))

    assert built.di is lightbulb.di


async def test_a_shared_manager_is_not_registered_against_twice() -> None:
    lightbulb = LightbulbStub(GatewayApp())
    built = risa.client_from_lightbulb(typing.cast("risa.LightbulbClient", lightbulb))

    async with built.di.enter_context(risa.Contexts.DEFAULT) as container:
        with pytest.raises(linkd.DependencyNotSatisfiableException):
            await container.get(hikari.api.RESTClient)


def test_a_lightbulb_client_on_neither_transport_is_refused() -> None:
    with pytest.raises(TypeError, match="neither"):
        risa.client_from_lightbulb(typing.cast("risa.LightbulbClient", LightbulbStub(object())))


def test_a_view_added_to_a_client_is_routed(client: risa.GatewayEnabledClient) -> None:
    client.add_view(Panel)
    assert routes(client, Panel)


def test_add_view_returns_the_class_so_it_reads_as_a_decorator(client: risa.GatewayEnabledClient) -> None:
    assert client.add_view(Panel) is Panel


def test_a_class_that_was_never_registered_is_refused(client: risa.GatewayEnabledClient) -> None:
    class Bare(risa.View):
        pass

    with pytest.raises(risa.NotAViewError):
        client.add_view(Bare)


def test_a_client_does_not_answer_for_views_it_was_never_given(client: risa.GatewayEnabledClient) -> None:
    client.add_view(Panel)
    assert not routes(client, Elsewhere)


def test_use_global_answers_for_everything_registered_in_the_process() -> None:
    built = risa.client_from_app(gateway_app(), use_global=True)
    assert routes(built, Elsewhere)
