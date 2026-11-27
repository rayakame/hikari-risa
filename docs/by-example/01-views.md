# Views

A view is a whole message. You describe it once in `render()`, and risa turns it into the components Discord draws.
{: .risa-lead }

## A view with no fields

The counter in [Your first view](../get-started/first-view.md) kept a number. This welcome message has no fields, so it
has nothing to remember. It draws a box with two lines of text and a button, and the button answers with a message only
the person who clicked can see.

```python
--8<-- "ch01_welcome.py:view"
```

--8<-- "previews/welcome.html"

--8<-- "previews/welcome-rules.html"

## From `render()` to Discord

`render()` returns one component or a sequence of them, and Discord draws them in that order.

| In `render()` | In Discord |
|---|---|
| `ui.Container(...)` | A box around the components inside it, like an embed |
| `accent_color=0xF680A1` | The colored bar down the left side of the box |
| `ui.TextDisplay("...")` | Text, in Discord's markdown |
| `ui.Row(...)` | A row of buttons, or one select menu |
| `ui.Button(self.rules, label="...")` | A button, and `self.rules` is the handler its clicks run |

[Selects, sections, galleries](04-selects-sections-galleries.md) shows the other components.

## A view is the whole message

A container or a text display makes the message a Components V2 message, and
[Discord's documentation](https://docs.discord.com/developers/components/using-message-components) says such a message
has no `content` and no embeds. Text goes in `ui.TextDisplay`, so `render()` draws everything the message shows.

## Register a view

[`@risa.register`](../api/view.md#risa.view.register) makes the class a view and gives it a name. A click finds its view
by that name, so keep the name once messages are out. [`client.add_view`](../api/client.md#risa.client.Client.add_view)
tells a client to answer for the view, once at startup.

```python
--8<-- "ch01_welcome.py:register"
```

Without `add_view` the message still sends, but its clicks fail in Discord, and `client.build` logs a warning once. A client made with
`risa.client_from_app(bot, use_global=True)` needs no `add_view`: it answers every view whose module has been imported.

## Send it

[`client.build`](../api/client.md#risa.client.Client.build) renders a view and returns the result, ready to send. How
you send it depends on where your code runs:

| Your code runs in | You send with |
|---|---|
| An event, such as a new message | `await rendered.send_to(bot.rest, event.channel_id)` |
| A lightbulb command or a risa handler | `await ctx.respond(**rendered)` |
| A raw hikari interaction with no answer yet, such as a slash command | `await rendered.respond_to(bot.rest, interaction)` |

Leave `content` out of a call that spreads a Components V2 view. [Rows alone](04-selects-sections-galleries.md#rows-alone)
are the one exception.

[`send_to`](../api/ui/build.md#risa.ui.build.Rendered.send_to) and
[`respond_to`](../api/ui/build.md#risa.ui.build.Rendered.respond_to) also take the other arguments of hikari's
`create_message` and `create_interaction_response`, such as `flags` or `attachment`, and pass them on. hikari lets a
mention ping nobody unless the call allows it, so a message that should ping reads
`await rendered.send_to(bot.rest, event.channel_id, user_mentions=True)`. Beside Components V2, both raise `TypeError`
for `content` or an embed.

The example uses the first row of the table: writing `!welcome` in a server channel posts the message. As with `!counter` in
[Your first view](../get-started/first-view.md#send-it), the bot needs **Message Content Intent** turned on in the
Developer Portal.

```python
--8<-- "ch01_welcome.py:send"
```

??? example "Full file"

    ```python
    --8<-- "ch01_welcome.py:20:"
    ```

## A message with nothing to click

Posted as a message of their own, the rules have nothing to click, so no click ever has to find a view for them. Such a
message needs no class, no name and no `add_view`. A function returns the layout, and
[`ui.render`](../api/ui/build.md#risa.ui.build.render) turns it into what `client.build` returns for a view. A layout has
no fields and no [`load()`](07-fresh-data.md), so what it shows comes in as the function's arguments.

```python
--8<-- "ch01_notice.py:layout"
```

--8<-- "previews/notice.html"

`ui.render` takes no client and no `await`. It works where no risa client is at hand, such as the error handler of a
lightbulb command, and its result sends through the three calls under [Send it](#send-it).

```python
--8<-- "ch01_notice.py:send"
```

In a lightbulb command or its error handler, and in a risa handler, it reads
`await ctx.respond(**ui.render(layout), ephemeral=True)`.

The layout may hold a [`ui.LinkButton` or a `ui.PremiumButton`](04-selects-sections-galleries.md#text-with-something-beside-it),
which take no handler. A button or a select menu with a handler raises
[`LayoutError`](../api/errors.md#risa.errors.LayoutError), since such a component routes through its view. Return that
layout from a view's `render()`.

??? example "Full file"

    ```python
    --8<-- "ch01_notice.py:20:"
    ```
