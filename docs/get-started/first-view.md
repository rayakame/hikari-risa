# Your first view

Build a counter: one message with one button, where each click raises the number. It runs on your own bot and keeps
counting after a restart.
{: .risa-lead }

## Before you start

- [Install hikari-risa](installation.md).
- Create an application in Discord's [Developer Portal](https://discord.com/developers/applications) and copy the token
  from its **Bot** page.
- Invite the bot to a server you can test in, with the scopes `bot` and `applications.commands` and the permission to
  send messages.
- Put the token in the environment variable `DISCORD_TOKEN`:

```sh
export DISCORD_TOKEN="your-bot-token"
```

In Windows PowerShell, that is `$env:DISCORD_TOKEN = "your-bot-token"`.

## The view

A view is a class. Its fields are its state, `render()` draws the message from them, and its handlers answer the clicks.

```python
--8<-- "first_view_gateway.py:view"
```

--8<-- "previews/counter.html"

--8<-- "previews/counter-after-click.html"

Part by part:

- `@risa.register(name="counter")` tells risa about the class. A click finds its way back to the view by this name, so
  keep the name once messages are out. Renaming a view retires the buttons already sent.
- `count: int = 0` is the state. risa keeps every annotated field from click to click, and the default is where a new
  counter starts.
- `render()` returns the message's layout, drawn from the fields. `ui.Container` is a box like an embed,
  `ui.TextDisplay` is text in Discord's markdown, and `ui.Row` holds buttons. [`risa.ui.nodes`](../api/ui/nodes.md)
  lists everything else you can draw.
- `ui.Button(self.click, label="+1")` draws a button, and `self.click` is the handler its clicks go to.
- `@risa.handler` makes `click` a handler. When it runs, the view already holds the state from the click, and `ctx` is
  the [context](../api/context.md) of that click.
- `await ctx.rerender()` answers the click: it draws the message again from the changed fields and saves them. Discord
  fails a click that gets no answer within three seconds.

## Send it

A view reaches Discord in two steps. Once at startup, you make a client and give it the class with `add_view`, so that
clicks find it. Then, for each message, `client.build(Counter())` renders a new view and you send the result.

The two ways below differ in what makes the bot post. Pick one.

=== "lightbulb"

    `/counter` posts the counter. Take this one if your bot gets its slash commands from
    [lightbulb](https://hikari-lightbulb.readthedocs.io/). You install it yourself, with `uv add hikari-lightbulb` or
    `pip install hikari-lightbulb`. The metadata of hikari-lightbulb 3.2.6 declares Python below 3.15.

    ```python
    --8<-- "first_view_lightbulb.py:send"
    ```

    [`risa.client_from_lightbulb`](../api/client.md#risa.client.client_from_lightbulb) makes a client that shares
    lightbulb's bot and its dependency manager. `build` returns a mapping, and `**` spreads it into `ctx.respond`.
    lightbulb registers `/counter` with Discord when the bot starts.

    ??? example "Full file"

        ```python
        --8<-- "first_view_lightbulb.py:20:"
        ```

=== "Gateway"

    Writing `!counter` in a server channel posts the counter. This is plain hikari, with no other package.

    Reading what people write takes a privileged intent. Turn on **Message Content Intent** on the **Bot** page of the
    Developer Portal, or Discord closes the connection when the bot starts.

    ```python
    --8<-- "first_view_gateway.py:send"
    ```

    [`risa.client_from_app`](../api/client.md#risa.client.client_from_app) makes a client for the bot. `send_to` sends
    the built view as a new message, here to the channel the command came from.

    ??? example "Full file"

        ```python
        --8<-- "first_view_gateway.py:20:"
        ```

A REST bot works the same way: [`risa.client_from_app`](../api/client.md#risa.client.client_from_app) takes it too, and
takes over its component listener.

## Run it

Save the full file of your tab as `bot.py` and start it:

```sh
uv run bot.py
```

If you installed with pip, that is `python bot.py`. Post the counter the way your tab does, then click **+1**. The
number in the message goes up.

## Restart it

Stop the bot with ++ctrl+c++, start it again, and click **+1** on the same message. The count carries on.

The count is not kept in your bot. When risa draws the button, it writes the count into the button's hidden id, which
Discord stores with the message and sends back with every click. On a click, risa reads the count out of the message,
rebuilds the view and runs your handler, and `ctx.rerender()` writes the new count onto the button. A restart loses
nothing, and the new process only needs `add_view`, which your code calls at every start.

A click that lands while the bot is down fails in Discord, which shows that the interaction failed. The count stays
where it was, and the next click after the restart works.

!!! warning "The count is not secret."
    Anyone who can see the message can read what the button holds, so keep secrets somewhere else.

The message also has limited room: about 60 bytes on one button, and about 70 more for each further one, with the
default codec. A view that needs more can move to a store with [`risa.InStore`](../api/state/placement.md). Its
handlers stay the same, but the move means raising its `version`, which retires the buttons already sent.

A click changes the id of the button that holds the count, and Discord refuses a click on an id the message no longer
has. If two people click at nearly the same moment, the second can fail in Discord and click again. In a store the ids
hold a key instead of the count, so clicks leave them as they are, which suits a view many people click at once.

!!! miru "In miru."
    A view stops answering after a restart unless you make it persistent. [Coming from miru](coming-from-miru.md) has
    the whole comparison.

!!! tip "Try it."
    Add a button that subtracts one: a second `@risa.handler` with `self.count -= 1`, and a second `ui.Button` in the
    row. Restart the bot and click **+1** on the old message. It keeps its count and gains the new button.

## Where to read more

- [`risa.view`](../api/view.md): views, handlers and `register`.
- [`risa.context`](../api/context.md): `respond`, `defer`, `rerender` and `edit`.
- [`risa.client`](../api/client.md): the client and its options.
- [`risa.state.placement`](../api/state/placement.md): `InMessage` and `InStore`.
