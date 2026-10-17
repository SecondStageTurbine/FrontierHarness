## Frontier 0.27.39

- Slack messages that arrive during a running turn are no longer silent. A Claude turn takes them at its next step and can answer in the thread right away with a new slack_reply tool, then carries on. Any other turn reacts with 👀 and posts one line saying the message is queued, then answers it when the turn ends.
- Ctrl Enter during a Claude turn now hands your message to the running turn instead of stopping it. Other agents still stop and restart with the new message.
- A message handed in while the agent was finishing is answered too; both answers appear in the reply.
