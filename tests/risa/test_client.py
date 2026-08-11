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

import asyncio
import logging
import typing
import unittest.mock

import hikari
import linkd
import pytest

import risa
from risa import ui
from risa.internal import codec
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


@risa.register(name="client-static")
class Static(risa.View):
    message: str

    def render(self) -> ui.Layout:
        return ui.Container(ui.TextDisplay(self.message))


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


def interaction_with(custom_id: str) -> unittest.mock.Mock:
    interaction = unittest.mock.Mock(spec=hikari.ComponentInteraction)
    interaction.custom_id = custom_id
    return interaction


def encoded_id_for(cls: type[risa.View], handler: str = "ab") -> str:
    meta = getattr(cls, constants.VIEW_META)
    assert isinstance(meta, registry.ViewMeta)
    return codec.CustomID(cookie=meta.key, handler=handler, fragment_index=0, fragment="", tail="").encode()


async def test_a_foreign_custom_id_is_ignored_silently(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    interaction = interaction_with("miru:settings:3")

    with caplog.at_level(logging.DEBUG, logger="risa.client"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not caplog.records
    interaction.create_initial_response.assert_not_called()


async def test_a_risa_id_nobody_answers_for_is_logged(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    interaction = interaction_with(encoded_id_for(Elsewhere))

    with caplog.at_level(logging.DEBUG, logger="risa.client"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "no view" in caplog.text
    interaction.create_initial_response.assert_not_called()


async def test_a_registered_views_component_is_recognised(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.add_view(Panel)
    interaction = interaction_with(encoded_id_for(Panel))

    with caplog.at_level(logging.DEBUG, logger="risa.client"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "client-panel" in caplog.text


CALLS: list[tuple[risa.View, risa.ComponentContext]] = []


class _ReleaseGate:
    def __init__(self) -> None:
        self.event = asyncio.Event()


RELEASE = _ReleaseGate()


@risa.register(name="client-sluggish")
class Sluggish(risa.View):
    @risa.handler
    async def update(self, ctx: risa.ComponentContext) -> None:
        CALLS.append((self, ctx))
        await RELEASE.event.wait()

    @risa.handler(defer=risa.AutoDefer.THINKING)
    async def thinking(self, ctx: risa.ComponentContext) -> None:
        CALLS.append((self, ctx))
        await RELEASE.event.wait()

    @risa.handler(defer=risa.AutoDefer.THINKING_EPHEMERAL)
    async def whisper(self, ctx: risa.ComponentContext) -> None:
        CALLS.append((self, ctx))
        await RELEASE.event.wait()

    @risa.handler(defer=risa.AutoDefer.OFF)
    async def alone(self, ctx: risa.ComponentContext) -> None:
        CALLS.append((self, ctx))
        await RELEASE.event.wait()

    @risa.handler
    async def quick(self, ctx: risa.ComponentContext) -> None:
        CALLS.append((self, ctx))
        await ctx.respond("hi")


@risa.register(name="client-spinner", defer=risa.AutoDefer.THINKING)
class Spinner(risa.View):
    @risa.handler
    async def wait(self, ctx: risa.ComponentContext) -> None:
        CALLS.append((self, ctx))
        await RELEASE.event.wait()


def deferring_client(cls: type[risa.View]) -> risa.GatewayEnabledClient:
    built = risa.client_from_app(gateway_app(), auto_defer_delay=0.0)
    built.add_view(cls)
    return built


async def until_acknowledged(interaction: unittest.mock.Mock) -> None:
    for _ in range(50):
        if interaction.create_initial_response.await_count:
            return
        await asyncio.sleep(0)
    pytest.fail("the watchdog never acknowledged the interaction")


@risa.register(name="client-clicker")
class Clicker(risa.View):
    @risa.handler
    async def press(self, ctx: risa.ComponentContext) -> None:
        CALLS.append((self, ctx))


@risa.register(name="client-faulty")
class Faulty(risa.View):
    @risa.handler
    async def boom(self, _ctx: risa.ComponentContext) -> None:
        msg = f"boom from {type(self).__name__}"
        raise RuntimeError(msg)


async def test_a_click_runs_the_handler(client: risa.GatewayEnabledClient) -> None:
    client.add_view(Clicker)
    CALLS.clear()
    interaction = interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token))

    await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    ((view, ctx),) = CALLS
    assert isinstance(view, Clicker)
    assert ctx.interaction is interaction


async def test_each_click_dispatches_against_a_fresh_view(client: risa.GatewayEnabledClient) -> None:
    client.add_view(Clicker)
    CALLS.clear()
    interaction = interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token))

    await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    (first, _), (second, _) = CALLS
    assert first is not second


async def test_a_component_whose_handler_was_retired_warns(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.add_view(Clicker)
    CALLS.clear()
    interaction = interaction_with(encoded_id_for(Clicker))

    with caplog.at_level(logging.WARNING, logger="risa.client"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "version bump" in caplog.text
    assert not CALLS


async def test_a_raising_handler_is_contained_and_logged(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.add_view(Faulty)
    interaction = interaction_with(encoded_id_for(Faulty, handler=Faulty.boom.token))

    with caplog.at_level(logging.ERROR, logger="risa.client"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "boom" in caplog.text
    assert "RuntimeError" in caplog.text


async def dispatch_slowly(built: risa.Client, interaction: unittest.mock.Mock) -> None:
    RELEASE.event = asyncio.Event()
    task = asyncio.create_task(built._process_interaction(interaction))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    try:
        await until_acknowledged(interaction)
    finally:
        RELEASE.event.set()
        await task


async def test_the_watchdog_acks_a_slow_handler_with_the_silent_update() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.update.token))

    await dispatch_slowly(built, interaction)

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE
    interaction.create_initial_response.assert_awaited_once()


async def test_a_thinking_handler_gets_the_spinner() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.thinking.token))

    await dispatch_slowly(built, interaction)

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    assert call.kwargs["flags"] is hikari.UNDEFINED


async def test_an_ephemeral_thinking_handler_whispers() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.whisper.token))

    await dispatch_slowly(built, interaction)

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    assert call.kwargs["flags"] is hikari.MessageFlag.EPHEMERAL


