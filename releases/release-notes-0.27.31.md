## Frontier 0.27.31

- Agents can now see environment variables you name. Set the user variable `FRONTIER_PASS_ENV` to a semicolon-separated list (for example `OPYN_BOARD_URL;AGENT_TOKEN`) and Frontier hands exactly those to agents and their tools. Everything else stays out, as before, so no stray credential reaches an agent.
