# Frontier

Frontier is a Tauri desktop AI workspace backed by a tenant-scoped Python workflow engine. Open a project, give the AI team an instruction, and inspect the resulting plan, file changes, actual project checks, and review in the same session.

## Install the desktop application

Run `src-tauri/target/release/bundle/nsis/Frontier_0.1.2_x64-setup.exe`. The per-user Windows installer includes Frontier, its Python runtime, backend dependencies, and frontend assets. Launch **Frontier** from the Start menu afterward. It does not need this repository, Python, Rust, Node.js, or a terminal to run. Existing projects, sessions, and encrypted credentials stay in the same application-data directory.

The installer installs WebView2 if it is missing; that step needs internet access. This build is unsigned. Project-specific dependencies are separate: npm checks need Node.js, and projects with additional Python packages can supply a `.venv`.

## Run from source

Prerequisites: Node.js 22+, Python 3.11+, Rust, Windows WebView2, and the Visual Studio C++ build tools with a Windows SDK. This checkout was exercised on Windows with Python 3.14.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
npm ci
npm run desktop:dev
```

The last command builds the interface and opens a normal **Frontier** window. Tauri starts and owns the internal backend; no separate server command or browser tab is needed. Close the window to stop its backend and descendant project checks. The development launcher rebuilds Rust changes; run `npm run build` and refresh the native window for frontend changes.

On a fresh installation, use **Settings → Models & Providers → Connect model**. Supply the exact model identifier available to your account and its key, or an OpenAI-compatible local endpoint. Test connection checks model discovery; it does not generate text. Credentials are encrypted locally and never returned to the UI. The broker does not silently fall back to environment credentials.

### Using a subscription instead of an API key

Choose **Claude subscription** or **Codex subscription** as the provider to run a model without any API key. Frontier runs the agent command line tool you already have installed, and that tool signs in with your own Claude or ChatGPT subscription. Install the tool, sign in once in a terminal, then connect a model whose identifier is the name the tool accepts, such as `sonnet`. There is no key, endpoint or per-token rate to enter. Test connection confirms the tool runs; a sign-in problem surfaces on the first request.

This is the one place where credentials come from outside the application, so it is worth knowing what holds and what does not. The tool is launched with its own tools disabled and, for Codex, a read-only sandbox, so it cannot reach your project. It only returns an artifact, and the reviewer and the workflow's execution mode still decide what is written. Provider API keys are stripped from its environment, because an inherited `ANTHROPIC_API_KEY` would make the Claude tool bill per token instead of using the subscription. Usage is covered by the subscription, so these models are rated at zero and never consume a workspace budget. Per-stage token and temperature limits have no command line equivalent and are not applied. Codex reports only a combined token total, so its token counts show as partial.

An explicit convenience import is available for an existing Anthropic environment credential: launch with `HARNESS_IMPORT_ENV=1`. On first native launch, if the initial workspace has no models, this copies `ANTHROPIC_API_KEY` into its encrypted model configuration. `HARNESS_DEFAULT_MODEL` can specify the imported model ID. It does not share credentials with other workspaces.

To build the standalone Windows x64 installer:

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
npm run desktop:build
.venv\Scripts\python scripts/test_package.py
```

The build bundles the backend with PyInstaller, then creates a Tauri NSIS installer. Release builds refuse to fall back to a development checkout if their bundled backend is missing. The bundled Python runner supports unittest, pytest, and compileall without a system Python installation. Signing and automatic-update distribution are not configured.

## Everyday use

1. Create a project or open an existing folder using the native folder picker.
2. Select the AI team near the composer. Planner, builder, reviewer, and discussion models may use different providers. Optional specialists and saved prompts are under the selector's advanced disclosure.
3. Choose **Build & test**, **Edit files**, or **Propose only**.
4. Type an instruction and press Enter. Shift+Enter adds a line.
5. Follow compact role activity and inline plans/reviews. Open Files, Changes, Plan, Review, Terminal, or Logs as needed; panels resize and collapse.
6. Continue with another instruction in the same session. Current files and previous instructions/review feedback become the next run's context.

Ctrl+N starts a session, Ctrl+K finds a session, and Escape closes the contextual panel. Project/session selection, drafts, appearance, and working history survive refresh. The native backend uses a stable internal origin across launches to preserve WebView preferences.

