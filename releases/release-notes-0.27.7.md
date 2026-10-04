# Frontier 0.27.7

## Added

- Jev chooses how hard Codex thinks on each turn. When the workspace's router is a TypeSafe model, the same call that sizes up a message also picks a reasoning effort (low, medium, high or extra high), after the [opencode-jev-router](https://github.com/robertn702/opencode-jev-router) plugin. Every Codex turn runs at that effort, whether Adaptive chose Codex or you picked it yourself, so easy edits stop spending high-effort usage. The effort shows beside the turn's mode. Claude keeps its own effort setting, since changing it mid-conversation throws away its prompt cache. Without a TypeSafe router, and after Adaptive escalates to a stronger agent, each tool runs at its own default as before.
