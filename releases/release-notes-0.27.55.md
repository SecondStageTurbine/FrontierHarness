## Frontier 0.27.55

- When an agent's reply says it is starting work ("I'm starting P9.21", "I'll build the scoring first") and then the turn ends, Frontier now tells it to do that work, once, in a new turn. This covers status answers to Slack, which used to promise work and then sit idle. The follow-up is marked as Frontier's, so it never repeats, and it hands over to another agent if the first one runs out of usage.
