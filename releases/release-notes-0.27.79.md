## Frontier 0.27.79

- Sessions can find and talk to each other when you ask them to. Every agent now has three Frontier tools: list_sessions (every conversation in the workspace, across projects), read_session (another conversation's recent messages) and message_session (send it a message, which it takes as a new turn, queued if it is busy; the message names the sender so it can answer back). A read-only session can only start read-only turns elsewhere.
