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

`load()` names `Scores` as a parameter, with the default `= risa.INJECTED` from
[Dependency injection](06-dependency-injection.md#name-it-as-a-parameter). `main()` registers it as that chapter shows,
or `client.build` raises linkd's `DependencyNotSatisfiableException`.
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
and the rows are new because `load()` has just read them.

```python
--8<-- "ch07_leaderboard.py:refresh"
```

`load()` runs inside the three seconds from [Answering in time](05-answering-in-time.md), and its time counts toward
auto-defer's 2 seconds.

## A handler that changes what `load()` reads

!!! warning "`load()` runs before the handler, and not again."
    A handler that sets `self.game` and calls `ctx.rerender()` draws the new heading over the old rows, because `load()`
    read them while `game` still held the old value.

Read them again in the handler. `switch` takes the game as a wire argument, as in
[Handlers and arguments](03-handlers-and-arguments.md#an-argument-per-button), and after it `scores`, which risa injects
as it does for `load()`. A handler's injected parameter needs no default.

```python
--8<-- "ch07_leaderboard.py:switch"
```

--8<-- "previews/leaderboard-go.html"

??? example "Full file"

    ```python
    --8<-- "ch07_leaderboard.py:20:"
    ```
