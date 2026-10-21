## Frontier 0.27.50

- Codex on Windows now gets the tools of MCP servers launched with `npx` (such as Playwright's browser and Slack). Codex starts servers without a shell, so a bare `npx` never launched and its tools went missing without an error; Frontier now runs such commands through `cmd /c`.
