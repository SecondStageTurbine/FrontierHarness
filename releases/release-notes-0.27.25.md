# Frontier 0.27.25

## Changes

- A session listening to Slack now also answers replies inside a thread, so a teammate can answer it where the conversation already is. This needs **Act on anyone's messages** to be on. It follows threads under the latest 50 messages in the channel, and its answer goes into the same thread. Your own thread replies never count, because the agent posts as you and acting on them would loop.
- Only one session can listen to a given channel. Turning on a second one names the session already listening, so the same message is never answered twice.
- Fixed: the listener could treat its own `🤖 Frontier:` replies as new messages, because Slack stores the emoji by name.
