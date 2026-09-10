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

## Scope and remaining limits

Standalone installer packaging was completed in the follow-up: the release now bundles Python, backend dependencies, and frontend assets. Package checks ran both the built sidecar and the installed sidecar with system Python and Node.js removed from PATH, verifying unittest, pytest, compileall, HTTP authentication, static assets, and project/tenant file boundaries. See [packaged verification](packaged-verification.json).

The NSIS installer returned exit code 0 and registered Frontier 0.1.0 for the current Windows user. It installed `frontier.exe`, `frontier-backend.exe`, an uninstaller, and a Start menu shortcut. The installed native application was launched from `%LOCALAPPDATA%\Frontier`, with no repository/Python/Node locations on PATH. Its native window restored the previous project and session. The release launcher cannot fall back to repository Python.

Final real-provider verification also ran against the frozen release backend in an isolated temporary workspace with system Python and Node.js absent from PATH. It created a calculator and test file, executed the bundled test runner, and obtained reviewer approval. The exact tested backend checksum is recorded in the packaged verification report and compared with the installed backend.

Testing uncovered and fixed two issues before final delivery: the model could return incorrectly typed build-tool fields (closed build/review contracts now use provider-side strict decoding while retaining full local validation), and Windows path spelling differences could trigger a false external-edit conflict. A regression test now verifies casing equivalence, duplicate-path rejection, and real external-edit protection. The earlier failed native attempt remains in its session history rather than being rewritten as a success.

Installer checksums and installed-file verification are in [installer-manifest.json](installer-manifest.json). The bundle archive was checked for local databases, environment files, and encryption keys; none are included.

A final same-version upgrade was tested while the installed desktop and frozen backend were running. The installer closed both processes, completed successfully in one pass, and installed files matching the current build and the live-tested backend checksum. Installer hooks handle backend shutdown before replacement to prevent retaining a locked older executable. The updated installed desktop was then relaunched.

Code signing, OS process sandboxes, automatic dependency installation, arbitrary interactive shells, file deletion, and autonomous web browsing are not implemented. Only the configured Anthropic provider was live-tested; other provider routes are implemented through the official OpenAI SDK and covered by isolated routing tests. Local project checks run with the current OS user's permissions. API/data tenant isolation is tested; adversarial process isolation requires an additional sandbox.

Dependency diagnostics remaining: two upstream Python testing deprecation warnings and non-failing Zod/Rollup comment-annotation warnings. There are no TypeScript/build/test failures.
