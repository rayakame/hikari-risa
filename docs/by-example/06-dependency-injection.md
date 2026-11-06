# Dependency injection

A handler often needs something from outside its view, such as your database. Register it once at startup, name it as a
parameter, and risa passes it in on every click.
{: .risa-lead }

## Why you need it

This shop shows a price when someone clicks an item. The prices are in your database, so the handler that answers the
click needs your database.

--8<-- "previews/shop.html"

A function of your own gets what it needs as arguments, because you write the call. You never call a handler: a click
comes in, and risa calls it. The view can't hold your database either, because
[its fields get saved](02-buttons-that-remember.md#fields-are-the-state) from click to click, and `@risa.register` raises
`ViewDeclarationError` on a type it can't save.

So the handler names what it needs as a parameter, and risa passes it in. Your database is a *dependency* of the handler,
and passing it in is *injecting* it.

<ol class="risa-flow">
<li class="risa-flow__step"><span class="risa-flow__n">1</span><strong>You register it</strong><span>At startup, you tell risa which object a <code>Prices</code> parameter gets.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">2</span><strong>The handler names it</strong><span>It takes a parameter, <code>prices: Prices</code>.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">3</span><strong>risa passes it in</strong><span>On each click, risa calls the handler with that object.</span></li>
</ol>

A global variable would do for a small object like this one. A connection pool opens only once the bot is running, so its
global would stay empty until then, and each handler would have to check it.

## Register it

`Prices` is your own class, standing in for your database, and you can find it in the full file. `main()` calls this once,
before the bot starts.

```python
--8<-- "ch06_shop.py:dependencies"
```

`register_value(Prices, Prices())` says that a parameter annotated `Prices` gets this one object. `registry_for` picks
where it's kept, and [`risa.Contexts.DEFAULT`](../api/di.md#risa.di.Contexts) keeps it for the whole run.
[`client.di`](../api/client.md#risa.client.Client.di) is the manager of [linkd](https://linkd.readthedocs.io/en/latest/),
the library risa injects with.

Do this before the bot starts: once a click or a view's `load()` has used the manager, `register_value` raises linkd's
`RegistryFrozenException`.

A connection pool has to open inside the running event loop, so register a function for it:
`registry.register_factory(Pool, open_pool, teardown=close_pool)` calls `open_pool` at first use, and
`await client.di.close()` calls the teardown.

## Name it as a parameter

A click on an item runs `show`, which names what it needs.

```python
--8<-- "ch06_shop.py:show"
```

| Parameter | Filled by |
|---|---|
| `ctx` | risa, by position |
| `item: str` | The [wire argument](03-handlers-and-arguments.md#what-can-be-an-argument) bound to the button |
| `prices: Prices`, `client: risa.Client` | Injection, going by the annotation, so any name will do |

risa tells them apart by type. `str` is a [type an argument can have](03-handlers-and-arguments.md#what-can-be-an-argument),
so `item` comes from the button. `Prices` is not, so `prices` is injected.

Later chapters add [`load()`](07-fresh-data.md#a-leaderboard), [`on_state_missing`](09-stores-and-ttl.md#ttl) and
[`on_outdated`](10-changing-a-view-safely.md#on_outdated), which take injected parameters the same way.

If nothing is registered for a parameter, the handler does not run: risa logs linkd's
`DependencyNotSatisfiableException`, and the click fails in Discord. A handler with only `ctx` and wire arguments needs
nothing from linkd.

## Build another view

A handler needs the client for `client.build`, and the client is a local variable of `main()`. risa registers it, so
`show` above names it, builds an `Offer` and sends that as its answer. This is the other view, and `main()` adds both to the
client.

```python
--8<-- "ch06_shop.py:offer"
```

--8<-- "previews/offer.html"

??? example "Full file"

    ```python
    --8<-- "ch06_shop.py:20:"
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
--8<-- "ch06_shop_lightbulb.py:share"
```

A command gets the risa client the same way:

```python
--8<-- "ch06_shop_lightbulb.py:command"
```

??? example "Full file"

    ```python
    --8<-- "ch06_shop_lightbulb.py:20:"
    ```