async def test_a_view_level_defer_applies_to_its_handlers() -> None:
    built = deferring_client(Spinner)
    interaction = interaction_with(encoded_id_for(Spinner, handler=Spinner.wait.token))

    await dispatch_slowly(built, interaction)

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.DEFERRED_MESSAGE_CREATE


async def test_off_disables_the_watchdog_but_not_the_answer() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.alone.token))

    RELEASE.event = asyncio.Event()
    task = asyncio.create_task(built._process_interaction(interaction))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    try:
        for _ in range(50):
            await asyncio.sleep(0)
        interaction.create_initial_response.assert_not_called()
    finally:
        RELEASE.event.set()
        await task

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE


async def test_a_handler_that_responds_is_never_second_guessed() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.quick.token))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    for _ in range(50):
        await asyncio.sleep(0)

    interaction.create_initial_response.assert_awaited_once()
    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.MESSAGE_CREATE


async def test_a_clean_finish_without_a_response_is_still_answered() -> None:
    built = deferring_client(Clicker)
    interaction = interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    call = interaction.create_initial_response.await_args
    assert call is not None
    assert call.args[0] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE


async def test_a_raising_handler_is_never_acknowledged() -> None:
    built = deferring_client(Faulty)
    interaction = interaction_with(encoded_id_for(Faulty, handler=Faulty.boom.token))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    for _ in range(50):
        await asyncio.sleep(0)

    interaction.create_initial_response.assert_not_called()


async def test_build_emits_what_the_view_renders(client: risa.GatewayEnabledClient) -> None:
    built = await client.build(Static(message="hello"))

    assert len(built) == 1
    payload, _attachments = built[0].build()
    assert payload["type"] == hikari.ComponentType.CONTAINER
    assert payload["components"][0]["content"] == "hello"
