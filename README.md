# Frontier

Frontier is a harness. You hold one conversation about one project folder, and you choose which
agent answers each turn — Claude, Codex, or OpenCode, including a local model through OpenCode.
Switching agent mid-conversation costs nothing: the conversation lives here, not inside any
tool's own session, and the agent taking over is given it along with the folder the previous one
was working in.

Every agent is agentic. A turn runs the agent command line tool you already have installed, in
your project folder, with its own tools: it reads, writes, and runs commands there. What it may
do is chosen per turn — read only, edit files, or full auto — and Frontier records what the
folder looked like before and after.

## Install the desktop application

Run `src-tauri/target/release/bundle/nsis/Frontier_0.7.0_x64-setup.exe`. The per-user Windows installer includes Frontier, its Python runtime, backend dependencies, and frontend assets. Launch **Frontier** from the Start menu afterward. It does not need this repository, Python, Rust, Node.js, or a terminal to run. Existing projects, sessions, and encrypted credentials stay in the same application-data directory.

Frontier checks GitHub releases once at launch. When a newer version is published, a banner offers **Install and restart**; the installer runs for the current user and the app reopens on the new version. **Settings → General → Check for updates** does the same on demand. Feeds are signed: the app only installs a package whose signature matches the public key built into it.

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

Choose **Claude subscription**, **Codex subscription** or **OpenCode subscription** as the provider to run a model without any API key. Frontier runs the agent command line tool you already have installed, and that tool signs in with your own subscription. Install the tool, sign in once in a terminal, then connect a model whose identifier is the name the tool accepts, such as `sonnet` for Claude or `opencode-go/glm-5.3` for OpenCode. There is no key, endpoint or per-token rate to enter. Test connection confirms the tool runs; a sign-in problem surfaces on the first request.

This is the one place where credentials come from outside the application, so it is worth knowing what holds and what does not. The tool runs in your project folder with its own tools, which is what makes it an agent rather than a text generator; how much it may do is the per-turn posture below and is never escalated on its own. Provider API keys are stripped from its environment, because an inherited `ANTHROPIC_API_KEY` would make the Claude tool bill per token instead of using the subscription. Codex reports only a combined token total, so its token counts show as unavailable.

An API key is not an agent. A model reached with a key has no tool loop, no file access and no shell, so keyed providers cannot take a turn; they stay configured and are used for dictation. A local model becomes an agent through OpenCode, which supplies the tool loop and can point at Ollama, LM Studio, or any OpenAI-compatible local endpoint.

### Connecting more than one subscription

A subscription login can carry a **subscription account** name, such as `work` or `personal`. Each name gets its own credential directory inside the workspace, so a second Claude, Codex or OpenCode subscription is connected beside the first instead of replacing it. **Sign in** on the model card prints the two lines that sign that account in: the command line tool writes its credential into that directory and reads it back from there, and Frontier never sees it. Leave the account name empty to keep using the sign-in already on this machine, which is what every existing model does.

Connect a second subscription by adding a second model with the same provider and the same model identifier under a different account name. When one of them answers that its usage window is spent, the run moves to the next connected account and continues; the stage records the login that actually answered, and the spent one is skipped for an hour before it is tried again. A login that is simply not signed in is reported rather than switched away from, because that is a sign-in the workspace still has to do. With only one account connected, a spent subscription fails the stage exactly as before.

### Dictation

A **Dictate** button in the composer records from the microphone and types the transcription into the field. Transcription goes through an OpenAI or OpenAI-compatible model this workspace has already connected, so it uses the same encrypted credential as everything else and needs no separate key; connect one whose identifier is a transcription model, such as `gpt-4o-mini-transcribe`. The button does not appear until such a model exists. The recording is posted, transcribed and dropped: it is how a prompt was typed, not an artifact of the workspace.

An explicit convenience import is available for an existing Anthropic environment credential: launch with `HARNESS_IMPORT_ENV=1`. On first native launch, if the initial workspace has no models, this copies `ANTHROPIC_API_KEY` into its encrypted model configuration. `HARNESS_DEFAULT_MODEL` can specify the imported model ID. It does not share credentials with other workspaces.

