Frontier 0.13.0 — agents that test in a browser, Frontier as a tool, catch-up, and first-run setup

- Let agents use a browser (Preview panel): every editing turn in the project gets a real browser (Edge, through Playwright) to open the page it changed, read the console and take screenshots, which appear in the Preview panel.
- Frontier is an MCP server: add it to Claude Code, Codex or any MCP client to list projects, send a message to any agent here and get the reply, and catch up on a project.
- Since you were last here: returning to a project shows what happened, and Summarize has an agent write the catch-up.
- Set up your agents: first run finds the agent tools on this computer, offers the model names each accepts, and connects them in one go, or shows how to install what is missing.
- Tools now receive the Windows program-location variables, so they can find installed software such as Edge.
