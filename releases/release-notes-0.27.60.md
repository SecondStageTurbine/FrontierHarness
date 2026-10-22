## Frontier 0.27.60

- In a conversation that listens to Slack, the agent picker now also sets the agent that answers Slack. Before, Slack kept the agent picked when listening started, so a conversation set to Adaptive went on answering Slack, and doing the follow-up work, on that old agent, and the picker jumped back to it when the conversation was reopened. Changing it keeps the conversation's place in the channel and any reply it still owes.
