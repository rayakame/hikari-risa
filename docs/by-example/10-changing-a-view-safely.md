# Changing a view safely

A message outlives the code that sent it: someone can click last month's poll on the code you released today. This
chapter shows which changes break those old buttons, and every way to deal with one that does.
{: .risa-lead }

Before you release a change, look it up under [What breaks](#what-breaks). If it is free, release it. If it breaks old
buttons, pick from [What you can do](#what-you-can-do), or from [Changing a handler](#changing-a-handler) if only a
handler changed.

## Why an old button can break

A button's `custom_id` stays as the old code wrote it until the message's next rerender. These are a poll's ids:

--8<-- "previews/poll-bumped-ids.html"

On a click, risa holds what the id says against the code running now, in this order:

<ol class="risa-flow risa-flow--4">
<li class="risa-flow__step"><span class="risa-flow__n">1</span><strong>The view</strong><span>Its <code>name</code> and <code>version</code>. With no class at that version, the click fails in Discord and none of your code runs.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">2</span><strong>The handler</strong><span>Its <code>handler_id</code> and <code>version</code>. With no such method, <code>on_outdated</code> runs.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">3</span><strong>The arguments</strong><span>Their types, then their values. With other types, or an enum value that is gone, <code>on_outdated</code> runs.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">4</span><strong>The state</strong><span>The fields that wrote it. If they no longer fit, <code>on_outdated</code> runs. If a store has lost the record, <code>on_state_<wbr>missing</code> runs.</span></li>
</ol>

If all four pass, the handler runs: today's code on the old state.

## What breaks

### Free changes

Old buttons keep working through all of these:

| You change | What to know |
|---|---|
| The code of `render()`, `load()`, a handler or a hook | An old message keeps its look until its next rerender |
| A button added to `render()`, or left out | A button you left out stays on old messages until their next rerender, so keep its handler until then |
| The Python name of the class or its module | risa routes by `name=` |
| A field added at the end, with a default | It takes room in a message |
| A field's default, or a default added to a field | State that holds the field keeps its value, and state saved before the field existed gets the current default |
| A [`risa.Prop`](07-fresh-data.md), added, changed or removed | Props are not saved |
| A field from `int` to `hikari.Snowflake` | Both are saved the same way |
| A value added to a field's `enum.Enum`, or to a `Literal` of the same kinds of values | risa compares only whether the values are `int`, `str` or `None` |
| An enum member's name, if its value stays | risa saves the value |
| `ttl` | An old record takes it at its next click, or `None` at its next save |
| `InMessage` to `InStore(adopt=True)`, with the same `version` | An old message moves its state into the store at its next click |
| The [codec](08-where-state-lives.md#another-codec) | An old message takes it at its next rerender, as long as risa can still read the old one |
| A handler's parameter names, its `defer`, or an injected parameter added | See [Changing a handler](#changing-a-handler) |

Here is the most useful one. This poll gets a note under its question, in a field at the end of the class:

```python
--8<-- "ch10_poll_free.py:view"
```

--8<-- "previews/poll-note.html"

An old poll keeps working. Its next click fills `note` with the default, `rerender()` draws it, and the votes are still
there. In a message the new field takes room too: with this `note`, the poll from
[Where state lives](08-where-state-lives.md#the-limit) has room for ten voters, and one that already holds ten or more
fails on its next vote.

!!! tip "Try it."
    Send a poll with the Redis file from [Stores and TTL](09-stores-and-ttl.md#redis-and-postgresql) and stop the bot.
    Start the file below on the same Redis and click **Yes** on the old poll. It counts your vote and gains the note.

??? example "Full file"

    ```python
    --8<-- "ch10_poll_free.py:20:"
    ```

### Breaking changes

| You change | A click on an old message | risa logs |
|---|---|---|
| The view's `version` or `name` | Fails in Discord | `DEBUG` |
| A field: removed, renamed, reordered or retyped | `on_outdated` | `ERROR` |
| A field added anywhere but the end, or with no default | `on_outdated` | `ERROR` |
| A field made a `risa.Prop` | `on_outdated` | `ERROR` |
| A value removed from a field's `enum.Enum` or `Literal`, or changed | `on_outdated`, only for messages that hold it | `WARNING` |
| A field's `Literal` gains a kind of value, or loses its last value of one | `on_outdated` | `ERROR` |
| A field's nested struct changes | `on_outdated` | `ERROR` |
| `InMessage` to `InStore` without `adopt=True`, or `InStore` to `InMessage` | `on_outdated` | `ERROR` |
| Another store, or another `key_prefix` | `on_state_missing` | `WARNING`, or `DEBUG` once you override it |
| Less room in a message: more state, or fewer buttons to hold it | The handler runs, and `rerender()` raises `StateOverflowError` | `ERROR` |
| A method renamed or deleted, or its `version` raised with no method left at the old one | `on_outdated` | `WARNING`, or `DEBUG` once you override it |
| A handler's arguments: added, removed or retyped | `on_outdated` | `ERROR` |
| A codec old messages use: its extra removed, or [your own](08-where-state-lives.md#another-codec) no longer registered | Fails in Discord | `ERROR` for the extra, nothing for your own |
| A bound enum member removed, or its value changed | `on_outdated` | `WARNING` |
| An argument's default removed, from a button that left the argument out | `on_outdated` | `ERROR` |

`on_outdated` and `on_state_missing` do nothing until you override them, so without that, a click that reaches them fails
in Discord too.

An enum with a `_missing_` method decides itself what a removed value becomes, in a field and in an argument. The handler
then runs with the member it returns, and `on_outdated` stays out of it.

The `ERROR` lines on fields and arguments usually mean a forgotten `version`, and they tell you to raise it. hikari logs
at `INFO` by default, so you see no line for a raised `version` until you turn `DEBUG` on.

### What risa cannot see

risa compares names and types. If `price: int` held cents and now holds euros, every check passes, and your handler reads
old cents as euros. An argument that keeps its type and changes its meaning is the same, and so are two enum members that
swap their values. Raise the `version` yourself when a meaning changes.

### In a message or in a store

This chapter's rules hold for `InMessage` and `InStore` alike, and its poll sits in a store. These are the differences:

| | `InMessage` | `InStore` |
|---|---|---|
| New fields | Take room, and a message that no longer fits fails at its next save | No limit |
| Fewer buttons | Less room, with the same failure | No effect, since each button holds the same key |
| State that is gone | Cannot happen, so `on_state_missing` never runs | An expired record, another store or another `key_prefix` run `on_state_missing` |
| What a retired message leaves behind | Nothing | Its record, until its `ttl` runs out, or for good under `ttl=None` |
| A bound argument | Shares its id with the state | Shares its id with the key |
| Free to change | The codec | `ttl` |

## What you can do

Say the poll should take any options, not only yes and no. `yes` and `no` become one `votes` dict, and `vote_yes` and
`vote_no` become one handler with an argument. The fields changed, so the view's
[`version`](../api/view.md#risa.view.register) goes up to 3.

```python
--8<-- "ch10_poll_bumped.py:view"
```

--8<-- "previews/poll-bumped.html"

Polls already sent stay at version 2. These are your options for them, and you can choose per old version:

| Option | A click on an old poll | What stays in your code |
|---|---|---|
| [Make the change free](#make-the-change-free) | Works | The old fields or handlers, next to the new ones |
| [Let old buttons fail](#let-old-buttons-fail) | Fails in Discord | Nothing |
| [Answer them all the same way](#answer-them-all-the-same-way) | Gets one reply | A small class |
| [Close the old message](#close-the-old-message) | Shows the final result, and the buttons go | The old fields and a method per old button |
| [Keep the old version running](#keep-the-old-version-running) | Works as before | The whole old class |
| [Upgrade the old message](#upgrade-the-old-message) | Turns the poll into version 3 | The old fields, a method per old button, the conversion |

### Make the change free

Many breaking changes have a free form that reaches the same goal:

| Instead of | Do this |
|---|---|
| Renaming or retyping a field | Keep it, add the new one at the end with a default, and read both |
| Renaming a method | [Pin its `handler_id`](#renaming-a-method) |
| Changing a handler's arguments | [Keep a method at the old version](#changing-its-arguments) |

For the poll, `yes` and `no` would stay behind the two old buttons, and a `more: dict[str, int]` at the end would hold
the other options. Old polls keep working, and you carry both forms for as long as those polls matter.

### Let old buttons fail

Raise the `version` and do nothing else. A click on an old poll reaches no view, so it fails in Discord. `on_outdated`
on the new class does not run, because that class answers only for version 3. For this poll in a store, that looks like this:

<ol class="risa-timeline">
<li class="risa-timeline__event"><span class="risa-timeline__when">Monday</span><strong>Version 2 sends a poll</strong><span>Its buttons route to version 2 and hold the record's key.</span></li>
<li class="risa-timeline__event"><span class="risa-timeline__when">Tuesday</span><strong>You release version 3</strong><span>New polls route to version 3. The old message stays as it was.</span></li>
<li class="risa-timeline__event"><span class="risa-timeline__when">Wednesday</span><strong>Someone clicks the old poll</strong><span>The click fails in Discord, unless a class at version 2 answers it.</span></li>
<li class="risa-timeline__event"><span class="risa-timeline__when">A week after the last click that read it</span><strong>The record expires</strong><span>The message stays, and a later click finds no record.</span></li>
</ol>

This suits messages nobody comes back to.

### Answer them all the same way

A class at the old version needs no fields, no handlers and no `state=`. Every old button finds no handler there, so
[`on_outdated`](#on_outdated) answers it. Add the class to the client as well. This one answers the oldest polls, from
version 1 in [Where state lives](08-where-state-lives.md):

```python
--8<-- "ch10_poll_bumped.py:retired"
```

--8<-- "previews/poll-outdated.html"

The old state is out of reach here. To replace the message, so nobody clicks it again, call
`ctx.edit(ui.TextDisplay("..."))` instead.

Each old version needs a class of its own, and a base class can hold the answer once:

```python
--8<-- "ch10_poll_retired.py:retired"
```

### Close the old message

To show what the old poll held, keep a class at the old version. Its fields and its `state=` have to match the old ones,
or risa cannot read the state.

```python
--8<-- "ch10_poll_bumped.py:old-fields"
```

Keep a method for each old button too, with the same name, version and arguments, and let its body say the poll is over.
Such a rejection handler routes like any other and logs nothing, and `self` holds the old state.

```python
--8<-- "ch10_poll_bumped.py:old-handlers"
```

--8<-- "previews/poll-ended.html"

Add both classes to the client. Each answers for its own version, so an old class in a store needs an `on_state_missing`
of its own as well.

```python
--8<-- "ch10_poll_bumped.py:client"
```

??? example "Full file"

    ```python
    --8<-- "ch10_poll_bumped.py:20:"
    ```

### Keep the old version running

Leave the old class as it was, with its `render()` and handlers, and give it another Python name. Old polls keep working
at version 2, and new polls start at version 3.

```python
--8<-- "ch10_poll_kept.py:old"
```

```python
--8<-- "ch10_poll_kept.py:client"
```

You now maintain two polls: a bug fix may have to go in both, for as long as the old messages matter.

??? example "Full file"

    ```python
    --8<-- "ch10_poll_kept.py:20:"
    ```

### Upgrade the old message

If the old state converts to the new, let the next click do it. These handlers build a version 3 poll from the version 2
state and count the vote. [`ctx.replace`](../api/context.md#risa.context.ComponentContext.replace) then turns the message
into that poll.

```python
--8<-- "ch10_poll_upgraded.py:upgrade"
```

--8<-- "previews/poll-upgraded.html"

- **`ctx.edit` cannot do this.** It takes only the buttons of the view that was clicked, and raises
  [`LayoutError`](../api/errors.md#risa.errors.LayoutError) for another's.
- **The old view stops here.** Its state is not saved, and `ctx.rerender()` and `ctx.edit()` raise
  [`NoViewError`](../api/errors.md#risa.errors.NoViewError).
- **The new state.** In a store, `ctx.replace` writes a new record under a new key, and the old one expires with its
  `ttl`. In a message, it raises `StateOverflowError` if the new state does not fit the new buttons.
- **Messages nobody clicks.** risa keeps no list of the messages it sent, so one that gets no click stays at version 2.
  It still needs this class when someone clicks it next.
- **Two clicks at once.** Both start from the same old state, and the second replaces the first with only its own vote.

When the old state cannot convert, close the message instead. A count of votes cannot say who chose what, so a version
that records it has nothing to fill it with.

??? example "Full file"

    ```python
    --8<-- "ch10_poll_upgraded.py:20:"
    ```

## Changing a handler

A handler has a route of its own, so you can change one and leave the rest of the view working. A button holds three
things about its handler:

| The button holds | Set by |
|---|---|
| `handler_id` | [`@risa.handler`](../api/view.md#risa.view.handler), the method's name by default |
| `version` | `@risa.handler`, 1 by default |
| A fingerprint of the argument types | The parameters after `ctx` that [`risa.bind`](03-handlers-and-arguments.md#what-can-be-an-argument) fills |

What stays outside these is free: the body, `defer`, a parameter's name, an injected parameter, a default added to an
argument, `int` to `hikari.Snowflake`, and a member added to a bound enum.

### Renaming a method

Pin the old name as `handler_id`, and buttons already sent reach the method under its new name. This method used to be
called `vote`:

```python
--8<-- "ch10_poll_handlers.py:renamed"
```

With no pin, the route changes with the name, and old buttons go to `on_outdated`.

### Changing its arguments

An option's name travels on its button, and in this poll, in a store, it can be 54 ASCII characters long before
`client.build` raises, and fewer of anything else, since a `str` takes its UTF-8 bytes. So
the buttons should carry the option's position instead, and `option: str` becomes `position: int`. Old buttons still hold
a `str`, so the new method gets a new `version`, and the method above stays at the old one.

```python
--8<-- "ch10_poll_handlers.py:version-two"
```

`render()` binds `self.vote`, which is version 2:

```python
--8<-- "ch10_poll_handlers.py:render"
```

Both methods route under the `handler_id` `vote`, and the button decides which one runs:

| Button | Holds | Reaches |
|---|---|---|
| Already sent | `vote`, version 1, a `str` | `vote_by_name` |
| Drawn by `render()` | `vote`, version 2, an `int` | `vote` |

`vote_by_name` ends in `ctx.rerender()`, which draws version 2 buttons, so an old poll moves over at its first click. Two
methods with the same id and the same version raise
[`DuplicateHandlerError`](../api/errors.md#risa.errors.DuplicateHandlerError) at `@risa.register`.

If you change the arguments and keep the version, each old button goes to `on_outdated` with an `ERROR` line that names
the fix.

??? example "Full file"

    ```python
    --8<-- "ch10_poll_handlers.py:20:"
    ```

### Retiring a handler

Delete the method once no old button matters. Unlike a raised view `version`, this reaches the current class, so its
`on_outdated` answers. To answer with what the view holds, keep the method and let its body say so, as
[the closed poll](#close-the-old-message) does. Such a method logs nothing.

### Sharing code between handlers

A handler cannot be called, and `self.vote(...)` raises
[`HandlerNotCallableError`](../api/errors.md#risa.errors.HandlerNotCallableError). Put what they share in a plain method,
as `_count` above. To run the handler itself, call `Poll.vote.func(self, ctx, 0)`, the plain function. It gets no
injection, so pass what it injects yourself.

### Which `version` to raise

| What changed | Raise | What it retires |
|---|---|---|
| One handler's arguments | The handler's `version` | The old buttons of that handler |
| The fields, or where the state lives | The view's `version` | Every button of every old message |

If both changed, raise the view's. The class you keep at the old version holds the old handlers.

## `on_outdated`

[`View.on_outdated`](../api/view.md#risa.view.View.on_outdated) answers a click that reaches a class but cannot run: its
handler has gone, its arguments changed, or the state no longer fits the fields. Data that looks broken, such as an id
that does not parse, stays away from it: the click fails in Discord, and risa logs a line.

- **It belongs to the class the button reaches.** That class is the one at the button's `name` and `version`, which is
  why a raised view `version` needs a class at the old one.
- **`ctx` has no view.** `ctx.respond` and a `ctx.edit` with nothing to click work, and `ctx.rerender()` raises
  [`NoViewError`](../api/errors.md#risa.errors.NoViewError).
- **Its parameters are injected**, as a [handler's](06-dependency-injection.md#where-injection-works) are. Your type
  checker wants `= risa.INJECTED` on them, since the base method takes only `ctx`.
- **Auto-defer does not cover it.** Answer within the three seconds Discord gives you.
- **By default it does nothing.** The click fails in Discord. Overriding it also lowers a `WARNING` about a missing
  handler to `DEBUG`.

[`on_state_missing`](09-stores-and-ttl.md#ttl) follows the same rules, for a record that is gone.
