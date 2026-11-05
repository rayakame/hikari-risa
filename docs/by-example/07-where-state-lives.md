# Where state lives

A view's state sits in its message until the message runs out of room. This chapter shows what you can do then, and how
to choose between the message and a store.
{: .risa-lead }

## What you can do

Smallest step first. The last column counts for the poll below, which fits twelve voters as it is:

| Step | What you write | Room for voters |
|---|---|---|
| Read it fresh | Mark the field [`risa.Prop[T]`](06-fresh-data.md) and fill it in `load()` from your database | The field takes none |
| Another codec | [`state=risa.InMessage(codec=risa.codecs.BASE32768)`](../api/codecs.md#risa.codecs.BASE32768), with the [`radixly` extra](../get-started/installation.md#extras) | 33 voters, or 30 with [`URO14`](../api/codecs.md#risa.codecs.URO14) |
| A store | `state=risa.InStore(ttl=...)`, as [Stores and TTL](08-stores-and-ttl.md) shows | No size limit |

A codec change leaves messages already sent working, since each id names its codec. A prop or a move to a store retires
them: a click on one fails in Discord, unless [a class at the old version](09-changing-a-view-safely.md#answering-old-polls)
answers it.

## A poll that outgrows its message

This poll counts the votes and keeps the id of everyone who voted, so that nobody votes twice.

```python
--8<-- "ch07_poll.py:fields"
```

**Yes** and **No** each ask this method first, and it adds the voter's id to the list.

```python
--8<-- "ch07_poll.py:first-vote"
```

--8<-- "previews/poll-already.html"

## The limit

Two buttons hold 140 bytes, framing included, and each voter takes 9 of them. With the question and the counts, twelve
voters fit:

--8<-- "previews/poll-full.html"

The thirteenth does not. [`client.build`](01-views.md#send-it) raises
[`StateOverflowError`](../api/errors.md#risa.errors.StateOverflowError), and the error lists what each field takes:

--8<-- "previews/poll-overflow.html"

A running poll gets this error from `ctx.rerender()` in the thirteenth voter's click. risa logs it, the click fails in
Discord, and the vote is lost.

At twelve voters the state fills both buttons:

--8<-- "previews/poll-full-ids.html"

!!! warning "The voters are not secret."
    `render()` shows only the totals, but the voters' ids travel in the pink part. Anyone who can see the message can
    read who voted.

Each vote also changes these ids. When tested, Discord refused a click on an old id, so of two people voting at the same
moment, the second can get a failed click and has to click again.

## Message or store

The message, [`risa.InMessage`](../api/state/placement.md#risa.state.placement.InMessage), is the default. It needs no infrastructure, and the state lives as long as the message does. Move to a store
when the view needs one of these:

| The view needs | A store gives it |
|---|---|
| Room | No size limit |
| A lifetime | A `ttl`, where state in a message never expires |
| Data not everyone should read | Buttons that hold a random key, with the state out of sight |
| Many people clicking at once | Ids that change only with a bound argument, so a vote leaves them as they are |

Where the bot runs counts too, as [Several processes](11-several-processes.md) shows.

??? example "Full file"

    ```python
    --8<-- "ch07_poll.py:20:"
    ```
