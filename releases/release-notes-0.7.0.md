Frontier 0.7.0 — working alongside the agent

- The Terminal panel is now a real shell: PowerShell in one ConPTY per tab, opened in the conversation's folder, worktree included. Tabs survive closing the panel.
- The Files panel edits: type and press Save or Ctrl+S. Writes go through the same project boundary as reads.
- Search: a box above the file tree finds lines across the project and opens the file at that line; the sidebar search now also finds past conversations by their messages, across every project in the workspace.
- A file path the agent writes in inline code, such as `src/app.py:42`, opens that file at that line.
