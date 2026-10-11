# Frontier 0.27.27

## Changes

- When a session listening to Slack answers someone else, its reply in the thread now mentions that person, so they are notified. The agent is told that `<@USERID>` is the way to mention anyone else. A plain `@name` notifies no one.
- Bold text and links in the agent's reply now use Slack's own markup, so `**bold**` no longer shows its asterisks.
