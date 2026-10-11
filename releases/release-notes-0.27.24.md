# Frontier 0.27.24

## Changes

- A session can listen to a Slack channel. Right-click it and choose **Listen to Slack…**, then name the channel. Every new message in that channel becomes a turn in the session, using the agent and posture currently selected. When the turn ends, the agent's reply is posted in that message's thread. Frontier checks every 30 seconds while it is running. Messages are handled one at a time, oldest first, and only top-level messages count. By default only your own messages count; turn on **Act on anyone's messages** to let other people give it work. It uses the tokens of the workspace's `slack` MCP server. Right-click the session again to stop.
