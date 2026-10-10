# Frontier 0.27.23

## Changes

- The Slack entry in the MCP catalog now uses a maintained Slack server (`slack-mcp-server`). The previous package is no longer supported on npm. It takes a user (`xoxp-`) or bot (`xoxb-`) token, reads channels and threads, and posts only in the channel IDs you list in `SLACK_MCP_ADD_MESSAGE_TOOL`.
