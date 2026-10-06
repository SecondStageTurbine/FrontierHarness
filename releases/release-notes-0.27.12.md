# Frontier 0.27.12

## New

- **Verified delivery** (Project settings). When a team finishes, agent-stack seals the changed files and has the other model family review exactly those. Claude reviews work by Codex, OpenCode and Gemini; Codex reviews Claude's. A NO-GO sends its findings back to the workers once, even if the lead would have accepted the work. Only a GO is committed, locally and never pushed. A NO-GO, a timeout or an error leaves the changes uncommitted and says why. The team card shows the review as it runs. Needs agent-stack installed and a git repository with at least one commit.
- Adaptive routing now skips a Claude or Codex subscription whose usage window is at 100% until that window resets, using the limits Frontier already reads for the Usage view, instead of losing a turn to find out.
