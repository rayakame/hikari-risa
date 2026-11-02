# testbot

A gateway bot for trying risa against a real Discord application. It runs hikari, lightbulb
and risa together, the way a bot author would wire them, and logs everything risa does at
DEBUG.

## Setup

1. Create an application in the [Discord developer portal](https://discord.com/developers/applications),
   add a bot user and copy its token.
2. Invite the bot to a test server with the `bot` and `applications.commands` scopes.
3. Put the token in `testbot/.env`, which git ignores:

   ```sh
   TOKEN=your-bot-token
   ```

## Run

From the repository root:

```sh
uv run --group bot python testbot/bot.py
```

The `bot` dependency group brings lightbulb and python-dotenv. Slash commands are registered
on startup; a new command can take a moment to show up in the client.

## Commands

- **`/test`** posts the `Demo` view:
  - **Ping** answers ephemerally at once.
  - **Slow (20s)** sleeps for 20 seconds. Its handler opts in with
    `defer=risa.AutoDefer.UPDATE`, so the watchdog acknowledges the click silently before
    Discord's three-second window closes, and the answer arrives as a follow-up. Without
    the opt-in, the client default `AutoDefer.OFF` would leave the click unanswered.
  - **Pick a color** is a text select; the answer names the option.
  - **Pick a user** is a user select with up to ten users; the answer lists them from the
    resolved data.
- **`/poll`** posts `PollDemo`, a three-option poll that keeps its counts in the message
  itself (`InMessage`, the default placement). Each vote changes the state and rerenders.

## The restart test

`/poll` is what proves that state survives a restart:

1. Run `/poll` and vote a few times.
2. Stop the bot and start it again.
3. Vote on the *old* message. The count continues from where it was, because the state
   came back from the message's components, not from the process.
