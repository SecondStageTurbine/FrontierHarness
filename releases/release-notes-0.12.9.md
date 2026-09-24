Frontier 0.12.9

- Agents are told that their turn ends when they reply: no "I'll report back", and background jobs they start are stopped when the turn ends, so they run long commands to completion and report the real result.
- A per-project turn time limit (Project settings, 5 to 240 minutes, 30 by default) for projects whose builds or engine tests run long.
- The window stays responsive while a turn starts in a large project: the folder scans before and after a turn run off the event loop instead of freezing it.
