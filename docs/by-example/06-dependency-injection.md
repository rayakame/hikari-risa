# Dependency injection

A handler often needs something it doesn't make itself, such as your database. Register that thing once, name it as a
parameter, and risa passes it in on every click.
{: .risa-lead }

## What it is

A *dependency* is anything your code needs but doesn't make itself: a database, an HTTP session, your config. A function
can get one in two ways. It can open the thing itself:

```python
async def price_of(item: str) -> int:
    database = await connect("postgres://localhost/shop")
    return await database.price(item)
```

That opens a new connection on every call, and each function that needs the database has to know how to open it. Or the
function can name what it needs and leave the rest to whoever calls it:

```python
async def price_of(item: str, database: Database) -> int:
    return await database.price(item)
```

This is dependency injection: the function says what it needs, and its caller passes it in. Every call can share one
database, and only one place in your bot knows how to open it.

A handler works the same way, except that you never call it. A click comes in, and risa calls it. So you tell risa at
startup what to pass, and the handler names what it wants. risa does this through
[linkd](https://linkd.readthedocs.io/en/latest/), the library lightbulb uses as well.

<ol class="risa-flow">
<li class="risa-flow__step"><span class="risa-flow__n">1</span><strong>You register it</strong><span>At startup, you tell risa which object a <code>Prices</code> parameter gets.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">2</span><strong>The handler names it</strong><span>It takes a parameter, <code>prices: Prices</code>.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">3</span><strong>risa passes it in</strong><span>On each click, risa calls the handler with that object.</span></li>
</ol>

A view's field can't hold your database: [fields get saved](02-buttons-that-remember.md#fields-are-the-state) from click
to click, and `@risa.register` raises `ViewDeclarationError` on a type it can't save. A global variable can, until you
have something that opens only once the bot is running, such as a connection pool. The global stays empty until then, and
each handler has to check it.

## Register it

This shop shows a price when someone clicks an item. `Prices` stands in for your database, and you can find it in the
full file.

--8<-- "previews/shop.html"

`main()` calls this once, before the bot starts:

```python
--8<-- "ch06_shop.py:dependencies"
```

`register_value(Prices, Prices())` says that a parameter annotated `Prices` gets this one object.
[How it works](#how-it-works) explains the line above it.

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

If nothing is registered for a parameter, the handler does not run: risa logs linkd's
`DependencyNotSatisfiableException`, and the click fails in Discord.

## Build another view

`show` also names `client`, which you never registered, because [risa registers it](#what-risa-registers) for you. A
handler needs it for `client.build`, since the client is a local variable of `main()`. `show` builds an `Offer` and sends
that as its answer. This is the other view, and `main()` adds both to the client.

```python
--8<-- "ch06_shop.py:offer"
```

--8<-- "previews/offer.html"

??? example "Full file"

    ```python
    --8<-- "ch06_shop.py:20:"
    ```

That covers most bots. The rest of this page explains how injection works and what else it can do.

## What risa registers

risa registers these itself, so you can name them in any handler without registering anything.

| For the whole run | What it gets |
|---|---|
| `risa.Client` | The risa client, for `client.build`. `risa.GatewayEnabledClient` is not registered, so annotate it this way |
| `hikari.api.RESTClient` | The bot's REST client, for calling Discord beyond the answer to this click |
| `hikari.GatewayBot` | On a gateway bot, the bot, with its cache in `bot.cache` |
| `hikari.api.EventManager` | On a gateway bot, its event manager |
| `hikari.RESTBot` | On a REST bot, the bot |
| `hikari.api.InteractionServer` | On a REST bot, its interaction server |

| For each click | What it gets |
|---|---|
| [`risa.ComponentContext`](../api/context.md#risa.context.ComponentContext) | The click, the same object as `ctx`. A [factory](#values-and-factories) or a function further down uses this to reach it |
| `hikari.ComponentInteraction` | The interaction behind the click, the same object as `ctx.interaction` |

This handler uses the REST client to post in a log channel as well as answering the click:

```python
--8<-- "ch06_feedback.py:report"
```

## How it works

Three things work together.

| Term | What it is |
|---|---|
| Context | A lifetime with a name. [`risa.Contexts.DEFAULT`](../api/di.md#risa.di.Contexts) lasts for the bot's whole run, and `risa.Contexts.COMPONENT` lasts for one click |
| Registry | The list of what a context can provide and how to make it. `register_value` adds to this list |
| Container | A box of live objects, made from a registry while its context is open. It makes each object the first time something asks for it, keeps it, and tears it down when the box closes |

So `client.di.registry_for(risa.Contexts.DEFAULT)` reads as "the list for the whole run", and
[`client.di`](../api/client.md#risa.client.Client.di) is linkd's manager, which holds every list and box. There is one more
context, `risa.Contexts.MODAL`, which waits for modals.

The boxes sit one inside the other. The objects below are the ones from the shop and the
[stall further down](#a-dependency-for-each-click).

<div class="risa-scope">
<p class="risa-scope__title"><code>DEFAULT</code> one container, open for the bot's whole run</p>
<p class="risa-scope__items"><code>risa.Client</code> <code>hikari.api.RESTClient</code> <code>Prices</code> <code>Database</code></p>
<div class="risa-scope risa-scope--inner">
<p class="risa-scope__title"><code>COMPONENT</code> a new container for each click</p>
<p class="risa-scope__items"><code>risa.ComponentContext</code> <code>hikari.ComponentInteraction</code> <code>Wallet</code></p>
<p class="risa-scope__note">A handler runs here. It looks in this box first, then in the one around it.</p>
</div>
</div>

<ol class="risa-flow risa-flow--4">
<li class="risa-flow__step"><span class="risa-flow__n">1</span><strong>The click</strong><span>risa opens a <code>COMPONENT</code> container and puts <code>ctx</code> and the interaction in it.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">2</span><strong><code>load()</code> and the handler</strong><span>For each parameter, the container hands over what it holds, or makes it.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">3</span><strong>The end of the click</strong><span>The container closes and tears down what it made.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">4</span><strong>The next click</strong><span>It gets a new container. The <code>DEFAULT</code> one stays open.</span></li>
</ol>

Two rules follow from this:

- **A container only looks outward.** A dependency in `DEFAULT` can't name one in `COMPONENT`, and a click that needs it
  fails with `DependencyNotSatisfiableException`.
- **A registry freezes while a container made from it is open.** The `DEFAULT` container opens at the first click or
  `load()` and stays open, so a later `register_value` raises linkd's `RegistryFrozenException`. Register before the bot
  starts.

## Values and factories

`register_value` takes an object you already have. `register_factory` takes a function, and the container calls it the
first time something names the type. Use it for what has to open inside the running event loop, such as a connection
pool.

```python
--8<-- "ch06_wallet.py:database"
```

A factory can be async, and its parameters are injected too, so `open_database` names `Config` and gets it from the same
registry. If a factory raises, the handler does not run: risa logs `DependencyNotSatisfiableException`, and the click
fails. [Cleanup](#cleanup) covers `teardown`.

## A dependency for each click

A wallet belongs to whoever clicked, so it can't exist before the click. Register it in `COMPONENT`, and each click gets
its own.

```python
--8<-- "ch06_wallet.py:wallet"
```

`open_wallet` names the click, which risa puts in the `COMPONENT` container, and `Database`, which the container finds in
the `DEFAULT` one around it. A handler names `Wallet` like anything else.

```python
--8<-- "ch06_wallet.py:buy"
```

--8<-- "previews/stall.html"

--8<-- "previews/stall-bought.html"

The container makes a wallet at most once per click, so `load()` and the handler of one click share it, and a handler
that doesn't name it never opens one.

[`client.build`](../api/client.md#risa.client.Client.build) runs `load()` in `DEFAULT` alone, even inside a handler. A
`load()` that names `Wallet` makes it raise `DependencyNotSatisfiableException`, and one that names `Wallet | None` gets
`None` there.

??? example "Full file"

    ```python
    --8<-- "ch06_wallet.py:20:"
    ```

## Overriding a dependency

Register a type in `COMPONENT` that `DEFAULT` has too, and a click gets the `COMPONENT` one. A dependency made in `DEFAULT`
keeps using the `DEFAULT` one, because a container only looks outward.

## Adding to an open container

A frozen registry takes nothing new, but an open container does. `container.add_value` and `container.add_factory` add to
that one container, and what they add goes when it closes. linkd calls these ephemeral dependencies, and risa adds `ctx`
this way. A handler reaches its container by naming it, and a function further down then gets what it added.

```python
--8<-- "ch06_feedback.py:flag"
```

| Container | What it gets |
|---|---|
| [`risa.ComponentContainer`](../api/di.md#risa.di.ComponentContainer) | The click's container |
| `linkd.RootContainer` | The `DEFAULT` container |
| `linkd.Container` | The innermost open container, which in a handler is the click's |

## Two of one type

A type has one registration. For two databases, make a class for each, and name those.

```python
--8<-- "ch06_feedback.py:types"
```

```python
--8<-- "ch06_feedback.py:vote"
```

If a library makes the object for you, so that a subclass won't do, wrap it in a small class of your own. linkd also accepts
a `typing.NewType` as the type, but pyright rejects it in `register_value`.

## Optional dependencies

Annotate a parameter `Metrics | None`, and it gets `None` when nothing is registered.

```python
--8<-- "ch06_feedback.py:like"
```

A union falls back the same way: `Memory | Disk` gets the first one registered. `linkd.Try[Memory] | Disk` also falls back
when making `Memory` fails, where a plain union would fail the click.

## Where injection works

Handlers, `load()`, [`on_state_missing`](09-stores-and-ttl.md#ttl), [`on_outdated`](10-changing-a-view-safely.md#on_outdated)
and factories have it without a decorator. For a function of your own, add `@linkd.inject`. `render()` takes no injected
parameters, so `load()` reads what it draws.

A parameter is injected if it has an annotation and either no default or the default
[`risa.INJECTED`](../api/di.md#risa.di.INJECTED). Any other default stays as it is. `*args`, `**kwargs` and positional-only
parameters are left alone. A parameter with no annotation gets nothing, so the click fails with a `TypeError`.

`risa.INJECTED` tells your type checker that nobody has to pass the parameter. [`on_outdated`](10-changing-a-view-safely.md#on_outdated),
`on_state_missing` and a function of your own that you call with no arguments need it. In a handler it also marks a wire type as
injected: `limit: int = risa.INJECTED`.

## Injection context

An injected function needs an open context when it runs. risa opens `DEFAULT` and `COMPONENT` around `load()`, the handler
and the hooks of a click, and `DEFAULT` around the `load()` in `client.build`. A function called from inside those has them
too, so you rarely need to think about this.

Anywhere else, such as in a hikari listener, open the context yourself, or the call raises
`DependencyNotSatisfiableException`.

```python
--8<-- "ch06_feedback.py:outside"
```

??? example "Full file"

    ```python
    --8<-- "ch06_feedback.py:20:"
    ```

## Cleanup

Give a dependency a `teardown`, a function that takes the dependency, as `open_database` above does with `Database.close`.
It runs when the dependency's container closes, and only if the container made it.

A `COMPONENT` container closes at the end of each click, even if the handler raised. The `DEFAULT` container stays open
until you close it, and risa never does, so call `client.di.close()` when the bot stops.

=== "Gateway bot"

    ```python
    --8<-- "ch06_wallet.py:stopping"
    ```

=== "REST bot"

    ```python
    --8<-- "ch06_stop_rest.py:stopping"
    ```

With lightbulb, `lightbulb_client.stop` closes the shared manager, so `bot.subscribe(hikari.StoppingEvent, lightbulb_client.stop)` is enough.

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

`risa.Contexts.DEFAULT` and lightbulb's `DEFAULT` are the same context, linkd's root. A click opens risa's `COMPONENT` and a
command opens lightbulb's `COMMAND`, and neither sees what the other registers.

??? example "Full file"

    ```python
    --8<-- "ch06_shop_lightbulb.py:20:"
    ```

## Turning it off

Set the environment variable `LINKD_DI_DISABLED` to `true` to turn linkd off. risa passes `ctx` and the wire arguments
itself, so a handler that takes only those keeps working. A handler that names anything else fails with a `TypeError`.

## Types have to exist at runtime

linkd reads annotations while the bot runs, so a dependency's type has to be importable then. A type imported only under
`if typing.TYPE_CHECKING:` makes `@risa.register` raise `HandlerSignatureError` for a handler, and `NameError` for a
`load()` or a factory. Keep that import outside the block.
