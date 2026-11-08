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
import contextlib
import enum
import logging
import typing
import unittest.mock

import hikari
import linkd
import msgspec
import pytest

import risa
from risa import codecs
from risa import ui
from risa.internal import codec
from risa.internal import constants
from risa.internal import reader
from risa.internal import registry
from risa.state import anchor
from risa.state import message
from risa.state import placement
from risa.state import stateless
from risa.state import stored
from risa.ui import build as build_

if typing.TYPE_CHECKING:
    import collections.abc


def emitted(layout: ui.Layout, meta: registry.ViewMeta) -> typing.Sequence[hikari.api.ComponentBuilder]:
    return build_.plan(layout, meta).emit(b"")


class GatewayApp:
    def __init__(self) -> None:
        self.rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
        self.event_manager = unittest.mock.Mock(spec=hikari.api.EventManager)


class InteractionServerStub:
    def __init__(self) -> None:
        self.listeners: dict[type[hikari.PartialInteraction], object] = {}

    def get_listener(self, interaction_type: type[hikari.PartialInteraction], /) -> object | None:
        return self.listeners.get(interaction_type)

    def set_listener(
        self,
        interaction_type: type[hikari.PartialInteraction],
        listener: object,
        /,
        *,
        replace: bool = False,
    ) -> None:
        if not replace and interaction_type in self.listeners:
            msg = f"Listener already set for {interaction_type.__name__}"
            raise TypeError(msg)
        self.listeners[interaction_type] = listener


class RestApp:
    def __init__(self) -> None:
        self.rest = unittest.mock.Mock(spec=hikari.api.RESTClient)
        self.interaction_server = InteractionServerStub()
        self.entity_factory = unittest.mock.Mock(spec=hikari.api.EntityFactory)
        self.http_settings = hikari.impl.HTTPSettings()
        self.proxy_settings = hikari.impl.ProxySettings()
        self.executor = None


class Downstream:
    def __init__(self) -> None:
        self.seen: list[hikari.ComponentInteraction] = []
        self.response = unittest.mock.Mock(spec=hikari.api.InteractionMessageBuilder)

    async def stream(
        self,
        interaction: hikari.ComponentInteraction,
    ) -> collections.abc.AsyncGenerator[hikari.api.InteractionResponseBuilder | None, None]:
        self.seen.append(interaction)
        yield self.response

    async def answer(self, interaction: hikari.ComponentInteraction) -> hikari.api.InteractionResponseBuilder | None:
        self.seen.append(interaction)
        return self.response

    async def fail(self, interaction: hikari.ComponentInteraction) -> hikari.api.InteractionResponseBuilder | None:
        self.seen.append(interaction)
        msg = "downstream broke"
        raise RuntimeError(msg)


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


def sharing_app(listener: object) -> RestApp:
    app = RestApp()
    app.interaction_server.set_listener(hikari.ComponentInteraction, listener)
    return app


def rest_of(built: risa.Client) -> unittest.mock.Mock:
    return typing.cast("unittest.mock.Mock", built.rest)


def routes(built: risa.Client, cls: type[risa.View]) -> bool:
    meta = getattr(cls, constants.VIEW_META)
    assert isinstance(meta, registry.ViewMeta)
    return built._resolve(meta.cookie) is not None  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


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
    built = risa.client_from_app(typing.cast("risa.RestClientAppT", app))

    assert app.interaction_server.get_listener(hikari.ComponentInteraction) == built._on_interaction  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


def test_a_rest_client_takes_the_slot_a_listener_already_holds() -> None:
    app = sharing_app(Downstream().stream)
    built = risa.client_from_app(typing.cast("risa.RestClientAppT", app))

    assert app.interaction_server.get_listener(hikari.ComponentInteraction) == built._on_interaction  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


def test_taking_over_a_listener_warns_and_names_it(caplog: pytest.LogCaptureFixture) -> None:
    app = sharing_app(Downstream().stream)

    with caplog.at_level(logging.WARNING, logger="risa"):
        risa.client_from_app(typing.cast("risa.RestClientAppT", app))

    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert "Downstream.stream" in record.getMessage()
    assert "warn_shared_listener=False" in record.getMessage()


def test_the_takeover_warning_can_be_switched_off(caplog: pytest.LogCaptureFixture) -> None:
    app = sharing_app(Downstream().stream)

    with caplog.at_level(logging.WARNING, logger="risa"):
        risa.client_from_app(typing.cast("risa.RestClientAppT", app), warn_shared_listener=False)

    assert not caplog.records


def test_an_empty_slot_is_taken_without_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="risa"):
        risa.client_from_app(rest_app())

    assert not caplog.records


def test_a_lightbulb_client_passes_the_switch_on(caplog: pytest.LogCaptureFixture) -> None:
    lightbulb = LightbulbStub(sharing_app(Downstream().stream))

    with caplog.at_level(logging.WARNING, logger="risa"):
        risa.client_from_lightbulb(typing.cast("risa.LightbulbClient", lightbulb), warn_shared_listener=False)

    assert not caplog.records


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
    interaction.message = unittest.mock.Mock(
        spec=hikari.Message,
        id=hikari.Snowflake(1),
        flags=hikari.MessageFlag.NONE,
        components=[],
    )
    return interaction


def interaction_on(
    view: risa.View,
    layout: ui.Layout,
    *,
    clicked: int = 0,
    sent_as: placement.InMessage | None = None,
) -> unittest.mock.Mock:
    meta = registry.require_meta(view)
    if sent_as is not None:
        meta = msgspec.structs.replace(meta, state=sent_as)
    state = message.MessageBackend().first_anchor(meta=meta, view=view) if meta.schema.durable else b""
    ids = [
        typing.cast("str", leaf["custom_id"])
        for builder in build_.plan(layout, meta).emit(state)
        for leaf in typing.cast("list[dict[str, object]]", builder.build()[0]["components"])
    ]
    interaction = interaction_with(ids[clicked])
    interaction.message.components = [
        hikari.ActionRowComponent(
            type=hikari.ComponentType.ACTION_ROW,
            id=0,
            components=[
                hikari.ButtonComponent(
                    type=hikari.ComponentType.BUTTON,
                    id=0,
                    style=hikari.ButtonStyle.PRIMARY,
                    label=None,
                    emoji=None,
                    custom_id=raw,
                    url=None,
                    is_disabled=False,
                )
                for raw in ids
            ],
        ),
    ]
    return interaction


def encoded_id_for(cls: type[risa.View], handler: str = "ab", tail: str = "") -> str:
    meta = getattr(cls, constants.VIEW_META)
    assert isinstance(meta, registry.ViewMeta)
    return codec.CustomID(cookie=meta.cookie, handler=handler, fragment_index=0, fragment="", tail=tail).encode()


async def test_a_foreign_custom_id_is_ignored_silently(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    interaction = interaction_with("miru:settings:3")

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not caplog.records
    rest_of(client).create_interaction_response.assert_not_called()


async def test_a_risa_id_nobody_answers_for_is_logged(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    interaction = interaction_with(encoded_id_for(Elsewhere))

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "no view" in caplog.text
    rest_of(client).create_interaction_response.assert_not_called()


async def test_a_registered_views_component_is_recognised(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.add_view(Panel)
    interaction = interaction_with(encoded_id_for(Panel))

    with caplog.at_level(logging.DEBUG, logger="risa"):
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
    built = risa.client_from_app(gateway_app(), auto_defer=risa.AutoDefer.UPDATE, auto_defer_delay=0.0)
    built.add_view(cls)
    return built


def rest_deferring_client(cls: type[risa.View]) -> risa.RestEnabledClient:
    built = risa.client_from_app(rest_app(), auto_defer=risa.AutoDefer.UPDATE, auto_defer_delay=0.0)
    built.add_view(cls)
    return built


async def until_acknowledged(rest: unittest.mock.Mock) -> None:
    for _ in range(50):
        if rest.create_interaction_response.await_count:
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

    with caplog.at_level(logging.WARNING, logger="risa"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "version bump" in caplog.text
    assert not CALLS


async def test_a_raising_handler_is_contained_and_logged(
    client: risa.GatewayEnabledClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.add_view(Faulty)
    interaction = interaction_with(encoded_id_for(Faulty, handler=Faulty.boom.token))

    with caplog.at_level(logging.ERROR, logger="risa"):
        await client._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "boom" in caplog.text
    assert "RuntimeError" in caplog.text


async def dispatch_slowly(built: risa.Client, interaction: unittest.mock.Mock) -> None:
    RELEASE.event = asyncio.Event()
    task = asyncio.create_task(built._process_interaction(interaction))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    try:
        await until_acknowledged(rest_of(built))
    finally:
        RELEASE.event.set()
        await task


async def test_the_watchdog_acks_a_slow_handler_with_the_silent_update() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.update.token))

    await dispatch_slowly(built, interaction)

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE
    rest_of(built).create_interaction_response.assert_awaited_once()


async def test_a_thinking_handler_gets_the_spinner() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.thinking.token))

    await dispatch_slowly(built, interaction)

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    assert call.kwargs["flags"] is hikari.UNDEFINED


async def test_an_ephemeral_thinking_handler_whispers() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.whisper.token))

    await dispatch_slowly(built, interaction)

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_CREATE
    assert call.kwargs["flags"] is hikari.MessageFlag.EPHEMERAL


async def test_a_view_level_defer_applies_to_its_handlers() -> None:
    built = deferring_client(Spinner)
    interaction = interaction_with(encoded_id_for(Spinner, handler=Spinner.wait.token))

    await dispatch_slowly(built, interaction)

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_CREATE


async def test_off_leaves_the_answer_entirely_to_the_handler() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.alone.token))

    RELEASE.event = asyncio.Event()
    task = asyncio.create_task(built._process_interaction(interaction))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    try:
        for _ in range(50):
            await asyncio.sleep(0)
        rest_of(built).create_interaction_response.assert_not_called()
    finally:
        RELEASE.event.set()
        await task

    rest_of(built).create_interaction_response.assert_not_called()


async def test_an_armed_watchdog_still_answers_a_fast_silent_handler() -> None:
    built = deferring_client(Clicker)
    CALLS.clear()
    interaction = interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE


async def test_a_handler_that_responds_is_never_second_guessed() -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.quick.token))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    for _ in range(50):
        await asyncio.sleep(0)

    rest_of(built).create_interaction_response.assert_awaited_once()
    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.MESSAGE_CREATE


async def test_a_clean_finish_without_a_response_is_still_answered() -> None:
    built = deferring_client(Clicker)
    interaction = interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE


@risa.register(name="client-needy")
class Needy(risa.View):
    thing: str

    @risa.handler
    async def press(self, ctx: risa.ComponentContext) -> None:
        CALLS.append((self, ctx))


async def test_a_failing_watchdog_ack_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    built = deferring_client(Sluggish)
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.update.token))
    rest_of(built).create_interaction_response.side_effect = RuntimeError("interaction is gone")

    with caplog.at_level(logging.ERROR, logger="risa"):
        await dispatch_slowly(built, interaction)

    assert "auto-defer failed" in caplog.text


async def test_a_failing_final_ack_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    built = deferring_client(Clicker)
    interaction = interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token))
    rest_of(built).create_interaction_response.side_effect = RuntimeError("interaction is gone")
    CALLS.clear()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert len(CALLS) == 1
    assert "failed to acknowledge" in caplog.text


async def test_a_view_without_defaults_is_hydrated_from_its_message() -> None:
    built = deferring_client(Needy)
    interaction = interaction_on(Needy(thing="from the message"), ui.Row(ui.Button(Needy(thing="").press)))
    CALLS.clear()

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    ((view, _ctx),) = CALLS
    assert isinstance(view, Needy)
    assert view.thing == "from the message"


