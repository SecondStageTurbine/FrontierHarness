Frontier 0.22.0

- Agents are judged by the public DeepSWE leaderboard (deepswe.datacurve.ai) where it lists them. Frontier reads it once a day; a model's pass rate there sets its coding, debugging, repository and tool-use strengths, and its cost per task sets its cost class (under $2 low, under $6 medium, above that high). Free and local models stay free.
- So team assignment and Adaptive pick the most capable agent that is cheapest for the job from current results rather than fixed guesses. Your track record on this computer still moves the numbers, and anything you set in an agent's Adaptive routing profile still wins.
- The team lead sees each agent's benchmark result and cost in the roster, and Agents & Providers shows it on each agent's card.
- Models the leaderboard does not list (local models, most free OpenCode models) keep their default profile and track record.
- Fixed: a free model whose name also matched another family (a free "flash" model) was ranked as low cost instead of free.
