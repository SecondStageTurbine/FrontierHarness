# Frontier 0.27.4

## Added

- Team mode can try to break its own work. Turn on **Try to break the team's work** in Project settings, and after the work and its review one more agent, from a model family other than the lead's and the workers', gets only the request and the code (no plan, reports or task board) and attacks the result in its own worktree: edge cases, invalid input, features meeting. Nothing it changes is kept. Its report reaches the lead as findings to check, not fixes to make. Needs a git repository; off by default, since it adds a turn to every team.

## Changed

- The team lead's review treats the workers' reports and passing tests as claims to check against the code, lists what the request requires and what it suspects, and after a round of fixes starts its second look from that list, rechecking every requirement rather than only the fixed ones.