async def test_a_click_on_a_message_that_carries_no_state_routes_to_on_outdated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = deferring_client(Needy)
    interaction = interaction_with(encoded_id_for(Needy, handler=Needy.press.token))
    CALLS.clear()

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not CALLS
    assert "could not be reassembled" in caplog.text
    rest_of(built).create_interaction_response.assert_not_called()


async def test_a_raising_handler_is_never_acknowledged() -> None:
    built = deferring_client(Faulty)
    interaction = interaction_with(encoded_id_for(Faulty, handler=Faulty.boom.token))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    for _ in range(50):
        await asyncio.sleep(0)

    rest_of(built).create_interaction_response.assert_not_called()


async def test_build_emits_what_the_view_renders(client: risa.GatewayEnabledClient) -> None:
    built = await client.build(Static(message="hello"))

    assert isinstance(built, ui.Rendered)
    assert len(built.components) == 1
    payload, _attachments = built.components[0].build()
    assert payload["type"] == hikari.ComponentType.CONTAINER
    assert payload["components"][0]["content"] == "hello"


WIRE_CALLS: list[tuple[object, ...]] = []


class Weather(enum.Enum):
    SUN = 1
    RAIN = 2


class Spiky(enum.Enum):
    ONE = 1

    @classmethod
    def _missing_(cls, value: object) -> Spiky:
        raise KeyError(value)


@risa.register(name="client-wired")
class WiredClicker(risa.View):
    @risa.handler
    async def vote(self, _ctx: risa.ComponentContext, option: str, count: int = 7) -> None:  # ruff:ignore[no-self-use]
        WIRE_CALLS.append((option, count))

    @risa.handler
    async def survey(self, _ctx: risa.ComponentContext, agreed: bool, amount: int, label: str) -> None:  # ruff:ignore[boolean-type-hint-positional-argument, no-self-use]
        WIRE_CALLS.append((agreed, amount, label))

    @risa.handler
    async def aim(self, _ctx: risa.ComponentContext, user: hikari.Snowflake) -> None:  # ruff:ignore[no-self-use]
        WIRE_CALLS.append((user,))

    @risa.handler
    async def pick(self, _ctx: risa.ComponentContext, weather: Weather) -> None:  # ruff:ignore[no-self-use]
        WIRE_CALLS.append((weather,))

    @risa.handler
    async def prickly(self, _ctx: risa.ComponentContext, spike: Spiky) -> None:  # ruff:ignore[no-self-use]
        WIRE_CALLS.append((spike,))


def custom_id_of(cls: type[risa.View], button: ui.Button) -> str:
    meta = getattr(cls, constants.VIEW_META)
    assert isinstance(meta, registry.ViewMeta)
    (built,) = emitted(ui.Row(button), meta)
    payload, _attachments = built.build()
    (component,) = payload["components"]
    return typing.cast("str", component["custom_id"])


def wired_client() -> risa.GatewayEnabledClient:
    built = risa.client_from_app(gateway_app())
    built.add_view(WiredClicker)
    WIRE_CALLS.clear()
    return built


async def test_bound_wire_args_survive_the_full_dispatch_loop() -> None:
    built = wired_client()
    view = WiredClicker()
    interaction = interaction_with(custom_id_of(WiredClicker, ui.Button(risa.bind(view.vote, "Red", 5))))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert WIRE_CALLS == [("Red", 5)]


async def test_an_omitted_trailing_default_uses_the_current_python_default() -> None:
    built = wired_client()
    interaction = interaction_with(custom_id_of(WiredClicker, ui.Button(risa.bind(WiredClicker().vote, "Blue"))))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert WIRE_CALLS == [("Blue", 7)]


async def test_falsy_wire_values_are_not_mistaken_for_decode_failures() -> None:
    built = wired_client()
    interaction = interaction_with(
        custom_id_of(WiredClicker, ui.Button(risa.bind(WiredClicker().survey, agreed=False, amount=0, label="")))
    )

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    (received,) = WIRE_CALLS
    assert received[0] is False
    assert received[1] == 0
    assert isinstance(received[2], str)
    assert not received[2]


async def test_a_snowflake_arrives_as_a_snowflake() -> None:
    built = wired_client()
    interaction = interaction_with(
        custom_id_of(WiredClicker, ui.Button(risa.bind(WiredClicker().aim, hikari.Snowflake(1364800923047857855))))
    )

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    (received,) = WIRE_CALLS
    assert isinstance(received[0], hikari.Snowflake)
    assert received[0] == 1364800923047857855


async def test_an_enum_member_arrives_as_the_member_itself() -> None:
    built = wired_client()
    interaction = interaction_with(custom_id_of(WiredClicker, ui.Button(risa.bind(WiredClicker().pick, Weather.RAIN))))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    (received,) = WIRE_CALLS
    assert received[0] is Weather.RAIN


async def test_a_signature_edited_in_place_fails_closed_and_loud(caplog: pytest.LogCaptureFixture) -> None:
    built = wired_client()
    raw = custom_id_of(WiredClicker, ui.Button(risa.bind(WiredClicker().vote, "Red")))
    fingerprint = raw[codec.HEADER_LENGTH : codec.HEADER_LENGTH + codec.FINGERPRINT_LENGTH]
    swap = "!" if fingerprint[0] != "!" else "?"
    forged = raw[: codec.HEADER_LENGTH] + swap + raw[codec.HEADER_LENGTH + 1 :]

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction_with(forged))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "bump the handler version" in caplog.text
    assert not WIRE_CALLS


async def test_a_tail_on_a_zero_arg_handler_fails_closed_and_loud(caplog: pytest.LogCaptureFixture) -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Clicker)
    CALLS.clear()
    interaction = interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token, tail="ab12"))

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "declares no wire parameters" in caplog.text
    assert not CALLS


async def test_a_missing_fingerprint_on_a_wire_handler_fails_closed_and_loud(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = wired_client()
    interaction = interaction_with(encoded_id_for(WiredClicker, handler=WiredClicker.vote.token))

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "carries no wire fingerprint" in caplog.text
    assert not WIRE_CALLS


async def test_unreadable_frames_fail_closed_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    built = wired_client()
    tail = WiredClicker.vote.signature.fingerprint + '"'
    interaction = interaction_with(encoded_id_for(WiredClicker, handler=WiredClicker.vote.token, tail=tail))

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "unreadable" in caplog.text
    assert not WIRE_CALLS


async def test_a_frame_count_outside_the_declared_range_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    built = wired_client()
    signature = WiredClicker.vote.signature
    one_frame = codec.pack_frames([codecs.BASE85.encode(codec.StrConverter().encode("x"))])
    interaction = interaction_with(
        encoded_id_for(WiredClicker, handler=WiredClicker.vote.token, tail=signature.fingerprint + one_frame * 3),
    )

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "accepts at most" in caplog.text
    assert not WIRE_CALLS


async def test_a_default_removed_in_place_is_diagnosed_as_the_signature_edit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = wired_client()
    interaction = interaction_with(
        encoded_id_for(WiredClicker, handler=WiredClicker.vote.token, tail=WiredClicker.vote.signature.fingerprint),
    )

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "bump the handler version" in caplog.text
    assert not WIRE_CALLS


async def test_a_forged_empty_frame_is_not_a_zero(caplog: pytest.LogCaptureFixture) -> None:
    built = wired_client()
    forged = codec.pack_frames([""])
    interaction = interaction_with(
        encoded_id_for(
            WiredClicker, handler=WiredClicker.aim.token, tail=WiredClicker.aim.signature.fingerprint + forged
        ),
    )

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "could not be decoded" in caplog.text
    assert not WIRE_CALLS


async def test_a_converter_that_raises_is_contained_as_unreadable(caplog: pytest.LogCaptureFixture) -> None:
    built = wired_client()
    stale = codec.pack_frames([codecs.BASE85.encode(codec.IntConverter(int).encode(99))])
    interaction = interaction_with(
        encoded_id_for(
            WiredClicker, handler=WiredClicker.prickly.token, tail=WiredClicker.prickly.signature.fingerprint + stale
        ),
    )

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "raised" in caplog.text
    assert not WIRE_CALLS


async def test_a_second_client_on_a_shared_manager_says_which_one_wins(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lightbulb = LightbulbStub(GatewayApp())
    first = risa.client_from_lightbulb(typing.cast("risa.LightbulbClient", lightbulb))
    first.add_view(DiWired)
    DI_CALLS.clear()

    with caplog.at_level(logging.WARNING, logger="risa"):
        second = risa.client_from_lightbulb(typing.cast("risa.LightbulbClient", lightbulb))

    assert "already registered" in caplog.text
    interaction = interaction_with(custom_id_of(DiWired, ui.Button(DiWired().whoami)))
    await first._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    ((got,),) = DI_CALLS
    assert got is second


async def test_a_deleted_enum_member_fails_closed_naming_the_parameter(caplog: pytest.LogCaptureFixture) -> None:
    built = wired_client()
    stale = codec.pack_frames([codecs.BASE85.encode(codec.IntConverter(int).encode(99))])
    interaction = interaction_with(
        encoded_id_for(
            WiredClicker, handler=WiredClicker.pick.token, tail=WiredClicker.pick.signature.fingerprint + stale
        ),
    )

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "could not be decoded" in caplog.text
    assert "weather" in caplog.text
    assert not WIRE_CALLS


DI_CALLS: list[tuple[object, ...]] = []


@linkd.inject
def peek_context(ctx: risa.ComponentContext = risa.INJECTED) -> risa.ComponentContext:
    return ctx


@risa.register(name="client-di-wired")
class DiWired(risa.View):
    @risa.handler
    async def fetch(self, _ctx: risa.ComponentContext, option: int, db: Database) -> None:  # ruff:ignore[no-self-use]
        DI_CALLS.append((option, db))

    @risa.handler
    async def marked(self, _ctx: risa.ComponentContext, option: int, db: Database = risa.INJECTED) -> None:  # ruff:ignore[no-self-use]
        DI_CALLS.append((option, db))

    @risa.handler
    async def probe(self, _ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        DI_CALLS.append((await peek_context(),))

    @risa.handler
    async def whoami(self, _ctx: risa.ComponentContext, client: risa.Client) -> None:  # ruff:ignore[no-self-use]
        DI_CALLS.append((client,))


def di_client(*, register_database: bool = True) -> tuple[risa.GatewayEnabledClient, Database]:
    built = risa.client_from_app(gateway_app())
    built.add_view(DiWired)
    database = Database()
    if register_database:
        built.di.registry_for(risa.Contexts.DEFAULT).register_value(Database, database)
    DI_CALLS.clear()
    return built, database


async def test_a_bare_di_parameter_resolves_beside_wire_args() -> None:
    built, database = di_client()
    interaction = interaction_with(custom_id_of(DiWired, ui.Button(risa.bind(DiWired().fetch, 3))))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    ((option, db),) = DI_CALLS
    assert option == 3
    assert db is database


async def test_an_injected_default_resolves_the_same_way() -> None:
    built, database = di_client()
    interaction = interaction_with(custom_id_of(DiWired, ui.Button(risa.bind(DiWired().marked, 9))))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    ((option, db),) = DI_CALLS
    assert option == 9
    assert db is database


async def test_an_unsatisfiable_dependency_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    built, _database = di_client(register_database=False)
    interaction = interaction_with(custom_id_of(DiWired, ui.Button(risa.bind(DiWired().fetch, 1))))

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "failed while answering" in caplog.text
    assert not DI_CALLS


class Wallet: ...


def wallet_torn_down(_wallet: Wallet) -> None:
    DI_CALLS.append(("torn down",))


@risa.register(name="client-di-per-click")
class DiPerClick(risa.View):
    async def load(self, wallet: Wallet = risa.INJECTED) -> None:  # ruff:ignore[no-self-use]
        DI_CALLS.append((wallet,))

    @risa.handler
    async def spend(self, _ctx: risa.ComponentContext, wallet: Wallet) -> None:  # ruff:ignore[no-self-use]
        DI_CALLS.append((wallet,))

    @risa.handler
    async def explode(self, _ctx: risa.ComponentContext, wallet: Wallet) -> None:  # ruff:ignore[no-self-use]
        DI_CALLS.append((wallet,))
        raise RuntimeError


def per_click_client() -> risa.GatewayEnabledClient:
    built = risa.client_from_app(gateway_app())
    built.add_view(DiPerClick)
    built.di.registry_for(risa.Contexts.COMPONENT).register_factory(Wallet, Wallet, teardown=wallet_torn_down)
    DI_CALLS.clear()
    return built


async def test_a_click_makes_a_component_dependency_once_and_load_shares_it() -> None:
    built = per_click_client()

    for _ in range(2):
        interaction = interaction_with(custom_id_of(DiPerClick, ui.Button(DiPerClick().spend)))
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    (first_load,), (first_handler,), first_end, (second_load,), (second_handler,), second_end = DI_CALLS
    assert first_load is first_handler
    assert second_load is second_handler
    assert first_load is not second_load
    assert first_end == second_end == ("torn down",)


async def test_a_component_dependency_is_torn_down_after_a_handler_raises() -> None:
    built = per_click_client()
    interaction = interaction_with(custom_id_of(DiPerClick, ui.Button(DiPerClick().explode)))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert DI_CALLS[-1] == ("torn down",)


async def test_build_runs_load_without_the_component_context() -> None:
    built = per_click_client()

    with pytest.raises(linkd.DependencyNotSatisfiableException):
        await built.build(DiPerClick())


def open_database_from_click(_ctx: risa.ComponentContext) -> Database:
    return Database()


async def test_a_default_dependency_cannot_name_a_component_one(caplog: pytest.LogCaptureFixture) -> None:
    built, _database = di_client(register_database=False)
    built.di.registry_for(risa.Contexts.DEFAULT).register_factory(Database, open_database_from_click)
    interaction = interaction_with(custom_id_of(DiWired, ui.Button(risa.bind(DiWired().fetch, 1))))

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "failed while answering" in caplog.text
    assert not DI_CALLS


@risa.register(name="client-dense-votes", state=risa.InMessage(codec=codecs.BASE32768))
class DenseVotes(risa.View):
    voters: list[int] = msgspec.field(default_factory=list[int])

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.vote, label="Vote"), ui.Button(self.vote, label="Vote too"))

    @risa.handler
    async def vote(self, ctx: risa.ComponentContext) -> None:
        self.voters.append(self.voters[0])
        DI_CALLS.append((len(self.voters),))
        await ctx.rerender()


async def test_a_new_codec_reads_a_full_message_and_makes_room_at_its_rerender() -> None:
    voter = 1298471056230400001
    full = DenseVotes(voters=[voter] * 14)
    grown = DenseVotes(voters=[voter] * 15)
    meta = registry.require_meta(DenseVotes)
    built = risa.client_from_app(gateway_app())
    built.add_view(DenseVotes)
    DI_CALLS.clear()
    interaction = interaction_on(full, full.render(), sent_as=risa.InMessage())
    assert interaction.custom_id[1] == codecs.BASE85.tag

    with pytest.raises(risa.StateOverflowError):
        build_.plan(grown.render(), msgspec.structs.replace(meta, state=risa.InMessage())).emit(
            message.MessageBackend().first_anchor(meta=meta, view=grown),
        )

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert DI_CALLS == [(15,)]
    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    (row,) = call.kwargs["components"]
    assert {leaf["custom_id"][1] for leaf in row.build()[0]["components"]} == {codecs.BASE32768.tag}


async def test_the_client_injects_itself_for_cross_file_handlers() -> None:
    built, _database = di_client()
    interaction = interaction_with(custom_id_of(DiWired, ui.Button(DiWired().whoami)))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    ((got,),) = DI_CALLS
    assert got is built


async def test_the_client_injects_itself_even_on_a_shared_manager() -> None:
    lightbulb = LightbulbStub(GatewayApp())
    built = risa.client_from_lightbulb(typing.cast("risa.LightbulbClient", lightbulb))
    built.add_view(DiWired)
    DI_CALLS.clear()
    interaction = interaction_with(custom_id_of(DiWired, ui.Button(DiWired().whoami)))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    ((got,),) = DI_CALLS
    assert got is built


async def test_the_dispatch_container_carries_the_component_context() -> None:
    built, _database = di_client()
    interaction = interaction_with(custom_id_of(DiWired, ui.Button(DiWired().probe)))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    ((got,),) = DI_CALLS
    assert isinstance(got, risa.ComponentContext)
    assert got.interaction is interaction


@risa.register(name="client-repaint")
class Repaint(risa.View):
    label: str = "start"

    def render(self) -> ui.Layout:
        return ui.TextDisplay(self.label)

    @risa.handler
    async def flip(self, ctx: risa.ComponentContext) -> None:
        self.label = "flipped"
        await ctx.rerender()

    @risa.handler
    async def broken(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.edit(ui.Row(ui.Button(Sluggish().quick)))


def repaint_click(handler: str = "flip") -> unittest.mock.Mock:
    view = Repaint()
    button = ui.Button(view.flip) if handler == "flip" else ui.Button(view.broken)
    return interaction_on(view, ui.Row(button))


async def test_a_handler_mutating_self_and_rerendering_paints_the_mutation() -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Repaint)
    interaction = repaint_click()

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.MESSAGE_UPDATE
    (sent,) = call.kwargs["components"]
    payload, _attachments = sent.build()
    assert payload["content"] == "flipped"
    rest_of(built).create_interaction_response.assert_awaited_once()


async def test_an_edit_only_handler_answers_the_rest_transport() -> None:
    built = risa.client_from_app(rest_app())
    built.add_view(Repaint)
    interaction = repaint_click()

    listener = built._on_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    await asyncio.wait_for(anext(listener), timeout=1.0)
    with pytest.raises(StopAsyncIteration):
        await anext(listener)

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.MESSAGE_UPDATE


async def test_a_broken_edit_layout_is_contained_as_a_handler_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Repaint)
    interaction = repaint_click("broken")

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "failed while answering" in caplog.text
    rest_of(built).create_interaction_response.assert_not_called()


