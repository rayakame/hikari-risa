# Fresh data with Prop and load()

Some things should be read fresh on every click instead of saved in the message, such as a database row. Mark them
`risa.Prop` and fill them in `load()`.
{: .risa-lead }

## A leaderboard

`game` is a field like any other, saved in the message. `rows` is a
[`risa.Prop`](../api/state/schema.md#risa.state.schema.Prop), and [`load()`](../api/view.md#risa.view.View.load) fills
it. `Scores` stands in for your database, and you can find it in the full file.

```python
--8<-- "ch07_leaderboard.py:fields"
```

`load()` [names `Scores` as a parameter](06-dependency-injection.md#name-it-as-a-parameter), as a handler would, and
`main()` registers it. Without that, `client.build` raises linkd's `DependencyNotSatisfiableException`.
`msgspec.field(default_factory=list[str])` gives each view a new empty list.

`render()` reads a prop as it reads a field. Each game's button runs `switch`, and `_TOP` at the top of the file holds
each game's top players.

```python
--8<-- "ch07_leaderboard.py:render"
```

--8<-- "previews/leaderboard.html"

## What a prop costs

A prop takes no room in the message: nothing about `rows` goes into the buttons' ids, however many rows `load()`
returns. It needs a default, because each click builds the view with its props at their defaults before `load()` runs,
and without one `@risa.register` raises `ViewDeclarationError`.

A prop is never saved, so what a handler puts in one lasts for that click's rerender, and the next click starts from the
default again.

## When `load()` runs

<ol class="risa-flow risa-flow--4">
<li class="risa-flow__step"><span class="risa-flow__n">1</span><strong>Fields return</strong><span>From the message.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">2</span><strong><code>load()</code></strong><span>Fills the props.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">3</span><strong>The handler</strong><span>Changes fields.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">4</span><strong><code>rerender()</code></strong><span>Draws fields and props.</span></li>
</ol>

`client.build` runs it too, before `render()`, so the first message has its rows. On a click, `refresh` only rerenders,
and the rows are new because `load()` has just read them. A handler can
[leave `load()` out](#a-handler-that-needs-no-load).

```python
--8<-- "ch07_leaderboard.py:refresh"
```

`load()` runs inside the three seconds from [Answering in time](05-answering-in-time.md), and its time counts toward
auto-defer's 2 seconds.

## A handler that changes what `load()` reads

!!! warning "`load()` runs before the handler, and not again."
    A handler that sets `self.game` and calls `ctx.rerender()` draws the new heading over the old rows, because `load()`
    read them while `game` still held the old value.

Read them in the handler. `load()` would still read the old game's rows first, for nothing, so `switch` is made with
[`load=False`](#a-handler-that-needs-no-load). It takes the game as a wire argument, as in
[Handlers and arguments](03-handlers-and-arguments.md#an-argument-per-button), and after it `scores`, which risa injects
as it does for `load()`. A handler's injected parameter needs no default.

```python
--8<-- "ch07_leaderboard.py:switch"
```

--8<-- "previews/leaderboard-go.html"

## A handler that needs no `load()`

[`@risa.handler(load=False)`](../api/view.md#risa.view.handler) keeps `load()` from running for clicks on that handler.
It suits a handler that fills the props itself, as `switch` does, and one that uses no prop at all, such as one that
deletes the message.

The handler then gets the props at their defaults, and so does every rerender of that click: a `ctx.rerender()` in the
handler, and the one risa does itself when the handler changed a field without rerendering. Fill every prop `render()`
draws, or change no field and leave the rerender out. `client.build` still runs `load()`.

## A change with no click

A message shows what `load()` read when it was last drawn. When the data changes with no click, such as a score that
comes in from elsewhere, [`client.rerender`](../api/client.md#risa.client.Client.rerender) redraws the message. The
view's fields stay as they are, and `load()` reads the rows again.

```python
--8<-- "ch07_leaderboard.py:outside"
```

`SCORES` is the `Scores` the bot registered, and `win` stands in for whatever changes your data. `rerender` takes the
channel, the message and the view's class, so keep the id of the message `send_to` returns. The example keeps it in a
`dict`; your bot would keep it beside the data. For a view sent in answer to an interaction, fetch the message once
with [`Response.fetch`](../api/context.md#risa.context.Response.fetch) to learn its id.

To change a field as well, open the view with [`client.open`](../api/client.md#risa.client.Client.open). The block gets
the view with its fields as they are now. Leaving the block redraws the message and saves the state, and a block that
raises draws nothing and saves none of its changes. The same listener goes on:

```python
--8<-- "ch07_leaderboard.py:block"
```

`load()` runs before the block, as it runs [before a handler](#a-handler-that-changes-what-load-reads). This update
changes `game`, which `load()` reads. So the example reads the new rows itself and then opens the view with
`load=False`. When the block changes nothing that `load()` reads, leave `load=False` out.

A redraw and a block are both updates of the message, and what follows holds for either. No [hook](13-hooks.md) and no
error handler runs for an update, and `load()` runs in `DEFAULT` alone, as it does in
[`client.build`](06-dependency-injection.md#a-dependency-for-each-click).

### Which call for which view

| The view | To redraw its message |
|---|---|
| Fields, and buttons or menus | `client.rerender`, or `client.open` to change a field too |
| No fields | `client.rerender`, or `client.open` to fill a prop in the block. risa fetches nothing, takes no lock, and does not check what the message shows |
| Fields, and a `render()` that never draws a button or a menu | `rest.edit_message(channel, message, **await client.build(view))`, where `view` is a new instance with its fields passed again |

A message with nothing to click holds no state, which is why the last row builds a new view.

!!! warning "Do not edit the message of a view from the first row through `rest.edit_message`."
    risa remembers the state it last saved to a message, and after an edit it did not make, a later click can bring that
    state back. A view in a store gets a second record, and the first stays until its `ttl` runs out, or for good under
    `ttl=None`. Use `rerender` or `open` for such a view.

### What an update costs

For a view with fields:

- **A fetch.** risa fetches the message to find the view's state, so the bot needs View Channel and Read Message
  History in the channel. A view in a store costs a second fetch: the message names the record, and risa reads the
  message again once it holds that record's lock.
- **The view's lock.** Clicks on the message wait until the update is done, and Discord's
  [three seconds](05-answering-in-time.md#the-clock) keep running for them. `load()` and the block run under the lock,
  so do slow work before `open`, as the example does with the rows. When the data changes often, update a message
  every few seconds at most: Discord limits how fast a bot edits messages, and an edit that waits for that limit holds
  the lock.

An update also restarts the [`ttl`](09-stores-and-ttl.md#ttl) of a view in a store and the time
[`expires_after`](14-ready-made-hooks.md#checks) counts, so a board that is redrawn every minute never expires.

### The limits of an update

- **A handler of the same message.** The handler holds the lock `open` waits for, so `open` raises
  [`LockTimeoutError`](../api/errors.md#risa.errors.LockTimeoutError) after 10 seconds. Change `self` there and call
  `ctx.rerender()`.
- **An ephemeral message.** Only an interaction can edit it: the one that sent it, or a click on it.
- **A layout from `ctx.edit`.** An update draws what `render()` returns, as `ctx.rerender()` does, so a layout a
  handler drew with [`ctx.edit`](05-answering-in-time.md#which-message) is gone after it. A layout with nothing to
  click ended the view, and the update raises [`MessageOutdatedError`](#what-an-update-raises), as it does once
  [`ctx.replace`](05-answering-in-time.md#which-message) gave the message to another view. A view without fields is
  not checked, so an update draws it over whatever the message shows.
- **Several processes.** [Several processes](11-several-processes.md#an-update-from-outside) says what an update does
  when clicks land in another process.

### What an update raises

Where a click would run [`on_outdated`](10-changing-a-view-safely.md#on_outdated) because the state no longer fits the
fields, or [`on_state_missing`](09-stores-and-ttl.md#ttl), there is no click to answer, so `open` and `rerender` raise:

- [`MessageOutdatedError`](../api/errors.md#risa.errors.MessageOutdatedError) when the message holds no state of the
  view that this code can read: it shows another view or another
  [`version`](10-changing-a-view-safely.md#which-version-to-raise) of this one, it has nothing to click, or a
  [breaking change](10-changing-a-view-safely.md#breaking-changes) to the view's fields or its placement went out
  without a raised `version`. Catch it to stop updating a message whose view has ended. A view without fields never
  raises it.
- [`StateNotFoundError`](../api/errors.md#risa.errors.StateNotFoundError) when the record of a
  [view in a store](08-where-state-lives.md#message-or-store) is gone.
- `LockTimeoutError` when a click holds the view's lock for more than 10 seconds.
- hikari's `NotFoundError` for a message that was deleted, and its `ForbiddenError` for one the bot may not read. risa
  passes them on as they are.

What the edit raises passes through as well. A view in the message has saved nothing then, so run the block again. A
view in a store has saved the new state, and the block would apply its change a second time: call `client.rerender`
to draw what is saved.

Leaving the block can raise as `ctx.rerender()` can, with [`StateOverflowError`](08-where-state-lives.md#the-limit)
when the state no longer fits the message and
[`StateConflictError`](11-several-processes.md#when-a-handler-loses-the-race) when another process saved first. After a
conflict the block is over, so run `open` again. The reference of
[`client.open`](../api/client.md#risa.client.Client.open) lists everything it raises.

??? example "Full file"

    ```python
    --8<-- "ch07_leaderboard.py:20:"
    ```