The optional **Settings → Execution** area retains advanced saved-workflow editing, role prompts, fallback configuration, run history, comparison, and output exports. Ordinary project work never requires that editor or a workflow diagram.

## Execution and security boundaries

- The deterministic engine runs Discussion → Planning → Building → Review. It validates `PlanArtifact`, `BuildArtifact`, and `ReviewReport` with Pydantic. A rejected review returns its complete feedback to planning. Approval below the required quality threshold is rejected. Failed actual project checks also prevent approval.
- The backend owns tenant checks, model routing, workflow snapshots, counters, status, usage, and budgets. Foreign resource IDs raise `TenantIsolationViolationException`; switching workspaces clears cached resources and open event streams.
- Project file APIs reject traversal, linked paths, sensitive names, private app storage, and overlapping project roots across tenants. File writes preserve source whitespace, record before/after diffs, and stop instead of overwriting files changed externally since they were read.
- **Build & test executes trusted project code as the signed-in operating-system user. This is not an OS/container sandbox.** Database/API tenant isolation does not isolate arbitrary project processes from the local user's filesystem or network. Use Propose only for untrusted projects. Running hostile tenants on a shared server requires a separate execution sandbox and is outside this local desktop implementation.
- Supported checks are Python pytest, unittest, compileall, and npm test/build/test/lint/typecheck. Commands run in the project folder without a generated shell command and without provider keys in their environment. Each check has a 120-second timeout and bounded captured output. Dependencies must already be installed; arbitrary shells, dependency installation, interactive terminals, file deletion, and autonomous web browsing are not implemented.
- File context is bounded: up to 2,000 tree entries, 50 KB per context file, and 90 KB total file context. Every readable file still gets a content baseline, so a file too large to include in context can be modified while a file changed outside the run is never overwritten. Files the reader cannot open, such as binaries, cannot be modified at all, and every file left out of context is named to the model with the reason, so a missing document is reported rather than explained away. File preview supports UTF-8 text under 300 KB, and text-based PDFs up to 5 MB and 100 pages, which are read as extracted text and never written back.
- One active run per tenant protects budget accounting. Multiple backend workers on the same database are rejected. On interruption, outputs remain saved and calls are not automatically replayed, since an in-flight call may already have been billed.
- Token usage comes from providers where available. Cost is an estimate from administrator-entered rates, not a billing invoice. Unknown pricing/usage is displayed as unavailable and configured budget enforcement fails closed when it cannot estimate safely. A subscription login is rated at zero, so its cost is known to be nothing even when token counts are not reported.

Native application state lives under `%LOCALAPPDATA%\dev.frontier.harness`. Managed project folders are stored in the adjacent `Frontier Projects` directory, segregated by tenant. Back up the database and `secret.key` together. Deleting workspace records does not recursively delete user project folders.

## Verification

```powershell
.venv\Scripts\python -m pytest -q
npm run build
npm run test:e2e
```

The Python suite exercises tenant boundaries, cross-provider role routing, validation, rejection/replanning, iteration limits, cancellation, restart recovery, budgets, actual file writes and subprocess tests. The Playwright suite uses an isolated server and an explicitly injected test broker to exercise the conversation UI deterministically. Production has no simulated provider path or hard-coded model response.

The native window was also exercised with an actual Anthropic model: create project → composer instruction → three files written → four standard-library tests passed → reviewer approval, followed by continuation in the restored session. See [verification notes](docs/verification.md).

## Code map

| Area | Implementation |
|---|---|
| Native startup and backend lifecycle | `src-tauri/src/main.rs`, `backend/desktop.py` |
| Native launch authentication and project/session API | `backend/desktop_routes.py` |
| Contracts, authoritative state machine | `backend/schemas.py`, `backend/engine.py` |
| Multi-provider SDK routing | `backend/broker.py` |
| Tenant-scoped persistence and encryption | `backend/store.py` |
| File context, safe writes, diffs, captured checks | `backend/projects.py` |
| Authenticated HTTP API and replayable SSE | `backend/app.py` |
| Project shell, conversation, contextual tools | `src/desktop/` |
| Tenant-aware query cache and run synchronization | `src/app/context.tsx`, `src/app/useRun.ts` |

Both supplied build bibles were read before implementation. The repository initially contained only those specifications. The later desktop instruction supersedes the dashboard presentation; its backend contracts and advanced configuration capabilities remain part of this integrated application.
