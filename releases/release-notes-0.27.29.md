## Frontier 0.27.29

- Adaptive no longer treats GPT-6-Astra as a mid-priced agent. Its public benchmark price had put it beside gpt-6-sol, so every turn Sol fell short of went to Astra, the dearest Codex tier. A benchmark price can now only make an agent dearer than its family, never cheaper.
- When Jev rates a turn as low effort, Adaptive asks less of the agent, so an acknowledgement or status update goes to a cheaper agent instead of the strongest one.
