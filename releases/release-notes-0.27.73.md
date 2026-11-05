## Frontier 0.27.73

- Listening to a Slack direct message no longer stops at `missing_scope` when the token cannot open DMs: Frontier finds your existing DM with that person instead.
- If the token still lacks a permission, the error names the Slack scope it needs, and you see it when you start listening rather than later.
