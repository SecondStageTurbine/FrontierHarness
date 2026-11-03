## Frontier 0.27.69

- Adaptive routing now honours a message addressed as `Name (Fable, Opus fallback): ...`: the agents in the brackets are tried in the order written, so Fable takes the turn and Opus takes it when Fable is not running. Before, the bracketed names were missed and the router picked the cheapest agent.
