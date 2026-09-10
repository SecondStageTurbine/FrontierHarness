# Verification — 2026-09-09

## Native acceptance

Launched with `npm run desktop:dev` and inspected the actual Tauri/WebView2 window using Windows native UI automation. No browser tab was needed for the product.

- Created **Desktop verification** from the native project dialog.
- Typed a calculator instruction in the persistent composer and pressed Enter.
- Observed real Anthropic discussion, planning, building, and review through the authoritative engine.
- The builder wrote `calculator.py`, `test_calculator.py`, and `README.md`.
- The integrated terminal displayed the actual `python -m unittest discover` result: **4 tests, OK, exit 0**.
- The reviewer approved the first run at **0.95** quality.
- Restarted the native runtime and observed the saved project and session return.
- Typed a follow-up instruction to add subtraction. The engine read all three current files, modified them, ran **6 tests, OK, exit 0**, and received approval.
- Inspected the real before/after README diff and terminal output in the optional right panel.
- Independently compared current project file contents with the final recorded changes. They match exactly.

Provider: `claude-haiku-4-5-20251001`, configured through an explicitly imported encrypted tenant credential. No hard-coded responses were used in these two runs. Pricing was not configured, so the UI correctly displayed **cost unavailable** while retaining provider-reported tokens. The evidence export contains no credentials: [live-verification.json](live-verification.json).

## Automated checks

| Check | Result |
|---|---|
| `python -m pytest -q` | **37 passed** after packaged routing, strict output, and Windows file identity checks were added |
| `npm run build` | TypeScript and Vite build passed |
| `npm run test:e2e` | Conversation acceptance test passed |
| Tauri debug compile and launch | Passed; native window running |

The isolated browser automation supplements native verification. It submits an instruction through the same React UI/API/engine, uses a deliberately rejected first review, observes iteration 2 approval, opens files/diffs/plans/reviews and actual subprocess output, reloads, sends a second instruction, navigates secondary settings/history, toggles themes, switches tenants, and verifies foreign project access is rejected. No JavaScript runtime errors were reported.

Backend checks also cover malformed artifacts, strict review consistency, configured fallbacks, provider errors, iteration circuit breakers and explicit continuation, budgets with unknown pricing, cross-tenant resource/attachment/model access, authenticated SSE replay, cancellation, startup recovery, source whitespace preservation, command failure overriding model approval, actual subprocess cancellation, specialist/prompt application, and path/secret exclusions.

A later review found and fixed four defects, each now covered by a test that fails without its fix. Project-check arguments rejected absolute paths only when the argument did not begin with a dash, and treated a Windows path with no drive letter as relative, so an option value could name a file outside the project; argument validation now runs on the caller's own arguments before the runner path is built, and rejects a rooted path wherever an option carries it. The manual terminal route spawned project checks without consulting the execution mode, so a project set to Edit files or Propose only still ran repository scripts; the mode gate now lives in the shared command path that every caller routes through. An interrupted model call recorded its charge as unknown, which permanently blocked a configured monthly budget and rejected every configured fallback model; such a call is now billed at the conservative bound the budget check had already reserved for it, so limits stay honest and no workspace is left holding an unreconcilable charge.

The same review found six further defects, also each covered by a test that fails without its fix. A build could not modify an existing file the project snapshot had skipped, because a file with no baseline hash is indistinguishable from one edited outside the run, which failed the whole build; projects in languages whose extensions were absent from the text list, and files past the per-file or total context budget, were therefore permanently unmodifiable. The snapshot now records a baseline for every file it can read and still sends only what fits in context. Windows path separators in a project-check command were deleted by POSIX-style argument splitting, so a relative test path failed and a recorded failure overrode reviewer approval. A file written with carriage returns hashed differently from what the next read returned, so an unchanged file failed the following iteration as changed outside the run; the baseline is now taken in the form a read returns, and identical text is no longer rewritten. Generated file names containing brackets, parentheses, a leading plus or an at sign were rejected after the build call had already been billed, so the artifact name rule now excludes what is unsafe or invalid in a path instead of listing what is allowed, leaving path containment to the resolver that already enforces it. The desktop shell treated its remembered port as mandatory, so anything else holding that port blocked every launch with no visible reason; it now falls back to any free port, and a launch failure is written beside the backend log. The desktop session expired after seven days even while the app was in use, leaving a login form that the automatically managed account can never pass; the stored session now extends on use, and an attempt to sign in to that account states that relaunching the app restores it.

