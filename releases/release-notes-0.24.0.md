Frontier 0.24.0

- When an agent cannot run a turn (not signed in, not installed, a model identifier it does not know, its service down, or its local server not running), Frontier hands the same turn to another agent instead of stopping. The next agent gets a handoff of what happened, and the conversation says who took over and why. Up to two handovers per turn.
- The replacement is the cheapest capable agent that is connected, running and not out of usage, and it comes from a different tool first, since a tool that is not signed in fails the same way for every model on it.
- Team mode: a worker whose agent cannot run is done by another agent, and its task card and track record follow the agent that actually did it. A lead that cannot run hands its seat to another agent, so the team goes on.
- An agent that ran out of time after doing work is not handed over: its work is in the folder, and the report says what happened. The answer of the agent you picked still stands; only an agent that could not run at all is replaced.