async def test_the_rest_listener_answers_204_while_the_handler_still_runs() -> None:
    built = rest_deferring_client(Sluggish)
    CALLS.clear()
    RELEASE.event = asyncio.Event()
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.update.token))

    listener = built._on_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    try:
        await asyncio.wait_for(anext(listener), timeout=1.0)

        assert len(CALLS) == 1
        assert not RELEASE.event.is_set()
    finally:
        RELEASE.event.set()
    with pytest.raises(StopAsyncIteration):
        await anext(listener)


async def test_an_adopted_interaction_that_answers_nothing_is_noted_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = risa.client_from_app(rest_app())
    built.add_view(Clicker)
    CALLS.clear()
    listener = built._on_interaction(interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token)))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await asyncio.wait_for(anext(listener), timeout=1.0)
        with pytest.raises(StopAsyncIteration):
            await anext(listener)

    assert len(CALLS) == 1
    assert "204" in caplog.text


async def test_a_handler_that_answered_is_not_noted(caplog: pytest.LogCaptureFixture) -> None:
    built = risa.client_from_app(rest_app())
    built.add_view(Repaint)
    listener = built._on_interaction(repaint_click())  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await asyncio.wait_for(anext(listener), timeout=1.0)
        with pytest.raises(StopAsyncIteration):
            await anext(listener)

    assert "204" not in caplog.text


async def test_a_foreign_interaction_is_never_noted(caplog: pytest.LogCaptureFixture) -> None:
    built = risa.client_from_app(rest_app())
    listener = built._on_interaction(interaction_with("miru:settings:3"))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await asyncio.wait_for(anext(listener), timeout=1.0)
        with pytest.raises(StopAsyncIteration):
            await anext(listener)

    assert not caplog.records


async def test_the_rest_listener_answers_promptly_for_unroutable_ids() -> None:
    built = risa.client_from_app(rest_app())
    listener = built._on_interaction(interaction_with("miru:settings:3"))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    await asyncio.wait_for(anext(listener), timeout=1.0)

    with pytest.raises(StopAsyncIteration):
        await anext(listener)


async def test_a_foreign_interaction_goes_to_the_listener_risa_replaced() -> None:
    downstream = Downstream()
    built = risa.client_from_app(typing.cast("risa.RestClientAppT", sharing_app(downstream.stream)))
    interaction = interaction_with("miru:settings:3")

    listener = built._on_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    assert await asyncio.wait_for(anext(listener), timeout=1.0) is downstream.response
    with pytest.raises(StopAsyncIteration):
        await anext(listener)

    assert downstream.seen == [interaction]


async def test_a_replaced_listener_that_returns_its_answer_is_handed_on_too() -> None:
    downstream = Downstream()
    built = risa.client_from_app(typing.cast("risa.RestClientAppT", sharing_app(downstream.answer)))
    interaction = interaction_with("miru:settings:3")

    listener = built._on_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    assert await asyncio.wait_for(anext(listener), timeout=1.0) is downstream.response
    with pytest.raises(StopAsyncIteration):
        await anext(listener)

    assert downstream.seen == [interaction]


async def test_a_risa_id_without_a_view_here_goes_to_the_replaced_listener() -> None:
    downstream = Downstream()
    built = risa.client_from_app(typing.cast("risa.RestClientAppT", sharing_app(downstream.stream)))
    interaction = interaction_with(encoded_id_for(Elsewhere))

    listener = built._on_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    assert await asyncio.wait_for(anext(listener), timeout=1.0) is downstream.response

    assert downstream.seen == [interaction]


async def test_an_interaction_risa_handles_never_reaches_the_replaced_listener() -> None:
    downstream = Downstream()
    built = risa.client_from_app(typing.cast("risa.RestClientAppT", sharing_app(downstream.stream)))
    built.add_view(Clicker)
    CALLS.clear()

    listener = built._on_interaction(interaction_with(encoded_id_for(Clicker, handler=Clicker.press.token)))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    assert await asyncio.wait_for(anext(listener), timeout=1.0) is None
    with pytest.raises(StopAsyncIteration):
        await anext(listener)

    assert len(CALLS) == 1
    assert downstream.seen == []


async def test_a_replaced_listener_that_raises_reaches_the_server_unchanged() -> None:
    downstream = Downstream()
    built = risa.client_from_app(typing.cast("risa.RestClientAppT", sharing_app(downstream.fail)))

    listener = built._on_interaction(interaction_with("miru:settings:3"))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    with pytest.raises(RuntimeError, match="downstream broke"):
        await asyncio.wait_for(anext(listener), timeout=1.0)


async def test_a_handler_missing_the_window_is_logged_but_never_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(constants, "INTERACTION_WINDOW", 0.01)
    built = rest_deferring_client(Sluggish)
    CALLS.clear()
    RELEASE.event = asyncio.Event()
    interaction = interaction_with(encoded_id_for(Sluggish, handler=Sluggish.alone.token))

    listener = built._on_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    with caplog.at_level(logging.ERROR, logger="risa"):
        await asyncio.wait_for(anext(listener), timeout=1.0)

    assert "no initial response" in caplog.text
    assert len(CALLS) == 1
    RELEASE.event.set()
    with pytest.raises(StopAsyncIteration):
        await anext(listener)


OUTDATED_CALLS: list[tuple[str, risa.ComponentContext]] = []


@risa.register(name="client-stoic")
class Stoic(risa.View):
    @risa.handler
    async def press(self, _ctx: risa.ComponentContext) -> None: ...


@risa.register(name="client-gracious")
class Gracious(risa.View):
    @risa.handler
    async def vote(self, _ctx: risa.ComponentContext, option: str) -> None: ...

    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        OUTDATED_CALLS.append((cls.__name__, ctx))
        await ctx.respond("that button is out of date", ephemeral=True)


@risa.register(name="client-heir")
class Heir(Gracious):
    pass


