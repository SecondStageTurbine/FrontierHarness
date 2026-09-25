Frontier 0.20.7

- Team mode: the lead sees each local model's context window in the roster and is told to give local models tasks that touch a few files, and to send tasks that read long logs, large diffs or much of the codebase (including most reviews) to a cloud agent.
- If a local model still runs out of context on a team task, Frontier hands that task once to a cloud agent instead of leaving it failed.
