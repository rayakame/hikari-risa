# Changing a view safely

A message outlives the code that sent it. This chapter shows which changes old polls survive, and how to answer clicks
on the ones that do not.
{: .risa-lead }

## The free change

Add a field at the end of the class, with a default. This poll gets a note under its question.

```python
--8<-- "ch09_poll_free.py:view"
```

--8<-- "previews/poll-note.html"

An old poll keeps working. Its next click fills `note` with the default, `rerender()` draws it, and the votes are still
there. In a message the new field takes room too: with this `note`, the poll from
[Where state lives](07-where-state-lives.md#the-limit) has room for ten voters, and one that already holds ten or more
fails on its next vote. A field anywhere else, or without a default, is not free.

!!! tip "Try it."
    Send a poll with the Redis file from [Stores and TTL](08-stores-and-ttl.md#redis-and-postgresql) and stop the bot.
    Start the file below on the same Redis and click **Yes** on the old poll. It counts your vote and gains the note.

??? example "Full file"

    ```python
    --8<-- "ch09_poll_free.py:20:"
    ```

## A change that is not free

The poll should now let people change their mind, so a vote has to record who chose what. `yes` and `no` become lists,
and `voters` goes. An old record cannot say who chose what, so the view's [`version`](../api/view.md#risa.view.register) goes up.

```python
--8<-- "ch09_poll_bumped.py:fields"
```

--8<-- "previews/poll-bumped.html"

`version` is part of the route in each button's id. A click on an old poll still names version 2, and no view answers to
that, so the click fails in Discord. `on_outdated` does not run, because it belongs to
the class at version 3.

<ol class="risa-timeline">
<li class="risa-timeline__event"><span class="risa-timeline__when">Monday</span><strong>Version 2 sends a poll</strong><span>Its buttons route to version 2 and hold the record's key.</span></li>
<li class="risa-timeline__event"><span class="risa-timeline__when">Tuesday</span><strong>You release version 3</strong><span>New polls route to version 3. The old message stays as it was.</span></li>
<li class="risa-timeline__event"><span class="risa-timeline__when">Wednesday</span><strong>Someone clicks the old poll</strong><span>The click fails in Discord, unless a class at version 2 answers it.</span></li>
<li class="risa-timeline__event"><span class="risa-timeline__when">A week after the last click that read it</span><strong>The record expires</strong><span>The message stays, and a later click finds no record.</span></li>
</ol>

## Answering old polls

To answer those clicks, keep a class at the old version, with the old fields, and add both to the client.

```python
--8<-- "ch09_poll_bumped.py:old-fields"
```

```python
--8<-- "ch09_poll_bumped.py:client"
```

Keep the old handlers too, with the same ids, versions and arguments, and let their bodies say the poll is over. Such a
rejection handler routes like any other and logs nothing, and `self` holds the old record.

```python
--8<-- "ch09_poll_bumped.py:old-handlers"
```

--8<-- "previews/poll-ended.html"

## `on_outdated`

[`View.on_outdated`](../api/view.md#risa.view.View.on_outdated) answers a click that reaches a view but cannot run: its
handler has gone, or the record no longer fits the fields. `ctx` has no view, so `ctx.rerender()` raises
`NoViewError`. By default it does nothing, and the click fails in Discord.

```python
--8<-- "ch09_poll_bumped.py:outdated"
```

--8<-- "previews/poll-outdated.html"

If you do not need the old record, a class at the old version needs no fields, no handlers and no `state=`: give it this
method, and every old button lands there.

A handler has a [`version`](03-handlers-and-arguments.md#the-options-of-risahandler) of its own too. This is what each
change does:

| What you changed | A click on an old message | risa logs |
|---|---|---|
| Appended a field with a default | Works, while the state still fits | Nothing, or `ERROR` once it no longer fits |
| Raised the view's `version` | Fails in Discord, unless a class at the old version stays | `DEBUG` |
| Changed the fields or moved the view to a store, with no new `version` | `on_outdated` | `ERROR` |
| Changed a handler's arguments, with no new `version` | `on_outdated` | `ERROR` |
| Deleted a handler, or raised its `version` and kept no method at the old one | `on_outdated` | `WARNING`, or `DEBUG` once you override it |

A record that is gone goes to [`on_state_missing`](08-stores-and-ttl.md#ttl) instead.

??? example "Full file"

    ```python
    --8<-- "ch09_poll_bumped.py:20:"
    ```
