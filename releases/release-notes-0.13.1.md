Frontier 0.13.1: resume Claude Code and Codex conversations

- Resume from CLI (sidebar) or /resume (composer) lists the recent Claude Code and Codex conversations for this project's folder, with the first message and date. Pick one and it becomes the current session.
- The same tool continues its own session natively (claude --resume, codex exec resume), keeping everything it read, ran and thought. Another agent picks it up from the transcript instead.
- Importing history no longer brings in the conversations Frontier's own turns leave behind in Claude's and Codex's folders.
