# Hooks

A hook is a function risa runs before or after the handler of a click: a check that turns the click away, a limit, an
audit once it is done. Attach it to the client, a view or a handler.
{: .risa-lead }

## A check

Anyone can click **Close** on this poll, and only its author should. A check is a hook in the `CHECKS` step that raises
when the click may not go on:

```python
--8<-- "ch13_hooks.py:check"
```

[`@risa.hook`](../api/hooks.md#risa.hooks.hook) makes `author_only` a hook. It takes the
[`ExecutionPipeline`](../api/hooks.md#risa.hooks.ExecutionPipeline) of the click and the context, and because its third
parameter is annotated `Poll`, it also gets the poll with its durable state. `@risa.handler(hooks=[...])` attaches it to
`close` alone:

```python
--8<-- "ch13_hooks.py:view"
```

--8<-- "previews/closable-poll.html"

When someone else clicks **Close**, `author_only` raises, `close` never runs, and `NotTheAuthorError` goes to the error
handlers.

## Answering a check

Without an error handler the click fails in Discord, and risa logs it. This one answers instead:

```python
--8<-- "ch13_hooks.py:answer"
```

--8<-- "previews/closable-poll-refused.html"

[`pipeline.find`](../api/hooks.md#risa.hooks.ExecutionPipeline.find) returns the first exception of that type the click
raised, or `None`. `client.error_handler(not_the_author)` registers it, as the client under
[After the handler](#after-the-handler) does, and [Answering in time](05-answering-in-time.md#when-something-fails) covers
the rest of what an error handler can do. When the author clicks, the check passes and `close` runs:

--8<-- "previews/closable-poll-closed.html"

A hook can also answer the click itself through `ctx`, as a handler would. A handler that answers after it then sends a
follow-up.

## The steps

By default a click runs through six steps, in this order:

<ol class="risa-flow">
<li class="risa-flow__step"><span class="risa-flow__n">1</span><strong><code>MAX_CONCURRENCY</code></strong><span>How many run at once.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">2</span><strong><code>CHECKS</code></strong><span>Whether it may run.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">3</span><strong><code>COOLDOWNS</code></strong><span>How often it may run.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">4</span><strong><code>PRE_INVOKE</code></strong><span>Right before the handler.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">5</span><strong><code>INVOKE</code></strong><span><code>load()</code>, the handler, the save.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">6</span><strong><code>POST_INVOKE</code></strong><span>After the handler, even after a failure.</span></li>
</ol>

A step's name only says what it is meant for: risa runs every step's hooks the same way. `INVOKE` takes no hooks, and
`@risa.hook` raises [`HookDeclarationError`](../api/errors.md#risa.errors.HookDeclarationError) for it. Within a step, the
client's hooks run first, then the view's, then the handler's, each in the order of its list. A hook you attach in two
places runs twice.

On a view with durable fields, hooks run after risa has read the state and taken its lock, so a check sees the fields as
they are, and no other click on the message gets in between. Props are still at their defaults, because `load()` runs in
`INVOKE`. Hooks count toward auto-defer's delay and Discord's three seconds, as the handler does.

## Where a hook goes

| Attach it with | It runs for |
|---|---|
| `risa.client_from_app(..., hooks=[...])` | Every click on every view of the client |
| `@risa.register(..., hooks=[...])` | Every click on the view |
| `@risa.handler(hooks=[...])` | The clicks on that handler |

`client_from_lightbulb` takes `hooks=` too.

## With or without the view

A hook whose third parameter is annotated with a view class gets the view, and runs only on clicks that have one.
Annotate it with the class alone: for `Poll | None`, `client.add_view` raises `HookDeclarationError`. A hook without that
parameter also runs on clicks that go to the view's fallbacks:
[`on_outdated`](10-changing-a-view-safely.md#on_outdated) for a component the view can no longer route, and
[`on_state_missing`](09-stores-and-ttl.md#ttl) for a state that is gone. On such a click the fallback runs in place of
`INVOKE`, without auto-defer. Which hooks run:

| The click | Hooks without the view | Hooks that take the view |
|---|---|---|
| Reaches the view | The client's, the view's and the handler's | The client's, the view's and the handler's |
| Its stored state is gone or no longer fits | The client's, the view's and the handler's | None |
| Its handler was retired, or its wire arguments no longer fit | The client's and the view's | None |
| Cannot reach its state | None | None |

A click cannot reach its state when the store is down, the lock timed out, a codec's extra is missing or the client lacks
the view's store. It goes straight to the error handlers with a `STATE` failure.

A hook for one view class belongs on that view or its handlers, or annotate its view parameter with a base class of every
view it runs for. Your type checker refuses a `Poll` hook on the client, and `client.add_view` raises
`HookDeclarationError` for a hook that takes a view of another class. With `use_global=True`, a view never passes through
`add_view`, so there the hook fails each click with that error.

Parameters after these are [injected](06-dependency-injection.md), as a handler's are, and their types have to
[exist at runtime](06-dependency-injection.md#types-have-to-exist-at-runtime). risa also registers the pipeline for
injection, so a factory, or a function the hook calls, can take `risa.ExecutionPipeline`. A handler can take it too, but
there it is always empty: the handler runs only when nothing has failed.

## When a hook fails

A hook fails the click by raising. Then:

- **The hooks after it still run**, so every check has its say. A hook made with `skip_when_failed=True` is skipped
  once anything in the click has failed, the handler included.
- **If `INVOKE` has not run yet, it does not run**: not `load()`, not the handler, not a fallback.
- **`POST_INVOKE` runs**, so a hook after the handler can release what one before it took.
- **The view is gone from `ctx`.** From the first failure on, as in an error handler, `ctx.rerender()` on a view with
  durable fields and `ctx.edit()` with something to click raise `NoViewError`, so a half-changed view is never saved. A
  hook that takes the view still gets it to read.
- **The error handlers run once**, when the click is over. [`pipeline.failures`](../api/hooks.md#risa.hooks.ExecutionPipeline.failures)
  lists every [`Failure`](../api/hooks.md#risa.hooks.Failure) in the order it happened, each with its exception, its
  stage and the hook that raised it.

If no error handler returns `True`, risa's log names the hook by its function name, or by the `name=` you give
`@risa.hook`, which is also `failure.hook.name`.

## After the handler

The client runs `audit` after every click on every view:

```python
--8<-- "ch13_hooks.py:audit"
```

`make_client` attaches `audit` to the client and registers `not_the_author`:

```python
--8<-- "ch13_hooks.py:client"
```

`pipeline.failed` says whether anything in the click has failed so far, `pipeline.any_hook_failed` whether a hook did,
and `pipeline.invocation_failed` whether `load()`, the handler or a fallback did.

risa saves what the handler changed before `POST_INVOKE` begins, so a failing `audit` cannot cost a vote, and
auto-defer still acknowledges the click. A hook after
the handler that changes the view's state has to call `ctx.rerender()` to save it, which works only while nothing in the
click has failed. A change it leaves unsaved is lost, and if nothing in the click failed, risa logs a warning.

Hooks may change the state before the handler too, and the handler's save takes their change along. Keep that rare: the
change is lost if a later hook or the handler fails.

## Steps of your own

Within one step, the client's hooks run before the view's. To audit after every other hook, give the audit a step of its
own after `POST_INVOKE`:

```python
--8<-- "ch13_hooks.py:order"
```

`execution_step_order` holds `INVOKE` once and every other step at most once, or the client raises `ValueError`. A hook
in a step the order leaves out never runs, and risa logs a warning when the client is made or the view added.

## Testing a hook

A hook is callable, so a test can await it with a pipeline of its own. `ctx` is whatever stands in for the context in
your tests, such as a mock:

```python
--8<-- "ch13_hooks.py:test"
```

`risa.ExecutionPipeline([risa.Failure(error, risa.FailureStage.HANDLER)])` makes a pipeline that has failed, for an error
handler or a hook after the handler.

??? example "Full file"

    ```python
    --8<-- "ch13_hooks.py:20:"
    ```
