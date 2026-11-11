# Answering in time

Discord gives a click three seconds. This chapter shows each way to answer, and what to do when the work takes longer.
{: .risa-lead }

## The clock

[Discord's documentation](https://docs.discord.com/developers/interactions/receiving-and-responding) asks for a first
answer within 3 seconds of the click. Later answers go through a token that comes with the click, separate from your
bot's token, and it stays valid for 15 minutes.

<ol class="risa-flow risa-flow--4">
<li class="risa-flow__step"><span class="risa-flow__n">0 s</span><strong>The click</strong><span>The clock starts.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">2 s</span><strong>Auto-defer</strong><span>Answers for a slow handler, if turned on.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">3 s</span><strong>The deadline</strong><span>With no answer, the click fails.</span></li>
<li class="risa-flow__step"><span class="risa-flow__n">15 min</span><strong>The token ends</strong><span>Calls through it stop working.</span></li>
</ol>

Auto-defer acknowledges the click in the handler's place. An acknowledgement counts as the first answer, so the click
does not fail at 3 seconds, and the real answer can come later. The 2 seconds are the client's `auto_defer_delay`.

## Which message

Four calls answer a click, and they differ in which message they touch.

| Call | What it does | `AutoDefer` for a slow handler |
|---|---|---|
| [`ctx.rerender()`](../api/context.md#risa.context.ComponentContext.rerender) | Redraws the clicked message | `UPDATE` |
| [`ctx.edit(layout)`](../api/context.md#risa.context.ComponentContext.edit) | Replaces the clicked message | `UPDATE` |
| [`ctx.replace(view)`](../api/context.md#risa.context.ComponentContext.replace) | Turns the clicked message into another view | `UPDATE` |
| [`ctx.respond(...)`](../api/context.md#risa.context.Context.respond) | Sends a new message | `THINKING` or `THINKING_EPHEMERAL` |

On a view with no slow handlers, a fast handler needs no auto-defer, because each of these calls is an answer in itself.
[`AutoDefer.OFF`](../api/view.md#risa.view.AutoDefer) is the default. Under `THINKING`, a slow `rerender()` edits the clicked
message and leaves the thinking message with nothing to answer it, so a handler that redraws belongs with `UPDATE`.

## A slow handler

This report takes five seconds to build, so `refresh` and `export` need an auto-defer, and `close` needs one so that it
is not stuck behind them.

```python
--8<-- "ch05_report.py:view"
```

--8<-- "previews/report.html"

`refresh` redraws its own message. `UPDATE` acknowledges the click at 2 seconds with no sign the user can see, and
`ctx.rerender()` edits the message when the report is ready.

```python
--8<-- "ch05_report.py:refresh"
```

--8<-- "previews/report-refreshed.html"

`export` answers with a new message that only the person who clicked can see. `THINKING_EPHEMERAL` shows Discord's
"thinking..." message at 2 seconds, and `ctx.respond` takes its place.

```python
--8<-- "ch05_report.py:export"
```

--8<-- "previews/report-thinking.html"

--8<-- "previews/report-exported.html"

Keep `ephemeral=True` on the `respond`: a handler that finishes inside the 2 seconds answers through that call, with no
thinking message before it.

`close` replaces the message with a layout `render()` does not draw, so the view ends here. Clicks on one message run one
at a time, so a click on **Close** while `refresh` runs waits for it. `UPDATE` answers that click at 2 seconds, and the
edit follows when its turn comes.

```python
--8<-- "ch05_report.py:close"
```

--8<-- "previews/report-closed.html"

## By hand

You can also acknowledge a click yourself, as `ReportByHand` in the full file does.
[`ctx.defer()`](../api/context.md#risa.context.Context.defer) does it silently and right away, and
`ctx.defer(thinking=True, ephemeral=True)` shows the thinking message.

```python
--8<-- "ch05_report.py:defer"
```

`defer()` raises `AlreadyRespondedError` if the click already has its answer. It also runs only when the handler's turn
comes, so a click waiting behind a slow handler on the same message can miss the 3 seconds. Auto-defer's timer starts
before that wait, so use it on a view with slow handlers.

[`ctx.acknowledge()`](../api/context.md#risa.context.Context.acknowledge) takes the same options and does nothing if the
click already has its answer. It suits a handler with one branch that answers and one that does not.

```python
--8<-- "ch05_report.py:acknowledge"
```

## Where to set it

The handlers above set it for themselves. A view and the client take it too.

| Set it on | With | It applies to |
|---|---|---|
| A handler | `@risa.handler(defer=...)` | That handler |
| A view | `@risa.register(defer=...)` | Its handlers with no setting of their own |
| The client | `client_from_app(auto_defer=...)` | Handlers with neither setting |

The most specific setting wins, so `defer=risa.AutoDefer.OFF` on a handler turns auto-defer off for it.

## A handler that answers nothing

| Mode | When the handler returns |
|---|---|
| `OFF` | risa sends nothing, so the click fails in Discord. On a gateway bot risa logs nothing either |
| `UPDATE` | risa acknowledges the click silently |
| `THINKING`, `THINKING_EPHEMERAL` | Inside the 2 seconds, risa acknowledges the click silently. After them the thinking message is already up and risa sends nothing more, so answer it with `ctx.respond` |

A handler that changed a field without answering gets a rerender from risa when it returns, in every mode. That saves
the change, and it answers the click if the 3 seconds have not run out.

A handler that raises gets no acknowledgement in any mode. risa logs the exception, and the click fails unless the
handler or auto-defer answered first. An [error handler](#when-something-fails) can answer it instead, and if it returns
`True`, risa logs nothing.

??? example "Full file"

    ```python
    --8<-- "ch05_report.py:20:"
    ```

## When something fails

A failure can still get an answer. [`client.error_handler`](../api/client.md#risa.client.Client.error_handler) registers
a function that risa calls when a click fails. This view asks a weather service that is down, and its **Next city** has a
bug:

```python
--8<-- "ch05_errors.py:view"
```

--8<-- "previews/forecast.html"

This error handler answers the failure the bot expects:

```python
--8<-- "ch05_errors.py:expected"
```

--8<-- "previews/forecast-unavailable.html"

It gets the click's [`ExecutionPipeline`](../api/hooks.md#risa.hooks.ExecutionPipeline) and its context.
[`pipeline.find(ForecastUnavailableError)`](../api/hooks.md#risa.hooks.ExecutionPipeline.find) returns the first such
exception the click raised, or `None`. The answer goes through `ctx`. It returns `True` once it has dealt with the click,
and risa logs nothing more. With `False` it passes the click on.

A click can fail more than once, for instance in a [hook](13-hooks.md) and again in a hook after the handler.
[`pipeline.failures`](../api/hooks.md#risa.hooks.ExecutionPipeline.failures) lists every
[`Failure`](../api/hooks.md#risa.hooks.Failure) in the order it happened. Each holds the exception in `error`, where it
was raised in `stage`, and the hook that raised it, if any, in `hook`:

| `stage` | What failed |
|---|---|
| `HOOK` | A [hook](13-hooks.md) |
| `LOAD` | [`load()`](07-fresh-data.md), so the handler never ran |
| `HANDLER` | The handler |
| `ON_OUTDATED`, `ON_STATE_MISSING` | That fallback |
| `STATE` | Reaching the view's state, before any of your code ran: the store failed or is missing, its lock timed out, or a codec's extra is not installed |

A failure in risa's own save after the handler returns stays a log line. A hook after the handler that raises reaches
the error handlers like any other.

Several error handlers run highest `priority` first, and in the order you registered them within one priority, until one
returns `True`. They run once per click, after everything else in it. If none returns `True`, risa logs the click: one
entry that names each failure and carries their tracebacks as an `ExceptionGroup`, or a single line without a traceback
when the view's state could not be reached. A `True` covers the whole click, so its other failures go unlogged
too. A last error handler that answers every failure and returns `False` tells the person something went wrong and still
leaves the click in your log:

```python
--8<-- "ch05_errors.py:everything"
```

--8<-- "previews/something-went-wrong.html"

`@client.error_handler` without a priority registers at priority `0`, and
`client.error_handler(forecast_unavailable, priority=1)` registers a function that is defined elsewhere.

- **The view is gone.** `ctx` has no view state, as in [`on_outdated`](10-changing-a-view-safely.md#on_outdated):
  `respond`, an `edit` with nothing to click and `replace` work, and on a view with fields `rerender()` raises
  `NoViewError`, so a half-changed view is never saved.
- **Three seconds.** An error handler runs without auto-defer, and the three seconds count from the click. A failure
  that comes late, such as a lock that took ten seconds, leaves it no time unless auto-defer answered first.
- **What was sent before.** `respond` continues it, as it would in the handler: after an answer it sends a follow-up,
  and after a thinking message it replaces that.
- **Injection.** Its parameters after `ctx` are [injected](06-dependency-injection.md#where-injection-works), as a
  handler's are.
- **One that raises** is logged and counts as `False`.

??? example "Full file"

    ```python
    --8<-- "ch05_errors.py:20:"
    ```
