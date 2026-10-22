## Frontier 0.27.57

- After an agent answers a Slack message, Frontier now always gives it one follow-up turn: if its reply names work of its own that is unfinished and not blocked, it does that work now; if nothing is its to do, or it is waiting on the sender's answer, it says so and stops. This replaces guessing from the reply's wording, which missed status answers such as "full validation remains unfinished. This is my next work." The Slack reply is posted as before, the follow-up never repeats, and it hands over to another agent if the first one runs out of usage.
