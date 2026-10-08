# Frontier 0.27.18

## New

- **/goal for every agent.** `/goal <objective>` gives the session one objective. After each turn Frontier sends the next one, with whichever agent is selected (Adaptive included), until the agent reports the goal complete (with evidence) or blocked. It pauses if a turn fails or is stopped, if the agent twice gives no progress report, or after 25 turns. `/goal resume` picks it back up and `/goal clear` stops it. A strip above the composer shows the goal, its status and the turn count.
- **Slash commands that really run.** The `/` menu now lists Frontier's commands, every command Claude Code reports (its built-ins, plugins and skills), your Claude and OpenCode commands on disk, and the commands that only exist in a tool's own screen. Claude Code and OpenCode run their commands as their own. Codex's commands, and screens such as Claude's `/config`, open that tool in the Terminal panel with the command typed in.
