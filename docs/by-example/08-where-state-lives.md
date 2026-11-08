# Where state lives

A view's state sits in its message until the message runs out of room. This chapter shows what you can do then, and how
to choose between the message and a store.
{: .risa-lead }

## What you can do

Smallest step first. The last column counts for the poll below, which fits twelve voters as it is:

| Step | What you write | Room for voters |
|---|---|---|
| Read it fresh | Mark the field [`risa.Prop[T]`](07-fresh-data.md) and fill it in `load()` from your database | The field takes none |
| [Another codec](#another-codec) | `state=risa.InMessage(codec=risa.codecs.BASE32768)`, with the `radixly` extra | 33 voters, or 30 with `URO14` |
| A store | `state=risa.InStore(ttl=...)`, as [Stores and TTL](09-stores-and-ttl.md) shows | No size limit |

A codec change leaves messages already sent working, since each id names its codec. A prop or a move to a store retires
them: a click on one fails in Discord, unless [a class at the old version](10-changing-a-view-safely.md#what-you-can-do)
answers it.

## A poll that outgrows its message

This poll counts the votes and keeps the id of everyone who voted, so that nobody votes twice.

```python
--8<-- "ch08_poll.py:fields"
```

**Yes** and **No** each ask this method first, and it adds the voter's id to the list.

```python
--8<-- "ch08_poll.py:first-vote"
```

--8<-- "previews/poll-already.html"

## The limit

Two buttons hold 140 bytes, framing included, and each voter takes 9 of them. With the question and the counts, twelve
voters fit:

--8<-- "previews/poll-full.html"

The thirteenth does not. [`client.build`](01-views.md#send-it) raises
[`StateOverflowError`](../api/errors.md#risa.errors.StateOverflowError), and the error lists what each field takes:

--8<-- "previews/poll-overflow.html"

A running poll gets this error from `ctx.rerender()` in the thirteenth voter's click. Nothing has been written yet, so
the message keeps twelve votes. If the handler lets the error through, risa logs it and the click fails in Discord. This
poll catches it and answers instead:

```python
--8<-- "ch08_poll.py:save"
```

--8<-- "previews/poll-full-reply.html"

`self` still holds the thirteenth vote. When the handler returns, risa tries to save it once more, finds that it still
does not fit, and leaves it at a `DEBUG` line, since the handler has dealt with it. A handler that goes on to change the
poll after the `except` saves that change together with the thirteenth vote, which does not fit either, so it takes the
vote back first.

At twelve voters the state fills both buttons:

--8<-- "previews/poll-full-ids.html"

!!! warning "The voters are not secret."
    `render()` shows only the totals, but the voters' ids travel in the pink part. Anyone who can see the message can
    read who voted.

Each vote also changes these ids. When tested, Discord refused a click on an old id, so of two people voting at the same
moment, the second can get a failed click and has to click again.

## Another codec

A codec decides how risa writes a view's state into the ids of its buttons. Discord allows 100 characters per id, however
many bytes each character takes, so a codec with more characters to choose from fits more into each one. The default,
[`BASE85`](../api/codecs.md#risa.codecs.BASE85), uses 85 ASCII characters and writes 6.4 bits with each.
[`BASE32768`](../api/codecs.md#risa.codecs.BASE32768) chooses from 32,768 characters and writes 15. When tested, ids of 100
such characters came back from Discord byte for byte.

| Codec | Bits per character | One button holds | The poll fits | A bound `str`, in ASCII characters |
|---|---|---|---|---|
| `BASE85` | 6.4 | 70 bytes | 12 voters | 68 |
| `BASE32768` | 15 | 165 bytes | 33 voters | 159 |
| [`URO14`](../api/codecs.md#risa.codecs.URO14) | 14 | 152 bytes | 30 voters | 147 |

The bytes include 9 of framing per view, so the default codec leaves about 60 for your state on one button, and about 70
for each further one. The last column is for a button with no state, as in
[What can be an argument](03-handlers-and-arguments.md#what-can-be-an-argument).

To switch, install the [`radixly` extra](../get-started/installation.md#extras):

=== "uv"

    ```sh
    uv add "hikari-risa[radixly]"
    ```

=== "pip"

    ```sh
    pip install "hikari-risa[radixly]"
    ```

Then name the codec on the view with [`risa.InMessage`](../api/state/placement.md#risa.state.placement.InMessage). Nothing
else in the view changes.

```python
--8<-- "ch08_poll_dense.py:register"
```

Compare the ids with the ones above: the same twelve voters now fit on the first button, and the second has no state at
all.

--8<-- "previews/poll-dense-ids.html"

You don't raise `version`, and messages already sent keep working. Each id names its codec, so risa reads an old message in
the codec it was written in, and writes it in the new one at its next rerender. A poll that was full gets its room with the
next vote.

`BASE32768` holds the most, so take it unless you have a reason not to. `URO14` holds a little less and writes only
characters from one CJK block. `InMessage(codec=...)` also takes a [`Codec`](../api/codecs.md#risa.codecs.Codec) of your own.

Keep these in mind:

- **There is still a limit.** The 34th voter raises `StateOverflowError`, where [a store](09-stores-and-ttl.md) has no limit.
- **The state stays readable.** The ids look scrambled, but anyone can decode them, so a codec is no encryption.
- **Each vote still changes the ids**, so two people voting at the same moment can still get a failed click.
- **The extra has to stay installed.** Without `radixly`, `@risa.register` raises
  [`CodecUnavailableError`](../api/errors.md#risa.errors.CodecUnavailableError) on a view that names the codec. A click on a
  message written in it logs an error that names the extra, and the click fails.
- **A codec of your own has to stay registered.** Once no view names it, call
  [`risa.codecs.register`](../api/codecs.md#risa.codecs.register) on it in every process that answers clicks. Otherwise risa
  takes its ids for another bot's and ignores them, so the click fails with no line in your log.
- **Going back can overflow.** A message that holds more than the old codec fits fails at its next rerender with
  `StateOverflowError`.

A codec is only for the message. `InStore` has no `codec`, since its buttons hold a short key.

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
    --8<-- "ch08_poll.py:20:"
    ```