@risa.register(name="client-brittle")
class Brittle(risa.View):
    required: str

    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        OUTDATED_CALLS.append((cls.__name__, ctx))


@risa.register(name="client-sulky")
class Sulky(risa.View):
    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        OUTDATED_CALLS.append((cls.__name__, ctx))
        msg = "no thanks"
        raise RuntimeError(msg)


def meta_of(cls: type[risa.View]) -> registry.ViewMeta:
    meta = getattr(cls, constants.VIEW_META)
    assert isinstance(meta, registry.ViewMeta)
    return meta


def outdated_client(cls: type[risa.View]) -> risa.GatewayEnabledClient:
    built = risa.client_from_app(gateway_app())
    built.add_view(cls)
    OUTDATED_CALLS.clear()
    return built


def test_the_census_records_whether_a_view_answers_for_outdated_components() -> None:
    assert not meta_of(Stoic).handles_outdated
    assert meta_of(Gracious).handles_outdated
    assert meta_of(Heir).handles_outdated


async def test_a_retired_handler_on_a_silent_view_warns_and_calls_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = outdated_client(Stoic)
    interaction = interaction_with(encoded_id_for(Stoic))

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "no handler answers to token" in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING
    assert not OUTDATED_CALLS


async def test_a_view_that_answers_gets_the_hook_and_only_a_debug_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = outdated_client(Gracious)
    interaction = interaction_with(encoded_id_for(Gracious))

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "no handler answers to token" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    ((name, ctx),) = OUTDATED_CALLS
    assert name == "Gracious"
    assert ctx.interaction is interaction


async def test_the_hook_can_answer_the_interaction() -> None:
    built = outdated_client(Gracious)
    interaction = interaction_with(encoded_id_for(Gracious))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.MESSAGE_CREATE
    assert call.kwargs["flags"] is hikari.MessageFlag.EPHEMERAL


async def test_an_inherited_hook_answers_for_the_subclass() -> None:
    built = outdated_client(Heir)
    interaction = interaction_with(encoded_id_for(Heir))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    ((name, _ctx),) = OUTDATED_CALLS
    assert name == "Heir"


async def test_a_signature_edited_in_place_reaches_the_hook_but_stays_loud(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = outdated_client(Gracious)
    forged = encoded_id_for(Gracious, handler=Gracious.vote.token, tail="!!")

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await built._process_interaction(interaction_with(forged))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "bump the handler version" in caplog.text
    assert [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(OUTDATED_CALLS) == 1


async def test_unreadable_frames_never_reach_the_hook(caplog: pytest.LogCaptureFixture) -> None:
    built = outdated_client(Gracious)
    tail = Gracious.vote.signature.fingerprint + '"'
    forged = encoded_id_for(Gracious, handler=Gracious.vote.token, tail=tail)

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(interaction_with(forged))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "unreadable" in caplog.text
    assert not OUTDATED_CALLS


class Shade(enum.Enum):
    LIGHT = 1
    DARK = 2


class ShadeGrown(enum.Enum):
    LIGHT = 1
    DARK = 2
    BLACK = 3


class ShadeWithoutLight(enum.Enum):
    DARK = 2


SHADED_CALLS: list[str] = []


async def shaded_click(cls: type[risa.View], interaction: unittest.mock.Mock) -> risa.GatewayEnabledClient:
    built = risa.client_from_app(gateway_app())
    built.add_view(cls)
    SHADED_CALLS.clear()
    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    return built


@risa.register(name="client-shaded")
class Shaded(risa.View):
    shade: Shade = Shade.LIGHT

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.go))

    @risa.handler
    async def go(self, _ctx: risa.ComponentContext) -> None:
        SHADED_CALLS.append(self.shade.name)


@pytest.fixture
def replaced_shaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_GLOBAL_REGISTRY", registry.Registry())


@pytest.mark.usefixtures("replaced_shaded")
async def test_a_value_added_to_an_enum_field_leaves_old_messages_working() -> None:
    old = interaction_on(Shaded(shade=Shade.DARK), Shaded().render())

    @risa.register(name="client-shaded")
    class Grown(risa.View):
        shade: ShadeGrown = ShadeGrown.LIGHT

        @risa.handler
        async def go(self, _ctx: risa.ComponentContext) -> None:
            SHADED_CALLS.append(self.shade.name)

    await shaded_click(Grown, old)

    assert SHADED_CALLS == ["DARK"]


class ShadeMapped(enum.Enum):
    DARK = 2

    @classmethod
    @typing.override
    def _missing_(cls, value: object) -> ShadeMapped:
        return cls.DARK


@pytest.mark.usefixtures("replaced_shaded")
async def test_an_enum_field_with_missing_maps_a_removed_value_itself() -> None:
    old = interaction_on(Shaded(shade=Shade.LIGHT), Shaded().render())

    @risa.register(name="client-shaded")
    class Mapped(risa.View):
        shade: ShadeMapped = ShadeMapped.DARK

        @risa.handler
        async def go(self, _ctx: risa.ComponentContext) -> None:
            SHADED_CALLS.append(self.shade.name)

    await shaded_click(Mapped, old)

    assert SHADED_CALLS == ["DARK"]


@pytest.mark.usefixtures("replaced_shaded")
async def test_a_value_removed_from_an_enum_field_reaches_the_hook_only_for_messages_that_hold_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    holds_it = interaction_on(Shaded(shade=Shade.LIGHT), Shaded().render())
    holds_another = interaction_on(Shaded(shade=Shade.DARK), Shaded().render())

    @risa.register(name="client-shaded")
    class Shrunk(risa.View):
        shade: ShadeWithoutLight = ShadeWithoutLight.DARK

        @risa.handler
        async def go(self, _ctx: risa.ComponentContext) -> None:
            SHADED_CALLS.append(self.shade.name)

        @classmethod
        @typing.override
        async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
            SHADED_CALLS.append("outdated")

    await shaded_click(Shrunk, holds_another)
    assert SHADED_CALLS == ["DARK"]

    with caplog.at_level(logging.WARNING, logger="risa"):
        await shaded_click(Shrunk, holds_it)

    assert SHADED_CALLS == ["outdated"]
    assert "a value of an enum or Literal field may have been removed" in caplog.text


HOOKED_CALLS: list[str] = []


@risa.register(name="client-hooked")
class Hooked(risa.View):
    @risa.handler
    async def pick(self, _ctx: risa.ComponentContext, weather: Weather) -> None:  # ruff:ignore[no-self-use]
        HOOKED_CALLS.append(weather.name)

    @risa.handler
    async def count(self, _ctx: risa.ComponentContext, amount: int) -> None:  # ruff:ignore[no-self-use]
        HOOKED_CALLS.append(str(amount))

    @risa.handler
    async def pair(self, _ctx: risa.ComponentContext, first: int, second: int) -> None:  # ruff:ignore[no-self-use]
        HOOKED_CALLS.append(f"{first} {second}")

    @risa.handler
    async def mixed(self, _ctx: risa.ComponentContext, weather: Weather, amount: int) -> None:  # ruff:ignore[no-self-use]
        HOOKED_CALLS.append(f"{weather.name} {amount}")

    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        HOOKED_CALLS.append("outdated")


async def click_hooked(token: str, tail: str) -> None:
    HOOKED_CALLS.clear()
    built = risa.client_from_app(gateway_app())
    built.add_view(Hooked)
    await built._process_interaction(interaction_with(encoded_id_for(Hooked, handler=token, tail=tail)))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


async def test_a_bound_enum_value_that_was_removed_reaches_the_hook(caplog: pytest.LogCaptureFixture) -> None:
    gone = codec.pack_frames([codecs.BASE85.encode(codec.IntConverter(int).encode(99))])

    with caplog.at_level(logging.WARNING, logger="risa"):
        await click_hooked(Hooked.pick.token, Hooked.pick.signature.fingerprint + gone)

    assert HOOKED_CALLS == ["outdated"]
    assert "could not be decoded" in caplog.text


async def test_an_argument_default_that_was_removed_reaches_the_hook(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="risa"):
        await click_hooked(Hooked.count.token, Hooked.count.signature.fingerprint)

    assert HOOKED_CALLS == ["outdated"]
    assert "bump the handler version" in caplog.text


async def test_too_few_arguments_reach_the_hook_only_when_the_ones_given_can_be_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    readable = codec.pack_frames([codecs.BASE85.encode(codec.IntConverter(int).encode(1))])
    unreadable = codec.pack_frames([codecs.BASE85.encode(b"")])

    await click_hooked(Hooked.pair.token, Hooked.pair.signature.fingerprint + readable)
    assert HOOKED_CALLS == ["outdated"]

    with caplog.at_level(logging.WARNING, logger="risa"):
        await click_hooked(Hooked.pair.token, Hooked.pair.signature.fingerprint + unreadable)

    assert not HOOKED_CALLS
    assert "could not be decoded" in caplog.text


async def test_a_removed_enum_value_reaches_the_hook_only_when_every_other_argument_can_be_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gone = codecs.BASE85.encode(codec.IntConverter(int).encode(99))
    readable = codecs.BASE85.encode(codec.IntConverter(int).encode(1))
    unreadable = codecs.BASE85.encode(b"")

    await click_hooked(Hooked.mixed.token, Hooked.mixed.signature.fingerprint + codec.pack_frames([gone, readable]))
    assert HOOKED_CALLS == ["outdated"]

    with caplog.at_level(logging.WARNING, logger="risa"):
        await click_hooked(
            Hooked.mixed.token, Hooked.mixed.signature.fingerprint + codec.pack_frames([gone, unreadable])
        )

    assert not HOOKED_CALLS


async def test_a_value_its_converter_cannot_read_never_reaches_the_hook(caplog: pytest.LogCaptureFixture) -> None:
    garbage = codec.pack_frames([codecs.BASE85.encode(b"")])

    with caplog.at_level(logging.WARNING, logger="risa"):
        await click_hooked(Hooked.count.token, Hooked.count.signature.fingerprint + garbage)

    assert not HOOKED_CALLS
    assert "could not be decoded" in caplog.text


async def test_too_many_frames_never_reach_the_hook(caplog: pytest.LogCaptureFixture) -> None:
    one = codecs.BASE85.encode(codec.IntConverter(int).encode(1))

    with caplog.at_level(logging.WARNING, logger="risa"):
        await click_hooked(Hooked.count.token, Hooked.count.signature.fingerprint + codec.pack_frames([one, one]))

    assert not HOOKED_CALLS
    assert "accepts at most" in caplog.text


@risa.register(name="client-counted")
class Counted(risa.View):
    total: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.add, label=str(self.total)))

    @risa.handler
    async def add(self, ctx: risa.ComponentContext) -> None:
        self.total += 1
        await ctx.rerender()


@risa.register(name="client-paid")
class ClientReceipt(risa.View):
    def render(self) -> ui.Layout:  # ruff:ignore[no-self-use]
        return ui.TextDisplay("Paid.")


async def test_building_a_view_this_client_does_not_answer_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    built = risa.client_from_app(gateway_app())

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built.build(Counted())
        await built.build(Counted())

    warned = [record for record in caplog.records if "does not answer its clicks" in record.getMessage()]
    assert len(warned) == 1
    assert "client-counted" in warned[0].getMessage()


@pytest.mark.parametrize("use_global", [False, True])
async def test_a_view_this_client_answers_builds_without_a_warning(
    use_global: bool,  # ruff:ignore[boolean-type-hint-positional-argument]
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = risa.client_from_app(gateway_app(), use_global=use_global)
    if not use_global:
        built.add_view(Counted)

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built.build(Counted())

    assert "does not answer its clicks" not in caplog.text


async def test_a_view_with_nothing_to_click_builds_without_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="risa"):
        await risa.client_from_app(gateway_app()).build(ClientReceipt())

    assert "does not answer its clicks" not in caplog.text


