Frontier 0.25.0

- Live activity: a new pulse icon at the top right, first of the panel buttons, opens **Activity**, which shows what the agent is doing as it happens: every command it runs, every file it reads or edits, a line of what each command returned, and what it says. The icon shows a green dot while an agent is working. After a turn ends, its activity stays readable until the next turn starts.
- It follows every agent: Claude Code, Codex, OpenCode and the Antigravity CLI. A handover to another agent shows in the same stream.
- In team mode, the team's conversation shows its lead and every worker together, each line labelled with the task it belongs to; a worker's own conversation shows just that worker.
- Claude Code now streams its progress to Frontier instead of reporting only at the end; replies, usage and errors read the same as before.
