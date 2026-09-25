Frontier 0.23.0

- Terminal-Bench (tbench.ai) joins DeepSWE as a second public benchmark. It tests models inside the agents themselves, Claude Code and Codex, on terminal work: builds, environments, debugging. Frontier reads it once a day, and where it lists a model it sets that agent's tool-use and debugging strengths; DeepSWE keeps setting coding, repository and cost.
- This separates agents DeepSWE rates alike: GPT-6 Astra and Fable 5.1 pass 58% in their own tools, GPT-5.6 Terra 22% and Sonnet 5 12%, so hard debugging and environment work goes to the agents that do it well.
- The team lead's roster and each agent's card in Agents & Providers show both results.
- Terminal-Bench publishes no data file, so Frontier reads the results from its page. If the site changes and they can no longer be read, Frontier logs it, keeps the last copy, and carries on with DeepSWE and your track records.
