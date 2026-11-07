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
handler or auto-defer answered first.

??? example "Full file"

    ```python
    --8<-- "ch05_report.py:20:"
    ```