To build the standalone Windows x64 installer:

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
npm run desktop:build
.venv\Scripts\python scripts/test_package.py
```

The build bundles the backend with PyInstaller, then creates a Tauri NSIS installer. Release builds refuse to fall back to a development checkout if their bundled backend is missing. The bundled Python runner supports unittest, pytest, and compileall without a system Python installation.

Updates are signed with a minisign key that is not in this repository. Set `TAURI_SIGNING_PRIVATE_KEY` to the private key's text (PowerShell: `$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content ~/.tauri/frontier.key -Raw`) before `npm run desktop:build` so the installer gets its `.sig`; an already built installer can be signed afterwards with `npx tauri signer sign -f ~/.tauri/frontier.key <installer>`. Then run `.venv\Scripts\python scripts/write_update_manifest.py` to write `latest.json` beside it. A GitHub release for tag `v<version>` needs the installer, its `.sig`, and `latest.json`; installed copies read `releases/latest/download/latest.json`. The public key lives in `src-tauri/tauri.conf.json`; losing the private key means shipping a new key with a manually installed version. Authenticode signing of the installer itself is not configured.

## Everyday use

1. Create a project or open an existing folder using the native folder picker.
2. Pick an agent next to the composer, and what it may do this turn.
3. Type a message and press Enter. Shift+Enter adds a line.
4. The agent works in the folder. When it finishes, its reply appears with the files it changed, how long it took, its tokens in and out, and its cost when the model has rates; open Files, Changes, or Terminal as needed. **Revert this turn** under the changed files puts every file the turn touched back to how it was before it.
5. Pick a different agent whenever you like. The next turn goes to it, and it is given this conversation and the same folder.

While a turn is running, Enter queues the next message for the moment it finishes, and Ctrl+Enter stops the turn and sends the new message instead. A turn is one opaque subprocess, so that is what steering means here: what the agent had already written to the folder stays, and the new message is sent with the conversation so far. Queued messages are shown under the conversation and can be removed; stopping a turn drops its queue.

### Working alongside the agent

The **Terminal** panel is a real shell: PowerShell on Windows, your login shell elsewhere, one ConPTY per tab, opened in the folder the conversation works in, worktree included. It runs with your full environment as your own terminal would, unlike agent tools, whose environment is stripped of provider keys. Shells outlive the panel: close and reopen it and the same sessions are still there. Closing Frontier ends them. In a browser, where there is no native process, the panel falls back to the bounded project check runner described under execution boundaries.

The **Files** panel edits: open a text file, type, and press **Save** or Ctrl+S. The write goes through the same path boundary as every read, so credentials, linked paths, and anything outside the project stay untouchable. A save while a turn is running is attributed to that turn, as any write during a turn is. The search box above the tree finds lines in every readable text file; each hit opens the file at that line. The sidebar search still filters sessions by name and now also searches their messages across every project in the workspace, listing matches under **In messages**.

A path the agent writes in inline code, such as `src/app.py:42`, is a link: it opens the file in the Files panel at that line.

### Git

When the project folder is a git repository, the **Changes** panel opens with the repository: the current branch, what is staged, and the working tree. Tick a change to stage it, open it to read its diff, write a commit message or press **Write message** to have the current agent draft one from the staged diff under Read only, then **Commit** and **Push**. These are your own commits made by Frontier's git, so they need no posture; an agent's commits still need Full auto. Below the repository, the panel keeps the per-turn record it always had.

Before and after every turn that may edit files, Frontier checkpoints the whole working tree as hidden git objects under `refs/frontier/checkpoints`, untracked files included and ignored files excluded. Nothing is committed to your branch and the index is untouched. **Revert this turn** restores every path the turn changed from its checkpoint, exactly and binaries included, and deletes files the turn created; later edits to those same files are undone with it. A folder that is not a repository still records each turn's readable before-and-after, and reverts from that.

A new session can start **On a new branch**: Frontier adds a git worktree beside its managed projects, in `Frontier Worktrees` next to the application data, on a branch named from your first message. The agent, the Files panel, the Changes panel, and your own project checks all work in that worktree, so parallel sessions never write over each other. The branch chip in the session header says where you are. Right-click the session and choose **Remove worktree** to delete the folder; the branch and its commits stay in the repository, and the conversation continues in the project folder.

Right-click a session to rename, pin, or archive it. Pinned sessions stay at the top; archived ones move to an **Archived** list at the bottom of the sidebar. A turn that finishes while Frontier is in the background raises a desktop notification with a short chime; **Settings → General** turns the chime or the notification off.

Ctrl+N starts a session, Ctrl+K finds a session, and Escape closes the contextual panel. Project and session selection, drafts, and appearance survive a restart.

### What an agent may do

Chosen per turn, and translated into each tool's own setting:

| Posture | Claude Code | Codex | OpenCode |
|---|---|---|---|
| Read only | `--permission-mode dontAsk --disallowedTools Bash Edit Write MultiEdit NotebookEdit` | `--sandbox read-only` | `--agent plan` |
| Edit files | `--permission-mode acceptEdits` | `--sandbox workspace-write` | `--agent build` |
| Full auto | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` | `--agent build --auto` |

