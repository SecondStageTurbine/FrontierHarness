Frontier 0.20.6

- When an OpenCode agent fails, Frontier shows OpenCode's own error instead of "confirm the subscription is signed in". A local model that runs out of context now says so, with what to do about it: give it smaller tasks, or raise the model server's context size and the limit in OpenCode's settings.
- Every agent tool's error output now goes to the local backend log when a turn fails, not only Claude's and Gemini's.