async def test_an_unknown_cookie_never_reaches_any_hook() -> None:
    built = outdated_client(Gracious)

    await built._process_interaction(interaction_with(encoded_id_for(Elsewhere)))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not OUTDATED_CALLS


async def test_a_view_without_defaults_still_reaches_its_hook() -> None:
    built = outdated_client(Brittle)
    interaction = interaction_with(encoded_id_for(Brittle))

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert [name for name, _ctx in OUTDATED_CALLS] == ["Brittle"]


async def test_a_raising_hook_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    built = outdated_client(Sulky)
    interaction = interaction_with(encoded_id_for(Sulky))

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "on_outdated of view client-sulky failed" in caplog.text
    assert len(OUTDATED_CALLS) == 1


@linkd.inject
def peek_database(db: Database = risa.INJECTED) -> Database:
    return db


@risa.register(name="client-injected")
class InjectedOutdated(risa.View):
    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext, db: Database = risa.INJECTED) -> None:
        OUTDATED_CALLS.append((cls.__name__, ctx))
        DI_CALLS.append((db, await peek_database()))


async def test_the_hook_resolves_dependencies_like_a_handler() -> None:
    built = outdated_client(InjectedOutdated)
    database = Database()
    built.di.registry_for(risa.Contexts.DEFAULT).register_value(Database, database)
    DI_CALLS.clear()

    await built._process_interaction(interaction_with(encoded_id_for(InjectedOutdated)))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert len(OUTDATED_CALLS) == 1
    ((injected, nested),) = DI_CALLS
    assert injected is database
    assert nested is database


@risa.register(name="client-ledger", state=risa.InStore(ttl=60.0))
class Ledger(risa.View):
    entries: list[str] = msgspec.field(default_factory=list[str])


@risa.register(name="client-vault", state=risa.InStore(store="redis", ttl=None))
class Vault(risa.View):
    secret: str = ""


@risa.register(name="client-forgetful", state=risa.InStore(ttl=60.0))
class Forgetful(risa.View):
    entries: list[str] = msgspec.field(default_factory=list[str])

    @classmethod
    @typing.override
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay("expired"))


@risa.register(name="client-everywhere")
class Everywhere(risa.View):
    note: str = ""

    @classmethod
    @typing.override
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        await ctx.edit(ui.TextDisplay("expired"))


def backend_of(built: risa.Client, cls: type[risa.View]) -> object:
    return built._backend_for(meta_of(cls))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


def stored_backend_of(built: risa.Client, cls: type[risa.View]) -> stored.StoredBackend:
    found = backend_of(built, cls)
    assert isinstance(found, stored.StoredBackend)
    return found


def test_the_census_records_whether_a_view_answers_for_missing_state() -> None:
    assert not meta_of(Ledger).handles_state_missing
    assert meta_of(Forgetful).handles_state_missing


def test_a_view_that_keeps_its_state_in_the_message_may_still_define_the_hook() -> None:
    assert meta_of(Everywhere).handles_state_missing


def test_a_stored_view_without_a_default_store_names_the_one_line_fix(client: risa.GatewayEnabledClient) -> None:
    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        client.add_view(Ledger)

    assert exc_info.value.view_name == "client-ledger"
    assert "store=risa.MemoryStore()" in exc_info.value.reason


def test_a_stored_view_naming_an_unconfigured_store_names_it() -> None:
    built = risa.client_from_app(gateway_app(), store=risa.MemoryStore())

    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        built.add_view(Vault)

    assert "stores={'redis': ...}" in exc_info.value.reason


def test_store_fills_the_default_slot() -> None:
    store = risa.MemoryStore()
    built = risa.client_from_app(gateway_app(), store=store)

    built.add_view(Ledger)

    assert stored_backend_of(built, Ledger).store is store


def test_stores_adds_named_slots_and_may_name_the_default_one() -> None:
    default, redis = risa.MemoryStore(), risa.MemoryStore()
    built = risa.client_from_app(gateway_app(), stores={"default": default, "redis": redis})

    built.add_view(Ledger)
    built.add_view(Vault)

    assert stored_backend_of(built, Ledger).store is default
    assert stored_backend_of(built, Vault).store is redis


def test_the_default_store_given_both_ways_is_refused() -> None:
    with pytest.raises(ValueError, match="given twice"):
        risa.client_from_app(gateway_app(), store=risa.MemoryStore(), stores={"default": risa.MemoryStore()})


def test_a_blank_key_prefix_is_refused() -> None:
    with pytest.raises(ValueError, match="key_prefix"):
        risa.client_from_app(gateway_app(), key_prefix="  ")


def test_key_prefix_reaches_the_store_keys() -> None:
    built = risa.client_from_app(gateway_app(), store=risa.MemoryStore(), key_prefix="modbot")
    clicked = unittest.mock.Mock(spec=hikari.ComponentInteraction, application_id=hikari.Snowflake(4242))

    key = stored_backend_of(built, Ledger).namespace.for_click("aa", clicked)

    assert key == "modbot:1:aa"


def test_a_memory_store_behind_a_stored_view_warns_once_per_slot(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    built = risa.client_from_app(gateway_app(), store=risa.MemoryStore())

    built.add_view(Ledger)
    built.add_view(Forgetful)

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "MemoryStore" in warnings[0].getMessage()


def test_a_store_of_the_users_own_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    built = risa.client_from_app(gateway_app(), store=unittest.mock.Mock(spec=risa.Store))

    built.add_view(Ledger)

    assert not [record for record in caplog.records if record.levelno == logging.WARNING]


def test_a_memory_store_nobody_keeps_state_in_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="risa")
    built = risa.client_from_app(gateway_app(), store=risa.MemoryStore())

    built.add_view(Panel)

    assert not [record for record in caplog.records if record.levelno == logging.WARNING]


def test_each_view_gets_the_backend_its_state_calls_for() -> None:
    built = risa.client_from_app(gateway_app(), store=risa.MemoryStore(), use_global=True)

    assert isinstance(backend_of(built, Panel), message.MessageBackend)
    assert isinstance(backend_of(built, Elsewhere), stateless.StatelessBackend)
    assert isinstance(backend_of(built, Ledger), stored.StoredBackend)
    assert backend_of(built, Vault) is None


def test_the_lightbulb_factory_takes_the_store_options() -> None:
    store = risa.MemoryStore()
    built = risa.client_from_lightbulb(typing.cast("risa.LightbulbClient", LightbulbStub(gateway_app())), store=store)

    built.add_view(Ledger)

    assert stored_backend_of(built, Ledger).store is store


STATE_CALLS: list[str] = []
KEPT: list[risa.ComponentContext] = []
RIVAL_WRITES: list[collections.abc.Callable[[], collections.abc.Awaitable[None]]] = []


@risa.register(name="client-tally")
class Tally(risa.View):
    counts: list[int] = msgspec.field(default_factory=lambda: [0, 0])
    loaded: risa.Prop[str] = ""

    def render(self) -> ui.Layout:
        return ui.Container(
            ui.TextDisplay(f"{self.counts} {self.loaded}"),
            ui.Row(
                ui.Button(risa.bind(self.vote, 0)),
                ui.Button(risa.bind(self.vote, 1)),
                ui.Button(self.quiet),
                ui.Button(self.boom),
                ui.Button(self.keep),
            ),
        )

    @typing.override
    async def load(self) -> None:
        self.loaded = "fresh"

    @risa.handler
    async def vote(self, ctx: risa.ComponentContext, option: int) -> None:
        self.counts[option] += 1
        await ctx.rerender()

    @risa.handler
    async def quiet(self, _ctx: risa.ComponentContext) -> None:
        self.counts[0] += 10

    @risa.handler
    async def boom(self, _ctx: risa.ComponentContext) -> None:
        self.counts[0] += 100
        msg = "handler gave up"
        raise RuntimeError(msg)

    @risa.handler
    async def keep(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        KEPT.append(ctx)


@risa.register(name="client-unloadable")
class Unloadable(risa.View):
    note: str = ""

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.press))

    @typing.override
    async def load(self) -> None:
        msg = "the database is down"
        raise RuntimeError(msg)

    @risa.handler
    async def press(self, _ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        STATE_CALLS.append("press")


@risa.register(name="client-notebook", state=risa.InStore(ttl=60.0))
class Notebook(risa.View):
    entries: list[str] = msgspec.field(default_factory=list[str])

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(risa.bind(self.add, "milk")), ui.Button(self.jot))

    @risa.handler
    async def add(self, ctx: risa.ComponentContext, text: str) -> None:
        self.entries.append(text)
        await ctx.rerender()

    @risa.handler
    async def jot(self, _ctx: risa.ComponentContext) -> None:
        self.entries.append("mine")
        for write in RIVAL_WRITES:
            await write()

    @risa.handler
    async def whisper(self, ctx: risa.ComponentContext) -> None:
        self.entries.append("whispered")
        await ctx.respond("noted", ephemeral=True)

    @classmethod
    @typing.override
    async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
        STATE_CALLS.append("missing")
        await ctx.edit(ui.TextDisplay("expired"))


@risa.register(name="client-diary", state=risa.InStore(ttl=60.0))
class Diary(risa.View):
    entries: list[str] = msgspec.field(default_factory=list[str])

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.read))

    @risa.handler
    async def read(self, _ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        STATE_CALLS.append("read")


class DownStore(risa.MemoryStore):
    __slots__ = ()

    @typing.override
    async def get_versioned(self, key: str) -> risa.VersionedEntry | None:
        msg = "redis went away"
        raise ConnectionError(msg)


@risa.register(name="client-receipt", state=risa.InStore(ttl=60.0))
class Receipt(risa.View):
    total: int = 0

    def render(self) -> ui.Layout:
        return ui.TextDisplay(f"total: {self.total}")


class CountingStore(risa.MemoryStore):
    __slots__ = ("touches",)

    def __init__(self) -> None:
        super().__init__()
        self.touches = 0

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        self.touches += 1
        await super().touch(key, ttl=ttl)


class StuckUnlock:
    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, *exc: object) -> None:
        msg = "redis went away during unlock"
        raise ConnectionError(msg)


class StuckStore(risa.MemoryStore):
    __slots__ = ()

    @typing.override
    def lock(
        self,
        key: str,
        *,
        timeout: float = risa.DEFAULT_LOCK_TIMEOUT,
    ) -> contextlib.AbstractAsyncContextManager[None]:
        return StuckUnlock()


class UntouchableStore(risa.MemoryStore):
    __slots__ = ()

    @typing.override
    async def touch(self, key: str, *, ttl: float) -> None:
        msg = "redis went away during touch"
        raise ConnectionError(msg)


def ids_of(rendered: collections.abc.Sequence[hikari.api.ComponentBuilder]) -> list[str]:
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            entries = typing.cast("dict[str, object]", node)
            if isinstance(raw := entries.get("custom_id"), str):
                found.append(raw)
            for value in entries.values():
                walk(value)
        elif isinstance(node, list):
            for value in typing.cast("list[object]", node):
                walk(value)

    for builder in rendered:
        walk(builder.build()[0])
    return found


def click(ids: list[str], index: int) -> unittest.mock.Mock:
    interaction = interaction_with(ids[index])
    interaction.message.components = [
        hikari.ActionRowComponent(
            type=hikari.ComponentType.ACTION_ROW,
            id=0,
            components=[
                hikari.ButtonComponent(
                    type=hikari.ComponentType.BUTTON,
                    id=0,
                    style=hikari.ButtonStyle.PRIMARY,
                    label=None,
                    emoji=None,
                    custom_id=raw,
                    url=None,
                    is_disabled=False,
                )
                for raw in ids
            ],
        ),
    ]
    return interaction


def restored_tally(builders: collections.abc.Sequence[hikari.api.ComponentBuilder]) -> list[int]:
    meta = registry.require_meta(Tally)
    found = reader.fragments(click(ids_of(builders), 0).message.components, cookie=meta.cookie)
    assert isinstance(found, dict)
    raw = anchor.gather(found)
    assert raw is not None
    parsed = anchor.parse(raw)
    assert isinstance(parsed, anchor.MessageAnchor)
    values = meta.schema.unpack(parsed.schema_fp, parsed.payload)
    assert values is not None
    return typing.cast("list[int]", values[0])