A commit or push needs **Full auto**. Under Edit files, Codex's sandbox keeps `.git` read-only and blocks the network, and Claude has no one to approve a shell command; the agent is told this on every turn so it asks for Full auto rather than asking you to run git by hand.

### Adaptive

Pick **Adaptive** in the agent selector and the harness chooses the agent for each message: the least expensive one that can reliably do it, escalating to a stronger one if that agent fails. A manual pick is never routed; Adaptive is consulted only when it is what the selector says.

It routes by comparing what a message needs against what each agent is good at, never by naming a provider. Each connected agent carries a capability profile — coding, reasoning, planning, debugging, architecture, review, tool use, repository understanding, instruction following, speed, each 0–10 — with defaults by provider and model family that any row can override under **Adaptive routing profile** in Agents & providers. That profile is the registry: a new agent, local or cloud, is a model row plus at most those numbers, and the router does not change. A message is classified by deterministic heuristics on its wording and on a cheap walk of the project folder (size, languages, and whether it touches auth, data or payment paths), optionally refined by a classifier named as the workspace's **Adaptive classifier** in Workspaces, and always falling back to the heuristics if that classifier fails. Length is not a signal: "Fix authentication." is short and hard.

The classifier is either a **TypeSafe** connection (provider "TypeSafe (classifier)" in Agents & providers — connect it with just an API key, then select it in Workspaces) or any other keyed model, a local Ollama model being the intended case for the latter. TypeSafe answers a fixed set of typed, calibrated questions — what kind of request this is, whether it touches many files, whether it names a concrete target or a vague symptom, and whether it touches auth/data/money/an external service — instead of being asked to write JSON and hope it parses; the same rule that turns a signal into a capability requirement runs either way, so the two classifiers only ever differ in how confidently they read the message. TypeSafe never takes a turn and is sent the message text and the same non-sensitive project summary the heuristics already compute — never file contents.

Candidates that lack an agent loop are out. Of the rest, the cheapest whose profile covers every requirement within a point wins; if none does, the closest. A turn that fails, or whose agent says it cannot and changed nothing, is escalated one rung — the weakest sufficient agent stronger than those already tried — with a handoff that names the objective, what each earlier agent did, and which files are already changed, so it continues rather than starts over. At most two escalations, then the turn is reported failed. The message records the decision: what was needed, who was chosen and why, and each attempt.

**Local models that take turns.** Several local models can be connected while only one holds the GPU at a time. OpenCode's own config names the server each local provider talks to, so Frontier asks each loopback server whether it is up and serving that model: the agent picker shows an offline local agent greyed with "offline", Adaptive never counts it as a candidate, and a manual pick of one fails in a second naming the server rather than after a minute with the tool's error. Cloud agents are assumed up. Only the server address is read from that config, and the answer is cached for ten seconds.