Two narrower cases remain open. A project file beyond the two-thousand-file tree limit, or larger than the file reader accepts, still has no baseline and cannot be modified. A desktop app left idle beyond the session lifetime still needs a relaunch, which the sign-in message now says.

## Scope and remaining limits

Standalone installer packaging was completed in the follow-up: the release now bundles Python, backend dependencies, and frontend assets. Package checks ran both the built sidecar and the installed sidecar with system Python and Node.js removed from PATH, verifying unittest, pytest, compileall, HTTP authentication, static assets, and project/tenant file boundaries. See [packaged verification](packaged-verification.json).

The NSIS installer returned exit code 0 and registered Frontier 0.1.0 for the current Windows user. It installed `frontier.exe`, `frontier-backend.exe`, an uninstaller, and a Start menu shortcut. The installed native application was launched from `%LOCALAPPDATA%\Frontier`, with no repository/Python/Node locations on PATH. Its native window restored the previous project and session. The release launcher cannot fall back to repository Python.

Final real-provider verification also ran against the frozen release backend in an isolated temporary workspace with system Python and Node.js absent from PATH. It created a calculator and test file, executed the bundled test runner, and obtained reviewer approval. The exact tested backend checksum is recorded in the packaged verification report and compared with the installed backend.

Testing uncovered and fixed two issues before final delivery: the model could return incorrectly typed build-tool fields (closed build/review contracts now use provider-side strict decoding while retaining full local validation), and Windows path spelling differences could trigger a false external-edit conflict. A regression test now verifies casing equivalence, duplicate-path rejection, and real external-edit protection. The earlier failed native attempt remains in its session history rather than being rewritten as a success.

Installer checksums and installed-file verification are in [installer-manifest.json](installer-manifest.json). The bundle archive was checked for local databases, environment files, and encryption keys; none are included.

Those recorded checksums describe the build made before the defect fixes above. The backend sidecar, the desktop binary and the installer were all rebuilt afterwards and the installer packaged successfully, but that rebuild was not installed, so the sidecar checksum in [packaged-verification.json](packaged-verification.json) and every installed-file assertion in the manifest still refer to the earlier build. Refreshing them requires running the new installer and repeating the packaged-executable and installed-state checks.

Visual inspection resumed against the installed release after the interrupted inspection. The native window restored its project and conversation. Checked actual source previews, historical unified and split diffs, full structured plans, reviewer approval, recorded command output, contextual panel resizing and closing, the AI team dialog, and the new-session screen. These views rendered without obvious layout defects at the inspected window size. The app was left on a new session with the prompt composer focused. This was a visual inspection of existing results, not another live model execution.

A final same-version upgrade was tested while the installed desktop and frozen backend were running. The installer closed both processes, completed successfully in one pass, and installed files matching the current build and the live-tested backend checksum. Installer hooks handle backend shutdown before replacement to prevent retaining a locked older executable. The updated installed desktop was then relaunched.

Code signing, OS process sandboxes, automatic dependency installation, arbitrary interactive shells, file deletion, and autonomous web browsing are not implemented. Only the configured Anthropic provider was live-tested; other provider routes are implemented through the official OpenAI SDK and covered by isolated routing tests. Local project checks run with the current OS user's permissions. API/data tenant isolation is tested; adversarial process isolation requires an additional sandbox.

Dependency diagnostics remaining: two upstream Python testing deprecation warnings and non-failing Zod/Rollup comment-annotation warnings. There are no TypeScript/build/test failures.
