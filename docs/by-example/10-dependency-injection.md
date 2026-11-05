# Dependency injection

Register a thing once, and any handler or `load()` that names it as a parameter gets it. risa does this through
[linkd](https://linkd.readthedocs.io/en/latest/).
{: .risa-lead }

## Register it

This shop looks up prices. `Prices` stands in for your database, and you can find it in the full file.

```python
--8<-- "ch10_shop.py:dependencies"
```

[`client.di`](../api/client.md#risa.client.Client.di) is linkd's manager, and
[`risa.Contexts.DEFAULT`](../api/di.md#risa.di.Contexts) is the context that stays open for the whole run. `register_value`
takes the type a parameter will name and the object it gets. Do this before the bot starts: once a click or a view's `load()` has used the
manager, `register_value` raises linkd's `RegistryFrozenException`.

A connection pool has to open inside the running event loop, so register a function for it:
`registry.register_factory(Pool, open_pool, teardown=close_pool)` calls `open_pool` at first use, and
`await client.di.close()` calls the teardown.

## Name it as a parameter

A click on an item runs `show`, which names what it needs.

```python
--8<-- "ch10_shop.py:show"
```

| Parameter | Filled by |
|---|---|
| `ctx` | risa, by position |
| `item: str` | The [wire argument](03-handlers-and-arguments.md#what-can-be-an-argument) bound to the button |
| `prices: Prices`, `client: risa.Client` | Injection, going by the annotation, so any name will do |

[`load()`](06-fresh-data.md#a-leaderboard) takes injected parameters as well, with `= risa.INJECTED`, and so do
`on_outdated` and `on_state_missing`.

If nothing is registered for a parameter, the handler does not run: risa logs linkd's
`DependencyNotSatisfiableException`, and the click fails in Discord. A handler with only `ctx` and wire arguments needs
nothing from linkd.

## Build another view

A handler needs the client for `client.build`, and the client is a local variable of `main()`. risa registers it, so
`show` above names it, builds an `Offer` and sends that as its answer. This is the other view, and `main()` adds both to the
client.

```python
--8<-- "ch10_shop.py:offer"
```

??? example "Full file"

    ```python
    --8<-- "ch10_shop.py:20:"
    ```

## What risa registers

These need no registering from you:

| Parameter | What it gets |
|---|---|
| `client: risa.Client` | The risa client. `risa.GatewayEnabledClient` is not registered, so annotate it this way |
| `rest: hikari.api.RESTClient` | The bot's REST client |
| `bot: hikari.GatewayBot`, `events: hikari.api.EventManager` | On a gateway bot, the bot and its event manager |
| `bot: hikari.RESTBot`, `server: hikari.api.InteractionServer` | On a REST bot, the bot and its interaction server |

## Share one manager with lightbulb

[`risa.client_from_lightbulb`](../api/client.md#risa.client.client_from_lightbulb) makes a client on lightbulb's manager,
so what you register with lightbulb reaches your handlers too. lightbulb registers the hikari types above itself.

```python
--8<-- "ch10_shop_lightbulb.py:share"
```

A command gets the risa client the same way:

```python
--8<-- "ch10_shop_lightbulb.py:command"
```

??? example "Full file"

    ```python
    --8<-- "ch10_shop_lightbulb.py:20:"
    ```
