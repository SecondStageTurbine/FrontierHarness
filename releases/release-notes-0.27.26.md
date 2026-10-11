# Frontier 0.27.26

## Changes

- Adaptive no longer lets an agent's track record raise its skill ratings, only lower them. A good record on routine coding used to raise every skill at once, including planning and review. That made one agent good enough for every turn, so it won on cost every time and built its record further. Planning, review and risky analysis go to the stronger agents again. A poor record still lowers an agent's ratings.
