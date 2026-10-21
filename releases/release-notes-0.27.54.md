## Frontier 0.27.54

- When an agent finishes a turn by asking whether to start the work it just described ("Should I start the rebase now?"), Frontier now tells it to go ahead, once, instead of leaving the work waiting. A choice between options, anything that can't be undone (merge, deploy, delete, push to Slack and similar), a Read only turn, or a question put to a Slack sender still waits for you.
- The inspector's pop-out and close buttons stay visible however narrow the panel is; the tabs scroll instead of pushing them off.
