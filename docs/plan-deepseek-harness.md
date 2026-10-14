# DeepSeek Harness in Frontier

Expose the installed DeepSeek Harness `headless` profile as a selectable agent and an Adaptive candidate.

- Discover the desktop bundle or a `dsh` command without invoking a shell shim for agent turns.
- Run `headless --json` with the prompt on stdin; use the terminal `final` event and show tool progress.
- The Harness profile owns model selection and credentials. Frontier does not copy or store them, and does not assume a subscription or zero token cost.
- Headless has no per-run permission mode, so admit it only for Full auto turns. Read only and Edit files must exclude it during manual selection, Adaptive selection and failover.
- Verify discovery, command construction, event parsing, routing and UI/build behavior with directed tests before release.

The installed Desktop bundle is `0.2.0-rc.2`. A real headless invocation currently ends with `MISSING_CREDENTIAL` for its `deepseek-official` route. Frontier reports that state without copying credentials or claiming a successful model turn.