A turn is one opaque subprocess, so there is no boundary inside it at which to reassess after the agent has looked at the repository. The harness does that look itself, before choosing, and reassesses at the one boundary it owns: the end of an attempt.

## Execution and security boundaries

- **An agent runs project code as the signed-in operating-system user. This is not an OS or container sandbox.** The postures above are the agent tool's own permission settings. Use Read only for a project you do not trust.
- Switching agent replays the conversation into the one taking over, and nothing else. Tool calls and file reads belong to the tool that made them; the project folder already holds their result, and the new agent is told to read it rather than trust a summary. A conversation too long to send drops its oldest turns and says so, rather than refusing to continue.
- A turn's file changes are recorded by reading the folder before and after it, because a project folder need not be a git repository. In a repository the working tree is also checkpointed as hidden refs before and after the turn. Anything else that writes to the folder during a turn is attributed to it.
- The backend owns workspace checks. Foreign resource IDs raise `TenantIsolationViolationException`; switching workspaces clears cached resources and open event streams.
- Project file APIs reject traversal, linked paths, sensitive names, private app storage, and overlapping project roots across workspaces. These bound what Frontier itself reads and shows; the agent reaches the folder through its own tools.
- One turn at a time per conversation. Multiple backend workers on the same database are rejected. A turn interrupted by a restart is closed out, never replayed: its subprocess died with the application, and whatever it had already written to the folder is still there.
- The Terminal panel in the desktop app is your own shell, running as you with no restriction beyond your account's. In a browser it falls back to the bounded project check runner: Python pytest, unittest, compileall, and npm test/build/test/lint/typecheck, with a 120-second timeout and no shell operators. Both are separate from the commands an agent runs through its own tool.
- File views are bounded: up to 2,000 tree entries, UTF-8 text under 300 KB, and text-based PDFs up to 5 MB and 100 pages, read as extracted text. A turn takes at most 30 minutes before it is stopped.

Native application state lives under `%LOCALAPPDATA%\dev.frontier.harness`. Managed project folders are stored in the adjacent `Frontier Projects` directory, segregated by workspace. Back up the database and `secret.key` together. Deleting workspace records does not recursively delete user project folders.

## Verification

```powershell
.venv\Scripts\python -m pytest -q
npm run build
npm run test:e2e
```

The Python suite exercises workspace boundaries, a turn's file record, agent switching and its handover, cancellation, restart recovery, subscription account switching, each posture's translation into every tool, and the project file and command boundaries. The Playwright suite uses an isolated server and an explicitly injected test agent. Production has no simulated provider path or hard-coded model response.

`scripts/switch_demo.py` shows a spent subscription handing a turn over without needing two real ones. The switch itself was exercised against the real tools: Claude edited a file in a project folder, then OpenCode picked up the same conversation and correctly described what Claude had done. See [verification notes](docs/verification.md).

## Code map

| Area | Implementation |
|---|---|
| Native startup and backend lifecycle | `src-tauri/src/main.rs`, `backend/desktop.py` |
| Native launch authentication and project/session API | `backend/desktop_routes.py` |
| Contracts | `backend/schemas.py` |
| One message, one agentic turn, the switch, and revert | `backend/agent.py` |
| Git status, staging, commit, push, checkpoints, worktrees | `backend/gitops.py` |
| Native terminals (ConPTY) and update, notification, dialog plugins | `src-tauri/src/main.rs` |
| Launching an agent tool, postures, subscription accounts | `backend/broker.py` |
| Workspace-scoped persistence and encryption | `backend/store.py` |
| Project file reading and the user's own checks | `backend/projects.py` |
| Authenticated HTTP API and replayable SSE | `backend/app.py` |
| Project shell, conversation, contextual tools | `src/desktop/` |
| Workspace-aware query cache and live turns | `src/app/context.tsx`, `src/app/useLiveSession.ts` |
