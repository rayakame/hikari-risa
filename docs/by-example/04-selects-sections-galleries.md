# Selects, sections, galleries

A message can hold more than text and buttons: menus to pick from, an image or a button beside the text, galleries and
files. Each is one more component for `render()` to return, and [`risa.ui.nodes`](../api/ui/nodes.md) lists them all
with their options.
{: .risa-lead }

## Picking from a list

A `ui.TextSelect` fills a `ui.Row` by itself. `ui.SelectOption("Ramen", "ramen")` shows `Ramen` and hands you `ramen`,
and without the second argument the value is the label.

```python
--8<-- "ch04_selects.py:lunch"
```

--8<-- "previews/lunch.html"

A pick routes to a handler as a click does, and `ctx.values` holds the `value` of each option the user picked. A menu
takes one pick by default, and `min_values` and `max_values` change that.

```python
--8<-- "ch04_selects.py:lunch-handler"
```

## Picking a user, a role or a channel

Four more menus list what Discord already knows, so they take no options.

```python
--8<-- "ch04_selects.py:reviewer"
```

[`ctx.values`](../api/context.md#risa.context.ComponentContext.values) holds the ids of the picks, as strings, and
[`ctx.resolved`](../api/context.md#risa.context.ComponentContext.resolved) holds the objects, keyed by
`hikari.Snowflake`. `ctx.resolved` can be `None`, so the handler checks it first.

```python
--8<-- "ch04_selects.py:reviewer-handler"
```

--8<-- "previews/reviewer.html"

[Discord's documentation](https://docs.discord.com/developers/components/reference) lists what each menu sends:

| Menu | `ctx.values` | `ctx.resolved` |
|---|---|---|
| `ui.TextSelect` | The `value` of each pick | `None` |
| `ui.UserSelect` | User ids | `.users`, and `.members` in a server |
| `ui.RoleSelect` | Role ids | `.roles` |
| `ui.MentionableSelect` | User and role ids | `.users`, `.roles`, and `.members` in a server |
| `ui.ChannelSelect` | Channel ids | `.channels` |

??? example "Full file"

    ```python
    --8<-- "ch04_selects.py:20:"
    ```

## Text with something beside it

A `ui.Section` draws text with one accessory beside it: a thumbnail, a button or a link.

```python
--8<-- "ch04_sections.py:view"
```

--8<-- "previews/release.html"

`_ART` and `_NOTES` are URLs from the top of the file. `ui.Thumbnail` is a small image, and `description` is its
alternative text. The previews on this page draw a plain box where Discord draws an image.

A `ui.Button` as an accessory routes like any other button, and it holds the state as one in a row would.

```python
--8<-- "ch04_sections.py:handler"
```

`ui.LinkButton` opens a URL, and `ui.PremiumButton(sku_id)` offers one of your app's SKUs for purchase. Discord's
documentation says neither sends your bot an interaction and neither can have a `custom_id`, so they take no handler and
cannot hold state.

??? example "Full file"

    ```python
    --8<-- "ch04_sections.py:20:"
    ```

## Galleries, separators and files

`ui.MediaGallery` lays out images and videos in a grid, `ui.Separator` leaves space with a line in it, and `ui.File`
attaches a file.

```python
--8<-- "ch04_media.py:view"
```

--8<-- "previews/meetup.html"

`ui.MediaGalleryItem` takes a URL, as these do, or a file.

`ui.File` takes the file itself: a path, or data named through `hikari.Bytes` as here. Discord's documentation allows only
`attachment://` references for it, which hikari writes for you, so a URL will not do.

A rerender uploads the file again, because `render()` draws it again.

??? example "Full file"

    ```python
    --8<-- "ch04_media.py:20:"
    ```

## Rows alone

A tree of nothing but rows is what older bots send, and risa builds it from the same components. hikari does not set the
`IS_COMPONENTS_V2` flag for a row, so this shape reaches Discord as a V1 message, the kind from before Components V2. It
is the only shape that can carry `content` beside it.

```python
--8<-- "ch04_rows.py:view"
```

--8<-- "previews/yes-no.html"

`send_to` refuses `content`, so an event calls hikari directly.

```python
--8<-- "ch04_rows.py:send"
```

In a lightbulb command, or in a risa handler that takes
[`client: risa.Client`](06-dependency-injection.md#build-another-view) as a parameter after `ctx`, it reads
`await ctx.respond("Ship it?", **await client.build(YesNo()))`.

??? example "Full file"

    ```python
    --8<-- "ch04_rows.py:20:"
    ```
