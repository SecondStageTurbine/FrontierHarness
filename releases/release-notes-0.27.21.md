# Frontier 0.27.21

## Fixes

- Team subagents no longer stall on permission cards. A worker runs unattended in its own conversation, so a card there sat unseen until the fifteen-minute timeout denied it and blocked the task; a worker's permission request is now allowed at once (noted in its trace), while questions still wait for you.
