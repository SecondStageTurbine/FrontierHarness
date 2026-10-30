## Frontier 0.27.68

- Internal cleanup with no change in behaviour: duplicated backend code for running local tools (git, gh, npm, agent CLIs), the two built-in MCP servers' message loop, approval polling and Codex session reading now each live in one place.
- Unused frontend exports removed, and a Vulture whitelist added for the backend's framework-called code.
