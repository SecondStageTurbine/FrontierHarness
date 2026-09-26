Frontier 0.23.1

- Gemini now runs through Google's Antigravity CLI (`agy`), which replaced the old `gemini` command; that one no longer signs in (it exited with code 55). Frontier finds `agy` where its installer puts it, sends the conversation on standard input, and maps Read only, Edit files and Full auto to agy's plan mode, accept-edits mode and skipped permissions.
- The setup wizard lists agy's own models (`agy models`, such as gemini-3.8-flash-high) with your agy default first. If you connected Gemini before, change its model identifier to one of those.
- When agy fails, Frontier shows agy's own reason instead of a guess about sign-in.
- The benchmarks match agy's model names too: gemini-3.8-flash-high is looked up as Gemini 3.8 Flash.
