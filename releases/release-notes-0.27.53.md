## Frontier 0.27.53

- New **Turns** panel in the inspector: every finished turn with its agent, time, tokens and prompt size, plus what the latest prompt was made of (instructions, rules, project notes, board, summary, transcript), and any dropped messages, compaction, escalation or fallback.
- A turn that was running when Frontier closed is now marked **Interrupted** instead of failed, with a **Resume turn** button that runs it again from the conversation. Nothing reruns until you click it. An interrupted team can be picked up with Continue team.
- Frontier's own agent tools carry shorter descriptions, so every turn spends less context on them.
