# Frontier 0.27.2

## Fixed

- OpenCode turns that failed with "Unexpected server error. Check server logs for details." now say why. OpenCode exits on that error before writing its log, so the cause is read from its error output and added to the message. When an OpenCode plugin is at fault, the message names the plugin and points at `opencode.json` to turn it off.
- `OPENCODE_DISABLE_*` settings (for example `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`, which stops OpenCode loading Claude Code's skills and makes its prompt much smaller) now take effect inside Frontier's OpenCode turns.
- Windows: Frontier no longer fills the temp folder. Each backend process unpacked a 16 MB `_MEI…` folder that was never deleted; Frontier now removes its own unused ones at start and every half hour.

## Changed

- Frontier writes its warnings, including why an agent turn failed, to `frontier.log` in its data folder (`%LOCALAPPDATA%\dev.frontier.harness`).
