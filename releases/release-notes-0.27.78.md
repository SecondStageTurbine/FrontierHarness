## Frontier 0.27.78

- Adaptive now keeps a conversation with the agent that answered the last turn when that agent can still do the next one at no extra cost. Staying lets the agent continue its own session instead of a new agent re-reading the whole conversation. It still moves to a cheaper agent when one is enough, and to a stronger one for high-risk work or when the last turn failed.
