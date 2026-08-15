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
import enum
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
    interaction.message = unittest.mock.Mock(spec=hikari.Message, flags=hikari.MessageFlag.NONE)
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


async def test_a_view_that_cannot_be_constructed_is_contained(caplog: pytest.LogCaptureFixture) -> None:
    built = deferring_client(Needy)
    interaction = interaction_with(encoded_id_for(Needy, handler=Needy.press.token))

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "could not be constructed" in caplog.text
    assert "never ran" in caplog.text
    assert "press" in caplog.text
    rest_of(built).execute_webhook.assert_not_called()


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
    (built,) = ui.build(ui.Row(button), meta)
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
    one_frame = codec.pack_frames([codec.StrConverter().encode("x")])
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
    stale = codec.pack_frames([codec.IntConverter(int).encode(99)])
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
    stale = codec.pack_frames([codec.IntConverter(int).encode(99)])
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


async def test_a_handler_mutating_self_and_rerendering_paints_the_mutation() -> None:
    built = risa.client_from_app(gateway_app())
    built.add_view(Repaint)
    interaction = interaction_with(encoded_id_for(Repaint, handler=Repaint.flip.token))

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
    interaction = interaction_with(encoded_id_for(Repaint, handler=Repaint.flip.token))

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
    interaction = interaction_with(encoded_id_for(Repaint, handler=Repaint.broken.token))

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
    listener = built._on_interaction(interaction_with(encoded_id_for(Repaint, handler=Repaint.flip.token)))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

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


async def test_an_unknown_cookie_never_reaches_any_hook() -> None:
    built = outdated_client(Gracious)

    await built._process_interaction(interaction_with(encoded_id_for(Elsewhere)))  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert not OUTDATED_CALLS


async def test_a_view_that_cannot_be_constructed_skips_the_hook(caplog: pytest.LogCaptureFixture) -> None:
    built = outdated_client(Brittle)
    interaction = interaction_with(encoded_id_for(Brittle))

    with caplog.at_level(logging.ERROR, logger="risa"):
        await built._process_interaction(interaction)  # type: ignore[reportPrivateUsage]  # ruff:ignore[private-member-access]

    assert "on_outdated hook never ran" in caplog.text
    assert not OUTDATED_CALLS


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
