# Coming from miru

hikari-risa is not a direct successor to miru, so it does some things differently. Much of it will still look familiar:
a view class, methods that answer clicks, a client that routes them. The big change is where a view lives. A miru view
is an object in your bot's memory. A risa view is rebuilt on every click, from state kept in the message or in a store
you choose.
{: .risa-lead }

This page compares with miru 4.3.1. The main things miru has and risa does not are under
[What risa does not have](#what-risa-does-not-have). risa needs Python 3.12 to 3.15, and miru 4.3.1 needs 3.11 or newer.

## The same things under other names

| miru | risa |
|---|---|
| `class MyView(miru.View)` | `@risa.register(name="my-view")` above `class MyView(risa.View)` |
| `self.x = 0` in `__init__` | An annotated field, `x: int = 0` |
| An attribute set on a view stays until the view stops | A `risa.Prop` starts from its default on every click, and `load()` fills it |
| `@miru.button(...)` on a callback `(self, ctx, button)` | `@risa.handler` on a method `(self, ctx)`, and `ui.Button(self.method, ...)` in `render()` |
| `@miru.text_select(...)` and `select.values` | `ui.TextSelect(self.method, ...)` and `ctx.values` |
| `select.values` on a `miru.RoleSelect` holds the roles | `ctx.values` holds their ids, and `ctx.resolved` the roles |
| `miru.Button` subclasses that hold a value | One handler with an argument, `ui.Button(risa.bind(self.method, value))` |
| `view.add_item(...)` and `remove_item(...)` | `if` and `for` in `render()` |
| `miru.ViewContext` | `risa.ComponentContext` |
| `ctx.view` | `self` |
| `await ctx.edit_response(components=self)` | `await ctx.rerender()` |
| `ctx.respond("Hi", flags=hikari.MessageFlag.EPHEMERAL)` | `ctx.respond("Hi", ephemeral=True)` |
| `miru.Client(bot)` | `risa.client_from_app(bot)`, or `risa.client_from_lightbulb(lightbulb_client)` with lightbulb |
| `client.start_view(view)` after each send | `client.add_view(MyView)` once at startup |
| `respond("Hi", components=view)` | `respond(**await client.build(MyView()))` |
| `timeout=`, 120 seconds by default | `InStore(ttl=...)`, and no expiry in the message |
| `autodefer=True` by default, after 2 seconds | `AutoDefer.OFF` by default; set it on a handler, view or client |
| alluka, with `@client.inject_dependencies` | linkd, with no decorator; [Dependency injection](../by-example/06-dependency-injection.md#name-it-as-a-parameter) says which parameters are injected |

## A view

[miru's guide to editing items](https://miru.hypergonial.com/guides/editing_items/) has a counter. This is its counter
button:

```python title="miru"
class EditView(miru.View):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.counter = 0

    @miru.button(label="Counter: 0", style=hikari.ButtonStyle.PRIMARY)
    async def counter_button(
        self, ctx: miru.ViewContext, button: miru.Button
    ) -> None:
        self.counter += 1
        button.label = f"Counter: {self.counter}"
        await ctx.edit_response(components=self)
```

And this is a counter in risa:

```python title="risa"
--8<-- "first_view_gateway.py:view"
```

--8<-- "previews/counter.html"

Three things differ:

- **Items come from `render()`.** A miru item is an object you change. A risa button is drawn again on every rerender,
  so you change the fields and the button follows.
- **The whole message is the view.** A miru 4.3.1 view builds action rows, and you pass `content` and embeds beside
  them. A risa view renders with Components V2: containers, sections, galleries and rows. Such a message has no
  `content`, so the text goes in `ui.TextDisplay`.
- **There is no `__init__`.** A view is a `msgspec.Struct`, made with `Counter(count=3)`, and its annotated fields are
  saved from click to click. For what should not be saved, such as a database row, mark the field
  `risa.Prop[T]` and fill it in `load()`, which runs before every handler, as
  [Fresh data](../by-example/07-fresh-data.md) shows.

## Sending

In miru, each message gets its own view object, which you start after sending:

```python title="miru"
view = EditView()
await event.message.respond("Hello miru!", components=view)
client.start_view(view)
```

In risa, you add the class once. `client.build` renders an instance for each message, and nothing needs starting:

```python title="risa"
--8<-- "first_view_gateway.py:send"
```

The result spreads into any call that takes `components=`, so inside a handler it reads
`await ctx.respond(**await client.build(Counter()))`.

## Restarts

[miru's guide to persistent views](https://miru.hypergonial.com/guides/persistent_views/) says that after a restart
"your views will no longer work, even if the timeout did not expire". The answer there is a persistent view:
`timeout=None`, a `custom_id` on every item, and starting the view again at startup.

```python title="miru"
class Persistence(miru.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @miru.button(label="Button 1", custom_id="my_unique_custom_id_1")
    async def button_one(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        await ctx.respond("You pressed button 1.")


@bot.listen()
async def start_views(event: hikari.StartedEvent) -> None:
    client.start_view(Persistence(), bind_to=None)
```

An unbound view like this one serves every message of its type and does not know which message it is on. A bound view
serves one message: you pass its id, which you keep yourself, or it binds to the first message clicked. Either way, you
bring back whatever the view held.

In risa, the `Counter` above already survives a restart. `add_view` at startup is the whole setup, there is no
`timeout=None` and no `custom_id`, and each message keeps its own count. See
[Restart it](first-view.md#restart-it) for how that works.

This has costs:

- The view's `name` is its route, and so is its `version`, which you raise when you change its fields or move it to a
  store. Either change retires the buttons already sent. Appending a field with a default needs no new version, and
  [Changing a view safely](../by-example/10-changing-a-view-safely.md) lists the rest.
- State in the message has limited room: about 60 bytes on one button, and about 70 more for each further one, with the
  default codec. Anyone who can see the message can read it.
- A rerender that changes state in the message changes the id of the button that holds it, and Discord refuses a click
  on the old id. If two people click at nearly the same moment, the second can fail and click again. A view in a store
  keeps its ids unless a bound argument changes.
- A view in a `MemoryStore` does not survive a restart. Use `RedisStore` or `PostgresStore` for that.

## Timeouts

A miru view [times out after 120 seconds of inactivity](https://miru.hypergonial.com/guides/checks_timeout/) unless you
pass another `timeout=`. Then `on_timeout()` runs and the view stops.

```python title="miru"
class TimeoutView(miru.View):
    @miru.button(label="Click me!", style=hikari.ButtonStyle.SUCCESS)
    async def basic_button(self, ctx: miru.ViewContext, button: miru.Button) -> None:
        await ctx.respond("You clicked me!")

    async def on_timeout(self) -> None:
        print("This view timed out!")


view = TimeoutView(timeout=10.0)
```

A risa view in the message has no timeout: it works as long as the message exists. For a lifetime, keep the state in a
store with [`InStore`](../by-example/09-stores-and-ttl.md#ttl). `ttl` has no default. Like miru's timeout it counts seconds of
inactivity, and every click restarts it.

```python title="risa"
--8<-- "coming_from_miru.py:view"
```

--8<-- "previews/question.html"

--8<-- "previews/question-expired.html"

The client needs a store for this. A `MemoryStore` is for trying things out:

```python
--8<-- "coming_from_miru.py:client"
```

??? example "Full file"

    ```python
    --8<-- "coming_from_miru.py:20:"
    ```

`on_timeout()` runs when the time is up. risa has no timer, so `on_state_missing()` runs when someone clicks after the
record has expired. Until then the message stays as it is, and risa has no hook for greying out its buttons at that
moment.

To end a view yourself, where miru has `self.stop()`, replace the message with a layout that has no buttons. `yes` above
does that with [`ctx.edit`](../by-example/05-answering-in-time.md#which-message).

## What risa does not have

- **Modals.** miru has [modals](https://miru.hypergonial.com/guides/modals/), with `miru.Modal` and
  `ctx.respond_with_modal`. risa's are designed but not built, because they wait for hikari to parse Discord's new modal
  components.
- **Menus and navigators.** miru ships [`miru.ext.menu`](https://miru.hypergonial.com/guides/menus/) and
  [`miru.ext.nav`](https://miru.hypergonial.com/guides/navigators/). In risa you write these yourself, for example as a
  view with a `page: int` field.
- **A check before every handler.** miru has `view_check`. In risa, a handler checks `ctx.user` itself.
- **An error hook on each view.** miru has [`on_error`](https://miru.hypergonial.com/guides/error_handling/) on each
  view. risa has [error handlers](../by-example/05-answering-in-time.md#when-something-fails) for the whole client, and
  a handler that wants its own answer uses `try`.
- **Waiting on a view.** miru has `view.wait()` and `wait_for_input()`. A risa view has no object that lives between
  clicks, so the code you would run after `wait()` goes in the handler.
- **Sharing injection with arc.** miru has `Client.from_arc`. risa shares a dependency manager with lightbulb only,
  through `client_from_lightbulb`. With arc or crescent, make the client with `client_from_app`.

## Moving one view at a time

A bot can run both, so you need not move everything at once. A risa client answers only the ids risa wrote.

On a gateway bot, make the miru client with `ignore_unknown_interactions=True`. Without it, miru logs a warning for
every click on a risa view.

On a REST bot, hikari has one slot for a component listener, so make the miru client first. risa takes the slot and
passes on each click it does not handle, and it logs a warning about that unless you pass `warn_shared_listener=False`.
In the other order, miru's own call to set its listener raises.
