# Handlers and arguments

One handler can serve many buttons, and each button can carry what it stands for.
{: .risa-lead }

## An argument per button

A ticket has a priority, and you want one button for each value, all running one handler. An `enum.Enum` holds the
values.

```python
--8<-- "ch03_ticket.py:priority"
```

[`risa.bind`](../api/binding.md#risa.binding.bind) puts a value on a button, so `render()` binds one priority to each
button it draws.

```python
--8<-- "ch03_ticket.py:render"
```

--8<-- "previews/ticket.html"

The handler receives the value as a parameter after `ctx`.

```python
--8<-- "ch03_ticket.py:handler"
```

Use a field for what changes while the view lives, and an argument for what a button stands for.

## Where the argument sits

An argument travels in the button's `custom_id`, behind the route and any state, so risa's messages call it a wire
argument. These are the ticket's real ids:

--8<-- "previews/ticket-ids.html"

## What can be an argument

Arguments come right after `ctx` and are positional. They are annotated `bool`, `int`, `str`, `hikari.Snowflake` or an
`enum.Enum` of `int` or `str` values. Every parameter after them without a default is filled in by dependency injection
through linkd, as [Dependency injection](06-dependency-injection.md) shows.

An argument takes room on its button, out of what the button can hold of the state. With the default codec, a button with
no state holds a `str` of up to 68 ASCII characters, and a longer one makes `client.build` raise.
[Another codec](08-where-state-lives.md#another-codec) fits more.

??? example "Full file"

    ```python
    --8<-- "ch03_ticket.py:20:"
    ```

## hikari's enums

!!! warning "hikari's enums are not `enum.Enum`."
    Annotate a parameter `hikari.ChannelType` and risa counts it as something to inject, so it is no argument at all,
    and a value bound to it fails when `client.build` runs.

```python
--8<-- "ch03_hikari_enum.py:wrong"
```

--8<-- "previews/channel-kind-wrong.html"

To carry one on the wire, bind it as an `int`.

```python
--8<-- "ch03_hikari_enum.py:fixed-render"
```

--8<-- "previews/channel-kind.html"

Annotate the parameter `int`, and rebuild the enum in the handler with `hikari.ChannelType(kind)`.

```python
--8<-- "ch03_hikari_enum.py:fixed-handler"
```

??? example "Full file"

    ```python
    --8<-- "ch03_hikari_enum.py:20:"
    ```

## The options of `@risa.handler`

Used bare, [`@risa.handler`](../api/view.md#risa.view.handler) takes the defaults. With options, it takes keywords, as
in `@risa.handler(handler_id="pick", version=2)`.

| Option | What it does |
|---|---|
| `handler_id` | The id the handler routes under, the method's name by default |
| `version` | Bump it when the handler's arguments change |
| `defer` | The handler's [`AutoDefer`](05-answering-in-time.md#where-to-set-it), overriding the view's |
| `hooks` | [Hooks](13-hooks.md#where-a-hook-goes) that run for clicks on the handler |
| `load` | `False` [skips the view's `load()`](07-fresh-data.md#a-handler-that-needs-no-load) for clicks on the handler |

[Changing a handler](10-changing-a-view-safely.md#changing-a-handler) shows what each change does to buttons already sent,
how `handler_id` keeps them working through a rename, and how a method at the old `version` answers them.
