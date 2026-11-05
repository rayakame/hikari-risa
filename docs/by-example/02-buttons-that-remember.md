# Buttons that remember

A view's fields last from click to click and across restarts, and you store none of them. They travel in the message.
{: .risa-lead }

## Fields are the state

Every annotated field is saved. A new view starts from the defaults.

```python
--8<-- "ch02_scoreboard.py:fields"
```

--8<-- "previews/scoreboard.html"

To start somewhere else, pass values, as this preview does: `client.build(Scoreboard(home=3, away=1))`.

Each handler sees all the fields, whichever button was clicked, and `ctx.rerender()` draws the message from the new
values.

```python
--8<-- "ch02_scoreboard.py:handlers"
```

--8<-- "previews/scoreboard-after.html"

## What a click goes through

No view object waits in your bot between clicks. risa builds one for each click and fills it with the state from the
message.

--8<-- "previews/scoreboard-click.html"

The state comes from the message and the class from `add_view`, which your code calls at every start, so a restart
changes nothing.

!!! tip "Try it."
    Stop the bot between two clicks on **Home +1**. A click while it is down fails in Discord, and the next click after
    the restart carries on from the old score.

## Where the state sits

risa writes the state into the hidden id of a button, its `custom_id`. Discord stores these ids with the message and
sends them back with every click. These are the scoreboard's real ids, before and after a click on **Home +1**:

--8<-- "previews/scoreboard-ids.html"

The state fits on **Home +1**, so **Away +1** holds only its route. A click on **Away +1** still finds the score,
because Discord delivers the whole message with the click. State that does not fit on one button spreads over the
others.

A click changes only the id that holds the state. Someone whose client still shows the old **Home +1** gets a failed
click and can click again.

!!! warning "The score is not secret."
    Anyone who can see the message can read what the buttons hold, so keep secrets somewhere else.

## What a field can hold

A view is a `msgspec.Struct`, and a field can be anything msgspec encodes: numbers, strings, enums, lists, dicts, other
Structs. It all has to fit in the message, which has room for about 60 bytes on one button and about 70 more for each
further one, with the default codec. [Where state lives](07-where-state-lives.md) shows what you can do when a view
needs more.

For what should be read fresh instead of saved, such as a database row, mark the field `risa.Prop[T]` and fill it in
`load()`, as [Fresh data](06-fresh-data.md) shows.

??? example "Full file"

    ```python
    --8<-- "ch02_scoreboard.py:20:"
    ```