def updated_with(built: risa.Client) -> collections.abc.Sequence[hikari.api.ComponentBuilder]:
    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.MESSAGE_UPDATE
    return typing.cast("collections.abc.Sequence[hikari.api.ComponentBuilder]", call.kwargs["components"])


def text_of(builders: collections.abc.Sequence[hikari.api.ComponentBuilder]) -> str:
    payload, _attachments = builders[0].build()
    return typing.cast("str", payload["components"][0]["content"])


def stored_client(
    store: risa.Store | None = None,
    *,
    auto_defer: risa.AutoDefer = risa.AutoDefer.OFF,
    auto_defer_delay: float = constants.AUTO_DEFER_DELAY,
) -> risa.GatewayEnabledClient:
    built = risa.client_from_app(
        gateway_app(),
        store=store or risa.MemoryStore(),
        key_prefix="test",
        auto_defer=auto_defer,
        auto_defer_delay=auto_defer_delay,
    )
    built.add_view(Notebook)
    built.add_view(Diary)
    return built


async def test_build_embeds_the_state_and_runs_load_first() -> None:
    built = risa.client_from_app(gateway_app())

    rendered = await built.build(Tally(counts=[5, 2]))

    assert restored_tally(rendered.components) == [5, 2]
    assert text_of(rendered.components) == "[5, 2] fresh"


async def test_a_click_hydrates_self_from_the_message_and_rerender_writes_it_back() -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Tally)
    ids = ids_of((await built.build(Tally(counts=[3, 1]))).components)

    await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert restored_tally(updated_with(built)) == [4, 1]
    assert text_of(updated_with(built)) == "[4, 1] fresh"


async def test_a_mutation_without_rerender_is_committed_by_one() -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Tally)
    ids = ids_of((await built.build(Tally(counts=[3, 1]))).components)

    await built._process_interaction(click(ids, 2))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert restored_tally(updated_with(built)) == [13, 1]
    rest_of(built).create_interaction_response.assert_awaited_once()


async def test_a_raising_handler_commits_nothing() -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Tally)
    ids = ids_of((await built.build(Tally(counts=[3, 1]))).components)

    await built._process_interaction(click(ids, 3))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    rest_of(built).create_interaction_response.assert_not_called()


async def test_a_failing_load_keeps_the_handler_from_running(caplog: pytest.LogCaptureFixture) -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Unloadable)
    ids = ids_of(
        build_.plan(Unloadable().render(), registry.require_meta(Unloadable)).emit(
            message.MessageBackend().first_anchor(meta=registry.require_meta(Unloadable), view=Unloadable()),
        )
    )
    STATE_CALLS.clear()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not STATE_CALLS
    assert "load() of view client-unloadable failed" in caplog.text
    assert "never ran" in caplog.text


async def test_build_of_a_stored_view_writes_its_record_first() -> None:
    store = risa.MemoryStore()
    built = stored_client(store)

    ids = ids_of((await built.build(Notebook(entries=["eggs"]))).components)

    fragment = codecs.BASE85.decode(codec.parse_custom_id(ids[0]).fragment)  # type: ignore[union-attr]
    located = anchor.StoreAnchor.parse(fragment)
    assert located is not None
    assert await store.get_versioned(f"test:1:{located.token}") is not None


async def test_build_of_a_view_whose_store_is_missing_names_the_fix() -> None:
    built = risa.client_from_app(gateway_app())

    with pytest.raises(risa.ViewDeclarationError) as exc_info:
        await built.build(Diary())

    assert "store=risa.MemoryStore()" in exc_info.value.reason


async def test_a_stored_view_round_trips_through_the_store() -> None:
    store = risa.MemoryStore()
    built = stored_client(store)
    ids = ids_of((await built.build(Notebook(entries=["eggs"]))).components)

    await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    (stored_key,) = list(store._entries)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    entry = await store.get_versioned(stored_key)
    assert entry is not None
    assert entry.version == 2
    assert ids_of(updated_with(built)) == ids


async def test_state_gone_from_the_store_reaches_on_state_missing(caplog: pytest.LogCaptureFixture) -> None:
    store = risa.MemoryStore()
    built = stored_client(store, auto_defer=risa.AutoDefer.UPDATE, auto_defer_delay=10.0)
    ids = ids_of((await built.build(Notebook())).components)
    for key in list(store._entries):  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
        await store.delete(key)
    STATE_CALLS.clear()
    caplog.clear()

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert STATE_CALLS == ["missing"]
    payload, _attachments = updated_with(built)[0].build()
    assert payload["content"] == "expired"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_state_gone_from_a_view_without_the_hook_warns_and_answers_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = risa.MemoryStore()
    built = stored_client(store, auto_defer=risa.AutoDefer.UPDATE, auto_defer_delay=10.0)
    ids = ids_of((await built.build(Diary())).components)
    for key in list(store._entries):  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
        await store.delete(key)
    STATE_CALLS.clear()

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not STATE_CALLS
    assert "override on_state_missing" in caplog.text
    rest_of(built).create_interaction_response.assert_not_called()


async def test_a_store_outage_is_an_error_and_reaches_no_hook(caplog: pytest.LogCaptureFixture) -> None:
    store = DownStore()
    built = stored_client(store)
    ids = ids_of((await built.build(Notebook())).components)
    STATE_CALLS.clear()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not STATE_CALLS
    assert "could not be reached" in caplog.text
    assert "ConnectionError" in caplog.text


async def test_a_global_view_whose_store_is_missing_is_an_error_at_click(caplog: pytest.LogCaptureFixture) -> None:
    ids = ids_of((await stored_client().build(Diary())).components)
    built = risa.client_from_app(gateway_app(), use_global=True)
    STATE_CALLS.clear()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not STATE_CALLS
    assert "no store of that name" in caplog.text


async def test_a_race_lost_by_the_end_of_dispatch_commit_is_logged_and_discarded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = risa.MemoryStore()
    built = stored_client(store)
    ids = ids_of((await built.build(Notebook())).components)
    (key,) = list(store._entries)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    async def rival() -> None:
        await store.put(key, b"another process", ttl=60.0)

    RIVAL_WRITES[:] = [rival]
    try:
        with caplog.at_level(logging.WARNING, logger="risa"):
            await built._process_interaction(click(ids, 1))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    finally:
        RIVAL_WRITES.clear()

    assert "the change was discarded" in caplog.text
    entry = await store.get_versioned(key)
    assert entry is not None
    assert entry.data == b"another process"


async def test_a_failing_unlock_after_a_good_click_still_acknowledges(caplog: pytest.LogCaptureFixture) -> None:
    built = stored_client(StuckStore(), auto_defer=risa.AutoDefer.UPDATE, auto_defer_delay=10.0)
    ids = ids_of((await built.build(Diary())).components)
    STATE_CALLS.clear()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert STATE_CALLS == ["read"]
    assert "releasing the state of view client-diary" in caplog.text
    assert "but its state could not be reached" not in caplog.text
    call = rest_of(built).create_interaction_response.await_args
    assert call is not None
    assert call.args[2] is hikari.ResponseType.DEFERRED_MESSAGE_UPDATE


async def test_a_leftover_commit_on_an_ephemeral_origin_writes_nothing_and_says_why(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = risa.MemoryStore()
    built = stored_client(store)
    ids = ids_of((await built.build(Notebook())).components)
    (key,) = list(store._entries)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    interaction = click(ids, 0)
    interaction.custom_id = ids_of(
        build_.plan(ui.Row(ui.Button(Notebook().whisper)), registry.require_meta(Notebook)).emit(
            codecs.BASE85.decode(codec.parse_custom_id(ids[0]).fragment),  # type: ignore[union-attr]
        )
    )[0]
    interaction.message.flags = hikari.MessageFlag.EPHEMERAL
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "origin message is ephemeral" in caplog.text
    entry = await store.get_versioned(key)
    assert entry is not None
    assert entry.version == 1


async def test_a_failing_ttl_refresh_is_reported_as_what_it_is(caplog: pytest.LogCaptureFixture) -> None:
    built = stored_client(UntouchableStore())
    ids = ids_of((await built.build(Diary())).components)
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "releasing the state of view client-diary" in caplog.text
    assert "mutated durable state" not in caplog.text


async def test_a_stored_view_with_nothing_to_click_writes_no_record() -> None:
    store = risa.MemoryStore()
    built = risa.client_from_app(gateway_app(), store=store, key_prefix="test")
    built.add_view(Receipt)

    rendered = await built.build(Receipt(total=12))

    assert not ids_of(rendered.components)
    assert not store._entries  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


async def test_a_dispatch_refreshes_the_ttl_once() -> None:
    store = CountingStore()
    built = stored_client(store)
    notebook_ids = ids_of((await built.build(Notebook())).components)
    diary_ids = ids_of((await built.build(Diary())).components)

    await built._process_interaction(click(notebook_ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    assert store.touches == 0
    await built._process_interaction(click(diary_ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    assert store.touches == 1


async def test_a_failed_leftover_commit_still_refreshes_the_ttl() -> None:
    store = CountingStore()
    built = stored_client(store)
    ids = ids_of((await built.build(Notebook())).components)
    interaction = click(ids, 0)
    interaction.custom_id = ids_of(
        build_.plan(ui.Row(ui.Button(Notebook().whisper)), registry.require_meta(Notebook)).emit(
            codecs.BASE85.decode(codec.parse_custom_id(ids[0]).fragment),  # type: ignore[union-attr]
        )
    )[0]
    interaction.message.flags = hikari.MessageFlag.EPHEMERAL

    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert store.touches == 1


async def test_a_context_that_outlives_its_dispatch_cannot_commit() -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Tally)
    ids = ids_of((await built.build(Tally())).components)
    KEPT.clear()

    await built._process_interaction(click(ids, 4))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    (kept,) = KEPT
    with pytest.raises(risa.NoViewError) as exc_info:
        await kept.rerender()
    assert "already ended" in str(exc_info.value)


async def test_a_stored_view_with_nothing_to_click_builds_without_its_store() -> None:
    built = risa.client_from_app(gateway_app())

    rendered = await built.build(Receipt(total=3))

    assert not ids_of(rendered.components)


@risa.register(name="client-retired", version=2)
class RetiredPoll(risa.View):
    votes: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.vote))

    @risa.handler
    async def vote(self, ctx: risa.ComponentContext) -> None:
        await ctx.respond(f"ended with {self.votes}")


@risa.register(name="client-retired", version=3)
class CurrentPoll(risa.View):
    votes: list[int] = msgspec.field(default_factory=list[int])
    outdated: typing.ClassVar[list[str]] = []

    @risa.handler
    async def vote(self, ctx: risa.ComponentContext) -> None:
        await ctx.respond(f"version 3 answered with {self.votes}")

    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        cls.outdated.append("ran")


async def test_a_class_kept_at_the_old_version_answers_clicks_on_old_components() -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(CurrentPoll)
    built.add_view(RetiredPoll)
    view = RetiredPoll(votes=3)

    await built._process_interaction(interaction_on(view, view.render()))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert rest_of(built).create_interaction_response.await_args.args[3] == "ended with 3"
    assert not CurrentPoll.outdated


async def test_without_the_old_class_a_click_reaches_no_view_and_no_hook(caplog: pytest.LogCaptureFixture) -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(CurrentPoll)
    view = RetiredPoll(votes=3)

    with caplog.at_level(logging.DEBUG, logger="risa.dispatch"):
        await built._process_interaction(interaction_on(view, view.render()))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    rest_of(built).create_interaction_response.assert_not_awaited()
    assert not CurrentPoll.outdated
    assert "this client has no view for it" in caplog.text


@risa.register(name="client-replaced-from-hook")
class ReplacedFromHook(risa.View):
    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        await ctx.replace(Counted())


REPLACED_CALLS: list[str] = []


@risa.register(name="client-replacement", state=risa.InStore(ttl=60))
class Replacement(risa.View):
    total: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.add, label=f"Total: {self.total}"))

    @risa.handler
    async def add(self, ctx: risa.ComponentContext) -> None:
        REPLACED_CALLS.append(f"replacement at {self.total}")
        await ctx.acknowledge()


