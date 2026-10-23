## Frontier 0.27.62

- Team mode reviews until the work is approved: the lead and the independent gate send fixes back to the workers round after round, and the team stops on its own when the review stalls, fails, or reaches the project's round limit (8 by default). Failing tests are always reported, whatever the verdict.
- New project setting, gate approver: only a GO from a reviewer model that wrote none of the change commits the team's work; the lead's own "done" never does. A same-family review is named as such.
- New project setting, NC channel: once an approved team commits, Frontier posts a handoff packet (branch, commit, reviewer, rounds, findings, test result) to that Slack channel, once per commit. A failed post leaves the commit in place; continue the team to retry it. Both settings are off unless set for a project.
