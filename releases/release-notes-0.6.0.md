Frontier 0.6.0 — git-native sessions

- The Changes panel opens with the repository: branch, staged and unstaged changes with diffs, stage and unstage, commit, push, and a commit message drafted by the current agent from the staged diff.
- Every editing turn is checkpointed as hidden git refs before and after. "Revert this turn" puts every file the turn touched back exactly, binaries included, and deletes what it created. Folders without a repository revert from the recorded before-text.
- A new session can start on a new branch in its own git worktree, so parallel sessions never collide. The agent, Files, Changes and project checks all work in that worktree. Remove the worktree from the session menu; the branch stays.