@risa.register(name="client-replacing", state=risa.InStore(ttl=60))
class Replacing(risa.View):
    voters: list[int] = msgspec.field(default_factory=list[int])

    def render(self) -> ui.Layout:
        return ui.Row(
            ui.Button(self.plain),
            ui.Button(self.after_thinking),
            ui.Button(self.after_respond),
            ui.Button(self.changed_itself),
            ui.Button(self.then_rerenders),
        )

    @risa.handler
    async def plain(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.replace(Replacement(total=1))

    @risa.handler
    async def after_thinking(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.defer(thinking=True)
        await ctx.replace(Replacement(total=2))

    @risa.handler
    async def after_respond(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.respond("hi")
        await ctx.replace(Replacement(total=3))

    @risa.handler
    async def changed_itself(self, ctx: risa.ComponentContext) -> None:
        self.voters.append(5)
        await ctx.replace(Replacement(total=4))

    @risa.handler
    async def then_rerenders(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.replace(Replacement(total=5))
        with pytest.raises(risa.NoViewError):
            await ctx.rerender()
        REPLACED_CALLS.append("rerender refused")


async def replacing_client() -> tuple[risa.Client, risa.MemoryStore, list[str]]:
    store = risa.MemoryStore()
    built = stored_client(store)
    built.add_view(Replacing)
    built.add_view(Replacement)
    REPLACED_CALLS.clear()
    return built, store, ids_of((await built.build(Replacing(voters=[1]))).components)


def answered_labels(call: unittest.mock._Call) -> str:  # type: ignore[reportPrivateUsage]
    return str([builder.build()[0] for builder in call.kwargs["components"]])


async def test_replace_turns_the_message_into_another_view_that_then_answers() -> None:
    built, _, ids = await replacing_client()

    await built._process_interaction(click(ids, 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    answer = rest_of(built).create_interaction_response.await_args
    assert answer.args[2] is hikari.ResponseType.MESSAGE_UPDATE
    assert "Total: 1" in answered_labels(answer)

    await built._process_interaction(click(ids_of(answer.kwargs["components"]), 0))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert REPLACED_CALLS == ["replacement at 1"]


@pytest.mark.parametrize(("button", "expected"), [(1, "Total: 2"), (2, "Total: 3")])
async def test_replace_lands_on_the_clicked_message_after_another_answer(button: int, expected: str) -> None:
    built, _, ids = await replacing_client()
    rest_of(built).execute_webhook.return_value = unittest.mock.Mock(id=hikari.Snowflake(2))

    await built._process_interaction(click(ids, button))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert expected in answered_labels(rest_of(built).edit_message.await_args)


async def test_replace_leaves_the_old_state_as_it_was_and_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    built, store, ids = await replacing_client()
    (old_key,) = list(store._entries)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    before = await store.get_versioned(old_key)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="risa"):
        await built._process_interaction(click(ids, 3))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not caplog.records
    after = await store.get_versioned(old_key)
    assert before is not None
    assert after is not None
    assert after.data == before.data
    assert len(store._entries) == 2  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


async def test_a_rerender_after_replace_is_refused() -> None:
    built, _, ids = await replacing_client()

    await built._process_interaction(click(ids, 4))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert REPLACED_CALLS == ["rerender refused"]


async def test_replace_runs_from_on_outdated() -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(ReplacedFromHook)
    built.add_view(Counted)

    await built._process_interaction(interaction_with(encoded_id_for(ReplacedFromHook, handler="zz")))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "'label': '0'" in answered_labels(rest_of(built).create_interaction_response.await_args)


AGAIN_SEEN: list[str] = []


@risa.register(name="client-again")
class Again(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(
            ui.Button(self.add, label=str(self.count)),
            ui.Button(self.reset),
            ui.Button(self.away),
            ui.Button(self.show),
        )

    @risa.handler
    async def add(self, ctx: risa.ComponentContext) -> None:
        self.count += 1
        await ctx.rerender()

    @risa.handler
    async def reset(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.replace(Again(count=50))

    @risa.handler
    async def away(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.replace(Away())

    @risa.handler
    async def show(self, _ctx: risa.ComponentContext) -> None:
        AGAIN_SEEN.append(f"count {self.count}")


@risa.register(name="client-away")
class Away(risa.View):
    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.back), ui.Button(self.then_rerender), ui.Button(self.then_edit))

    @risa.handler
    async def back(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.replace(Again(count=100))

    @risa.handler
    async def then_rerender(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.replace(Again())
        with pytest.raises(risa.NoViewError):
            await ctx.rerender()
        AGAIN_SEEN.append("rerender refused")

    @risa.handler
    async def then_edit(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.replace(Again())
        with pytest.raises(risa.NoViewError):
            await ctx.edit(ui.TextDisplay("Closed."))
        AGAIN_SEEN.append("edit refused")


async def again_client() -> tuple[risa.Client, list[str]]:
    built = risa.client_from_app(gateway_app())
    built.add_view(Again)
    built.add_view(Away)
    AGAIN_SEEN.clear()
    return built, ids_of((await built.build(Again())).components)


async def click_and_follow(built: risa.Client, ids: list[str], index: int) -> list[str]:
    await built._process_interaction(click(ids, index))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    return ids_of(rest_of(built).create_interaction_response.await_args.kwargs["components"])


async def test_replace_with_the_same_view_shows_the_new_state_on_the_next_click() -> None:
    built, ids = await again_client()

    ids = await click_and_follow(built, ids, 0)
    ids = await click_and_follow(built, ids, 1)
    await click_and_follow(built, ids, 3)

    assert AGAIN_SEEN == ["count 50"]


async def test_a_view_that_comes_back_through_replace_shows_its_new_state() -> None:
    built, ids = await again_client()

    ids = await click_and_follow(built, ids, 0)
    ids = await click_and_follow(built, ids, 2)
    ids = await click_and_follow(built, ids, 0)
    await click_and_follow(built, ids, 3)

    assert AGAIN_SEEN == ["count 100"]


@pytest.mark.parametrize(("button", "expected"), [(1, "rerender refused"), (2, "edit refused")])
async def test_the_view_that_called_replace_cannot_answer_again(button: int, expected: str) -> None:
    built, ids = await again_client()

    ids = await click_and_follow(built, ids, 2)
    await built._process_interaction(click(ids, button))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert [expected] == AGAIN_SEEN


FAILURES: list[str] = []


@risa.register(name="client-erroring")
class Erroring(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.boom), ui.Button(self.answer_then_boom))

    @risa.handler
    async def boom(self, _ctx: risa.ComponentContext) -> None:
        self.count += 1
        msg = "boom"
        raise RuntimeError(msg)

    @risa.handler
    async def answer_then_boom(self, ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        await ctx.respond("first")
        msg = "late"
        raise RuntimeError(msg)


@risa.register(name="client-erroring-load")
class FaultyLoad(risa.View):
    count: int = 0

    async def load(self) -> None:  # ruff:ignore[no-self-use]
        msg = "load"
        raise RuntimeError(msg)

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.go))

    @risa.handler
    async def go(self, _ctx: risa.ComponentContext) -> None:  # ruff:ignore[no-self-use]
        FAILURES.append("handler ran")


@risa.register(name="client-erroring-hook")
class FaultyHook(risa.View):
    @classmethod
    @typing.override
    async def on_outdated(cls, ctx: risa.ComponentContext) -> None:
        msg = "hook"
        raise RuntimeError(msg)


def erroring_client(
    *views: type[risa.View],
    store: risa.Store | None = None,
    use_global: bool = False,
    auto_defer: risa.AutoDefer = risa.AutoDefer.OFF,
) -> risa.GatewayEnabledClient:
    built = risa.client_from_app(
        gateway_app(),
        store=store,
        key_prefix="test",
        use_global=use_global,
        auto_defer=auto_defer,
        auto_defer_delay=0.01,
    )
    for view in views:
        built.add_view(view)
    FAILURES.clear()
    return built


def erroring_click(button: int = 0) -> unittest.mock.Mock:
    return interaction_on(Erroring(), Erroring().render(), clicked=button)


async def process(built: risa.Client, interaction: unittest.mock.Mock) -> None:
    await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]


async def test_an_error_handler_answers_a_failed_handler_and_nothing_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    built = erroring_client(Erroring)

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:
        FAILURES.append(f"{failure.stage} {type(failure.error).__name__}")
        await failure.ctx.respond("Something went wrong.", ephemeral=True)
        return True

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await process(built, erroring_click())

    assert FAILURES == ["handler RuntimeError"]
    assert rest_of(built).create_interaction_response.await_args.args[3] == "Something went wrong."
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


async def test_a_failure_no_error_handler_takes_is_logged_as_before(caplog: pytest.LogCaptureFixture) -> None:
    built = erroring_client(Erroring)

    @built.error_handler
    async def on_error(_failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append("asked")
        return False

    with caplog.at_level(logging.ERROR, logger="risa"):
        await process(built, erroring_click())

    assert FAILURES == ["asked"]
    assert "handler 'boom' (version 1) of view client-erroring failed" in caplog.text


async def test_error_handlers_run_by_priority_then_in_order_until_one_handles() -> None:
    built = erroring_client(Erroring)

    def handler(name: str, *, handles: bool) -> collections.abc.Callable[[risa.ClickFailure], typing.Awaitable[bool]]:
        async def on_error(_failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
            FAILURES.append(name)
            return handles

        return on_error

    built.error_handler(handler("low", handles=True), priority=-1)
    built.error_handler(handler("first", handles=False))
    built.error_handler(handler("high", handles=False), priority=5)
    built.error_handler(handler("second", handles=True))
    built.error_handler(handler("never", handles=True))

    await process(built, erroring_click())

    assert FAILURES == ["high", "first", "second"]


async def test_an_error_handler_that_raises_is_logged_and_passes_on(caplog: pytest.LogCaptureFixture) -> None:
    built = erroring_client(Erroring)

    @built.error_handler(priority=1)
    async def broken(_failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        msg = "in the error handler"
        raise ValueError(msg)

    @built.error_handler
    async def fallback(_failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append("fallback")
        return False

    with caplog.at_level(logging.ERROR, logger="risa"):
        await process(built, erroring_click())

    assert FAILURES == ["fallback"]
    assert "ValueError: in the error handler" in caplog.text
    assert "handler 'boom' (version 1) of view client-erroring failed" in caplog.text


async def test_an_error_handler_cannot_save_the_state_of_the_failed_view() -> None:
    built = erroring_client(Erroring)

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:
        with pytest.raises(risa.NoViewError):
            await failure.ctx.rerender()
        with pytest.raises(risa.NoViewError):
            await failure.ctx.edit(ui.Row(ui.Button(Erroring().boom)))
        await failure.ctx.edit(ui.TextDisplay("This view broke."))
        FAILURES.append("closed")
        return True

    await process(built, erroring_click())

    assert FAILURES == ["closed"]
    assert rest_of(built).create_interaction_response.await_args.args[2] is hikari.ResponseType.MESSAGE_UPDATE


async def test_an_error_handler_after_an_answer_follows_up() -> None:
    built = erroring_client(Erroring)
    rest_of(built).execute_webhook.return_value = unittest.mock.Mock(id=hikari.Snowflake(2))

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:
        await failure.ctx.respond("That went wrong after all.")
        return True

    await process(built, erroring_click(1))

    assert rest_of(built).execute_webhook.await_args.args[2] == "That went wrong after all."


async def test_a_failing_load_reaches_the_error_handler() -> None:
    built = erroring_client(FaultyLoad)

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append(f"{failure.stage} {failure.error}")
        return True

    await process(built, interaction_on(FaultyLoad(), FaultyLoad().render()))

    assert FAILURES == ["load load"]


async def test_a_failing_hook_reaches_the_error_handler(caplog: pytest.LogCaptureFixture) -> None:
    built = erroring_client(FaultyHook)

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append(f"{failure.stage} {failure.error}")
        return True

    with caplog.at_level(logging.DEBUG, logger="risa"):
        await process(built, interaction_with(encoded_id_for(FaultyHook, handler="zz")))

    assert FAILURES == ["on_outdated hook"]
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


async def test_state_out_of_reach_reaches_the_error_handler(caplog: pytest.LogCaptureFixture) -> None:
    built = erroring_client(Notebook, store=DownStore())
    ids = ids_of((await stored_client().build(Notebook())).components)

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:
        FAILURES.append(f"{failure.stage} {type(failure.error).__name__}")
        await failure.ctx.respond("Try again in a moment.", ephemeral=True)
        return True

    with caplog.at_level(logging.ERROR, logger="risa"):
        await process(built, click(ids, 1))

    assert FAILURES == ["state StoreUnavailableError"]
    assert "could not be reached" not in caplog.text


async def test_a_store_the_client_lacks_reaches_the_error_handler() -> None:
    built = erroring_client(use_global=True)
    ids = ids_of((await stored_client().build(Notebook())).components)

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append(f"{failure.stage} {type(failure.error).__name__}")
        return True

    await process(built, click(ids, 1))

    assert FAILURES == ["state ViewDeclarationError"]


async def test_an_error_handler_gets_its_dependencies_injected() -> None:
    built = erroring_client(Erroring)
    database = Database()
    built.di.registry_for(risa.Contexts.DEFAULT).register_value(Database, database)

    @built.error_handler
    async def on_error(_failure: risa.ClickFailure, db: Database) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append("injected" if db is database else "not injected")
        return True

    await process(built, erroring_click())

    assert FAILURES == ["injected"]


def test_error_handler_returns_the_function_in_both_forms() -> None:
    built = erroring_client()

    async def on_error(_failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        return True

    assert built.error_handler(on_error) is on_error
    assert built.error_handler(priority=2)(on_error) is on_error


class Teardown:
    __slots__ = ()


async def broken_teardown(_: Teardown) -> None:  # ruff:ignore[unused-async]
    msg = "teardown failed"
    raise RuntimeError(msg)


def with_teardown(built: risa.Client) -> risa.Client:
    built.di.registry_for(risa.Contexts.COMPONENT).register_factory(Teardown, Teardown, teardown=broken_teardown)
    return built


@risa.register(name="client-torn")
class Torn(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(self.add), ui.Button(self.add_and_fail))

    @risa.handler
    async def add(self, ctx: risa.ComponentContext, _torn: Teardown) -> None:
        self.count += 1
        await ctx.rerender()

    @risa.handler
    async def add_and_fail(self, _ctx: risa.ComponentContext, _torn: Teardown) -> None:
        self.count += 1
        msg = "boom"
        raise RuntimeError(msg)


async def test_a_failure_after_the_handler_stays_out_of_the_error_handlers(caplog: pytest.LogCaptureFixture) -> None:
    built = with_teardown(erroring_client(Torn))

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append(f"{failure.stage} {failure.error}")
        return True

    with caplog.at_level(logging.ERROR, logger="risa"):
        await process(built, interaction_on(Torn(), Torn().render()))
        await process(built, interaction_on(Torn(), Torn().render(), clicked=1))

    assert FAILURES == ["handler boom"]
    assert "RuntimeError: teardown failed" in caplog.text


async def test_a_failing_on_state_missing_reaches_the_error_handler() -> None:
    @risa.register(name="client-missing-hook", state=risa.InStore(ttl=60))
    class MissingHook(risa.View):
        count: int = 0

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.go))

        @risa.handler
        async def go(self, _ctx: risa.ComponentContext) -> None: ...

        @classmethod
        @typing.override
        async def on_state_missing(cls, ctx: risa.ComponentContext) -> None:
            msg = "gone"
            raise RuntimeError(msg)

    sender = erroring_client(MissingHook, store=risa.MemoryStore())
    ids = ids_of((await sender.build(MissingHook())).components)
    built = erroring_client(MissingHook, store=risa.MemoryStore())

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append(f"{failure.stage} {failure.error}")
        return True

    await process(built, click(ids, 0))

    assert FAILURES == ["on_state_missing gone"]


async def test_the_watchdog_stands_down_before_the_error_handler_answers() -> None:
    built = erroring_client(Erroring, auto_defer=risa.AutoDefer.UPDATE)

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:
        await asyncio.sleep(0.05)
        await failure.ctx.respond("Something went wrong.", ephemeral=True)
        return True

    await process(built, erroring_click())

    responses = [call.args[2] for call in rest_of(built).create_interaction_response.await_args_list]
    assert responses == [hikari.ResponseType.MESSAGE_CREATE]


class PerClick:
    __slots__ = ()


async def test_an_error_handler_shares_the_dependencies_of_the_failed_handler() -> None:
    made: list[PerClick] = []

    def make() -> PerClick:
        made.append(PerClick())
        return made[-1]

    @risa.register(name="client-sharing")
    class Sharing(risa.View):
        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.go))

        @risa.handler
        async def go(self, _ctx: risa.ComponentContext, per_click: PerClick) -> None:  # ruff:ignore[no-self-use]
            FAILURES.append(str(id(per_click)))
            msg = "boom"
            raise RuntimeError(msg)

    built = erroring_client(Sharing)
    built.di.registry_for(risa.Contexts.COMPONENT).register_factory(PerClick, make)

    @built.error_handler
    async def on_error(_failure: risa.ClickFailure, per_click: PerClick) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append(str(id(per_click)))
        return True

    await process(built, interaction_on(Sharing(), Sharing().render()))

    assert len(made) == 1
    assert FAILURES[0] == FAILURES[1]


async def test_a_component_written_in_a_codec_that_is_not_installed_reaches_the_error_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @risa.register(name="client-dense-args", state=risa.InMessage(codec=codecs.BASE32768))
    class DenseArgs(risa.View):
        count: int = 0

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(risa.bind(self.pick, 3)))

        @risa.handler
        async def pick(self, _ctx: risa.ComponentContext, amount: int) -> None:  # ruff:ignore[no-self-use]
            FAILURES.append(str(amount))

    interaction = interaction_on(DenseArgs(), DenseArgs().render())
    built = erroring_client(DenseArgs)

    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append(f"{failure.stage} {type(failure.error).__name__}")
        return True

    def missing(name: str) -> object:
        raise risa.CodecUnavailableError(name, codecs.RADIXLY_EXTRA)

    monkeypatch.setattr(codecs, "_radixly_module", missing)
    await process(built, interaction)

    assert FAILURES == ["state CodecUnavailableError"]


class RefusedLock(contextlib.AbstractAsyncContextManager[None]):
    __slots__ = ("key", "timeout")

    def __init__(self, key: str, timeout: float) -> None:
        self.key = key
        self.timeout = timeout

    @typing.override
    async def __aenter__(self) -> None:
        raise risa.LockTimeoutError(self.key, self.timeout)

    @typing.override
    async def __aexit__(self, *_: object) -> None: ...


class LockedStore(risa.MemoryStore):
    __slots__ = ()

    @typing.override
    def lock(self, key: str, *, timeout: float = 10.0) -> contextlib.AbstractAsyncContextManager[None]:
        return RefusedLock(key, timeout)


async def state_failure(built: risa.Client, ids: list[str]) -> list[str]:
    @built.error_handler
    async def on_error(failure: risa.ClickFailure) -> bool:  # ruff:ignore[unused-async]
        FAILURES.append(f"{failure.stage} {type(failure.error).__name__}")
        return True

    await process(built, click(ids, 1))
    return FAILURES


async def test_a_lock_that_times_out_reaches_the_error_handler() -> None:
    ids = ids_of((await stored_client().build(Notebook())).components)

    assert await state_failure(erroring_client(Notebook, store=LockedStore()), ids) == ["state LockTimeoutError"]


async def test_a_failing_restore_reaches_the_error_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    ids = ids_of((await stored_client().build(Notebook())).components)

    async def broken(_: stored.StoredSession) -> None:  # ruff:ignore[unused-async]
        msg = "restore broke"
        raise RuntimeError(msg)

    monkeypatch.setattr(stored.StoredSession, "restore", broken)

    assert await state_failure(erroring_client(Notebook, store=risa.MemoryStore()), ids) == ["state RuntimeError"]


async def test_state_in_a_codec_that_is_not_installed_reaches_the_error_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @risa.register(name="client-dense-state", state=risa.InMessage(codec=codecs.BASE32768))
    class DenseState(risa.View):
        count: int = 0

        def render(self) -> ui.Layout:
            return ui.Row(ui.Button(self.add), ui.Button(self.add))

        @risa.handler
        async def add(self, ctx: risa.ComponentContext) -> None:
            self.count += 1
            await ctx.rerender()

    ids = ids_of((await erroring_client(DenseState).build(DenseState(count=3))).components)

    def missing(name: str) -> object:
        raise risa.CodecUnavailableError(name, codecs.RADIXLY_EXTRA)

    monkeypatch.setattr(codecs, "_radixly_module", missing)

    assert await state_failure(erroring_client(DenseState), ids) == ["state CodecUnavailableError"]


async def test_a_lost_commit_at_the_end_stays_out_of_the_error_handlers(caplog: pytest.LogCaptureFixture) -> None:
    store = risa.MemoryStore()
    built = erroring_client(Notebook, store=store)
    ids = ids_of((await built.build(Notebook())).components)
    (key,) = list(store._entries)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    async def rival() -> None:
        await store.put(key, b"another process", ttl=60.0)

    RIVAL_WRITES[:] = [rival]
    try:
        with caplog.at_level(logging.WARNING, logger="risa"):
            assert await state_failure(built, ids) == []
    finally:
        RIVAL_WRITES.clear()

    assert "the change was discarded" in caplog.text


@risa.register(name="client-dense-rest", state=risa.InMessage(codec=codecs.BASE32768))
class DenseRest(risa.View):
    count: int = 0

    def render(self) -> ui.Layout:
        return ui.Row(ui.Button(risa.bind(self.pick, 3)))

    @risa.handler
    async def pick(self, _ctx: risa.ComponentContext, amount: int) -> None:
        self.count = amount


async def codec_click_on_rest(*, with_handler: bool, monkeypatch: pytest.MonkeyPatch) -> Downstream:
    interaction = interaction_on(DenseRest(), DenseRest().render())
    downstream = Downstream()
    built = risa.client_from_app(typing.cast("risa.RestClientAppT", sharing_app(downstream.stream)))
    built.add_view(DenseRest)
    if with_handler:

        @built.error_handler
        async def on_error(failure: risa.ClickFailure) -> bool:
            await failure.ctx.respond("The poll cannot be read here.", ephemeral=True)
            return True

    def missing(name: str) -> object:
        raise risa.CodecUnavailableError(name, codecs.RADIXLY_EXTRA)

    monkeypatch.setattr(codecs, "_radixly_module", missing)
    listener = built._on_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]
    async for _ in listener:
        pass
    return downstream


async def test_without_error_handlers_a_rest_click_in_a_missing_codec_still_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downstream = await codec_click_on_rest(with_handler=False, monkeypatch=monkeypatch)

    assert len(downstream.seen) == 1


async def test_with_an_error_handler_risa_keeps_a_rest_click_in_a_missing_codec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downstream = await codec_click_on_rest(with_handler=True, monkeypatch=monkeypatch)

    assert not downstream.seen
