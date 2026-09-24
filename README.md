<p align="center"><img src="src-tauri/icons/128x128.png" width="96" alt="Frontier"></p>
<h1 align="center">Frontier</h1>
<p align="center">One conversation about one project folder. Any agent takes the next turn.</p>
<p align="center"><a href="https://github.com/SecondStageTurbine/FrontierHarness/releases/latest">Download for Windows</a> · <a href="#everyday-use">Everyday use</a> · <a href="#run-from-source">Run from source</a> · <a href="docs/verification.md">Verification notes</a></p>

Frontier is a desktop harness for coding agents. You hold one conversation about one project
folder and choose which agent answers each turn: Claude Code, Codex, Gemini CLI, or OpenCode,
including a local model through OpenCode. Switching agent mid-conversation costs nothing. The
conversation lives in Frontier, not inside any tool's own session, and the agent taking over is
given it along with the folder the previous one was working in. Or leave the choice to
**Adaptive**, the default: it routes each message to the least expensive connected agent whose
capability profile covers it, and escalates to a stronger one, with a handoff, if that agent fails.

Every agent is agentic. A turn runs the agent command line tool you already have installed, in
your project folder, with its own tools: it reads, writes, and runs commands there. What it may
do is chosen per turn, read only, edit files, or full auto, and Frontier records what the folder
looked like before and after so any turn can be reverted.

## What it does

| | |
|---|---|
| **Four agents, your subscriptions** | Claude Code, Codex, Gemini CLI and OpenCode run as the tools you already installed and signed in to. No API key is needed to take a turn. Several logins per provider can be connected; a spent one hands over to the next. |
| **Adaptive routing** | Pick Adaptive and each message goes to the least expensive agent whose capability profile covers it, escalating to a stronger one with a handoff if that agent fails. |
| **Three postures, and approval cards** | Read only, Edit files, Full auto, translated into each tool's own flags. Under Edit files, Claude asks before running a command and a card in the conversation answers it. |
| **Git-native sessions** | Stage, diff, commit and push from the Changes panel; every editing turn is checkpointed so **Revert this turn** restores it exactly; a session can start on its own branch in its own worktree; one message can fan out to several agents at once. |
| **Work alongside the agent** | A real terminal in the project folder, a file editor, project and conversation search, clickable `file:line` links, and a preview panel for the project's dev server. |
| **Queue, steer, compact** | Enter queues the next message while a turn runs, Ctrl+Enter stops it and sends instead, and a context meter offers to compact the conversation into a summary before the oldest turns fall off. |
| **Automations and remote access** | Prompts that run daily, every N minutes or by webhook, each as an ordinary session. A browser on another device on your network or tailnet can use the same Frontier. |
| **MCP servers and skills** | Servers the workspace hands to every agent on every turn, with a catalog of common ones, and the skills and slash commands the agents already find, offered with `/` in the composer. |
| **Team mode** | Pick a lead; it plans the work as tasks, Frontier hands each task to the agent whose profile fits (or the one the lead names), workers run in parallel worktrees, their changes are folded back, and the lead reviews and replies. |
| **Pull requests** | Open a PR from the session's branch through the GitHub CLI, watch checks and review state, and hand review comments to the agent. |
| **Agents that test in a browser** | Turn it on per project and every editing turn gets a real browser: the agent opens the page it changed, clicks through, reads the console and takes screenshots, which appear in the Preview panel. |
| **Frontier as a tool** | Frontier is itself an MCP server: another agent, an editor or a script can list projects, send a message to any agent here and wait for the reply, and catch up on a project. |
| **Quiet desktop manners** | Tray icon and close-to-tray, one instance at a time, desktop notifications when a turn finishes in the background, signed auto-updates, usage per agent and project, four themes and an accent colour. |

## Install the desktop application

Download `Frontier_<version>_x64-setup.exe` from the [latest release](https://github.com/SecondStageTurbine/FrontierHarness/releases/latest). The per-user Windows installer includes Frontier, its Python runtime, backend dependencies, and frontend assets, and launches Frontier when it finishes. It does not need this repository, Python, Rust, Node.js, or a terminal to run. Existing projects, sessions, and encrypted credentials stay in the same application-data directory across versions.

Frontier checks GitHub releases once at launch. When a newer version is published, a banner offers **Install and restart**; the installer runs for the current user and the app reopens on the new version. **Settings → General → Check for updates** does the same on demand. Feeds are signed: the app only installs a package whose signature matches the public key built into it.

The installer installs WebView2 if it is missing; that step needs internet access. The installer is not Authenticode-signed, so Windows may ask you to confirm it. Project-specific dependencies are separate: npm checks need Node.js, and projects with additional Python packages can supply a `.venv`.

## Connecting agents

On first run, **Set up your agents** looks for Claude Code, Codex, OpenCode and Gemini CLI on this computer, shows where each is and its version, offers the model identifiers each accepts (Codex's are read from Codex itself), and connects the ones you tick in one go; for a tool that is missing it shows the install and sign-in commands. **Settings → Agents & Providers → Run the setup wizard again** reopens it.


On a fresh installation, open **Settings → Agents & Providers → Connect model**. Choose **Claude subscription**, **Codex subscription**, **Gemini CLI** or **OpenCode subscription** to run a model without any API key: Frontier runs the agent command line tool you already have installed, and that tool signs in with your own subscription. Install the tool, sign in once in a terminal, then connect a model whose identifier is the name the tool accepts, such as `sonnet` for Claude or `opencode-go/glm-5.3` for OpenCode. There is no key, endpoint or per-token rate to enter. **Test connection** confirms the tool runs; a sign-in problem surfaces on the first request.

This is the one place where credentials come from outside the application, so it is worth knowing what holds and what does not. The tool runs in your project folder with its own tools, which is what makes it an agent rather than a text generator; how much it may do is the per-turn posture below and is never escalated on its own. Provider API keys are stripped from its environment, because an inherited `ANTHROPIC_API_KEY` would make the Claude tool bill per token instead of using the subscription. Codex reports only a combined token total, so its token counts show as unavailable.

An API key is not an agent. A model reached with a key has no tool loop, no file access and no shell, so keyed providers cannot take a turn; they stay configured and are used for dictation and as an Adaptive classifier. A local model becomes an agent through OpenCode, which supplies the tool loop and can point at Ollama, LM Studio, or any OpenAI-compatible local endpoint. Test connection for a keyed model checks model discovery; it does not generate text. Credentials are encrypted locally and never returned to the UI, and the broker never falls back to environment credentials on its own.

### More than one subscription

A subscription login can carry a **subscription account** name, such as `work` or `personal`. Each name gets its own credential directory inside the workspace, so a second Claude, Codex or OpenCode subscription is connected beside the first instead of replacing it. **Sign in** on the model card prints the two lines that sign that account in: the command line tool writes its credential into that directory and reads it back from there, and Frontier never sees it. Leave the account name empty to keep using the sign-in already on this machine.

Connect a second subscription by adding a second model with the same provider and the same model identifier under a different account name. When one of them answers that its usage window is spent, the run moves to the next connected account and continues; the message records the login that actually answered, and the spent one is skipped for an hour before it is tried again. When every login for an agent is spent, the turn hands over to another connected agent with the same handoff an Adaptive escalation writes, up to twice, and the message records who was out of usage and who continued. A login that is simply not signed in is reported rather than switched away from.

### Dictation

A **Dictate** button in the composer records from the microphone and types the transcription into the field. Transcription goes through an OpenAI or OpenAI-compatible model this workspace has already connected, such as `gpt-4o-mini-transcribe`, using the same encrypted credential as everything else. The button does not appear until such a model exists. The recording is posted, transcribed and dropped.

## Everyday use

1. Create a project, open an existing folder using the native folder picker, or paste a repository URL to clone it into a new managed folder.
2. The agent selector opens on **Adaptive**, which picks an agent per message. Choose a specific agent next to the composer to override it for as long as the project stays open, and choose what the agent may do this turn.
3. Type a message and press Enter. Shift+Enter adds a line.
4. The agent works in the folder. When it finishes, its reply appears with the files it changed, how long it took, its tokens in and out, and its cost when the model has rates. Open Files, Changes, Terminal or Preview as needed. **Revert this turn** under the changed files puts every file the turn touched back to how it was before it.
5. Pick a different agent whenever you like. The next turn goes to it, and it is given this conversation and the same folder.

Ctrl+N starts a session, Ctrl+K finds a session, and Escape closes the contextual panel. Project and session selection, drafts, and appearance survive a restart.

### What an agent may do

Chosen per turn, and translated into each tool's own setting:

| Posture | Claude Code | Codex | OpenCode | Gemini CLI |
|---|---|---|---|---|
| Read only | `--permission-mode dontAsk --disallowedTools Bash Edit Write MultiEdit NotebookEdit` | `--sandbox read-only` | `--agent plan` | `--approval-mode plan` |
| Edit files | `--permission-mode acceptEdits --permission-prompt-tool mcp__frontier__approve` | `--sandbox workspace-write` | `--agent build` | `--approval-mode auto_edit` |
| Full auto | `--permission-mode bypassPermissions` | `--dangerously-bypass-approvals-and-sandbox` | `--agent build --auto` | `--approval-mode yolo` |

A commit or push by the agent needs **Full auto**. Under Edit files, Codex's sandbox keeps `.git` read-only and blocks the network; the agent is told this on every turn so it asks for Full auto rather than asking you to run git by hand. Your own commits from the Changes panel need no posture at all.

**Approval cards.** Under Edit files, Claude may only edit; a shell command it wants to run used to be refused outright. Now it asks: the turn pauses, a card appears in the conversation naming the tool and the command, and **Allow** or **Deny** answers it. The mechanism is Claude Code's permission prompt tool: every Edit-files turn is given a small MCP server that Frontier ships inside its own backend, and Claude calls it instead of refusing. A request nobody answers in fifteen minutes is denied and the turn continues; stopping the turn denies whatever it was asking. Codex, OpenCode and Gemini run without a way to ask, so their postures stay as they were. Read only and Full auto never ask.

### Adaptive

Pick **Adaptive** in the agent selector and the harness chooses the agent for each message: the least expensive one that can reliably do it, escalating to a stronger one if that agent fails. A manual pick is never routed; Adaptive is consulted only when it is what the selector says.

It routes by comparing what a message needs against what each agent is good at, never by naming a provider. Each connected agent carries a capability profile, coding, reasoning, planning, debugging, architecture, review, tool use, repository understanding, instruction following and speed, each 0 to 10, with defaults by provider and model family that any row can override under **Adaptive routing profile** in Agents & Providers. That profile is the registry: a new agent, local or cloud, is a model row plus at most those numbers, and the router does not change. A message is classified by deterministic heuristics on its wording and on a cheap walk of the project folder (size, languages, and whether it touches auth, data or payment paths), optionally refined by a classifier named as the workspace's **Adaptive classifier** in Workspaces, and always falling back to the heuristics if that classifier fails. Length is not a signal: "Fix authentication." is short and hard.

The classifier is either a **TypeSafe** connection (provider "TypeSafe (classifier)", connected with just an API key, then selected in Workspaces) or any other keyed model, a local Ollama model being the intended case for the latter. TypeSafe answers a fixed set of typed, calibrated questions, what kind of request this is, whether it touches many files, whether it names a concrete target or a vague symptom, and whether it touches auth, data, money or an external service, instead of being asked to write JSON and hope it parses. TypeSafe never takes a turn and is sent the message text and the same non-sensitive project summary the heuristics already compute, never file contents.

Candidates that lack an agent loop are out. Of the rest, the cheapest whose profile covers every requirement within a point wins; if none does, the closest. A turn that fails, or whose agent says it cannot and changed nothing, is escalated one rung, the weakest sufficient agent stronger than those already tried, with a handoff that names the objective, what each earlier agent did, and which files are already changed, so it continues rather than starts over. At most two escalations, then the turn is reported failed. The message records the decision: what was needed, who was chosen and why, and each attempt.

**Local models that take turns.** Several local models can be connected while only one holds the GPU at a time. OpenCode's own config names the server each local provider talks to, so Frontier asks each loopback server whether it is up and serving that model: the agent picker shows an offline local agent greyed with "offline", Adaptive never counts it as a candidate, and a manual pick of one fails in a second naming the server rather than after a minute with the tool's error. Cloud agents are assumed up.

### The composer

While a turn is running, Enter queues the next message for the moment it finishes, and Ctrl+Enter stops the turn and sends the new message instead. A turn is one opaque subprocess, so that is what steering means here: what the agent had already written to the folder stays, and the new message is sent with the conversation so far. Queued messages are shown under the conversation and can be removed; stopping a turn drops its queue.

A session is named by its first reply: the agent is asked to put a one-line title at the top of its first answer, Frontier takes it off the reply and onto the session, and a name you set yourself is never replaced. ArrowUp in an empty composer walks back through your earlier messages in the session and ArrowDown returns. Ctrl+Shift+S stashes the draft aside; a **Stash** chip below the composer lists stashed drafts per project and puts one back. Type `/` at the start of the composer to pick one of the slash commands the agents find in the project.

The meter below the composer shows how much of the transcript limit the conversation the agent is sent occupies. Past 70 percent it turns amber and offers **Compact**; once the oldest messages are already being dropped, it says how many. Compact asks the current agent, under Read only, for a handoff summary of everything so far and sends that in place of the earlier messages from then on. The messages stay in the conversation for you, with a note at the point of compaction that shows the summary.

### Working alongside the agent

The **Terminal** panel is a real shell: PowerShell on Windows, your login shell elsewhere, one ConPTY per tab, opened in the folder the conversation works in, worktree included. It runs with your full environment as your own terminal would, unlike agent tools, whose environment is stripped of provider keys. Shells outlive the panel: close and reopen it and the same sessions are still there. Closing Frontier ends them. A project with a virtual environment, uv, Poetry, conda or Pipenv has it activated in new terminals and named to every agent at the top of the turn. The robot button runs an agent's own command line tool interactively in the terminal, for the times the tool's own interface is the right one.

The **Files** panel edits: open a text file, type, and press **Save** or Ctrl+S. The write goes through the same path boundary as every read, so credentials, linked paths, and anything outside the project stay untouchable. The search box above the tree finds lines in every readable text file; each hit opens the file at that line. The sidebar search filters sessions by name and also searches their messages across every project in the workspace, listing matches under **In messages**. A path the agent writes in inline code, such as `src/app.py:42`, is a link that opens the file at that line. Select text in a reply and **Cite in composer** adds it as a chip beside your next message.

The **Preview** panel shows a dev server the project is running. Frontier probes the usual local ports and lists the ones answering; pick one or type any URL. Right-click a project for **Open in VS Code**, **Open in Cursor**, or **Show in Explorer**; a session with a worktree offers the same for that worktree.

### Git

When the project folder is a git repository, the **Changes** panel opens with the repository: the current branch, what is staged, and the working tree. Tick a change to stage it, open it to read its diff, write a commit message or press **Write message** to have the current agent draft one from the staged diff under Read only, then **Commit** and **Push**. Below the repository, the panel keeps the per-turn record of what each agent changed.

Before and after every turn that may edit files, Frontier checkpoints the whole working tree as hidden git objects under `refs/frontier/checkpoints`, untracked files included and ignored files excluded. Nothing is committed to your branch and the index is untouched. **Revert this turn** restores every path the turn changed from its checkpoint, exactly and binaries included, and deletes files the turn created; later edits to those same files are undone with it. A folder that is not a repository still records each turn's readable before-and-after, and reverts from that.

A new session can start **On a new branch**: Frontier adds a git worktree in `Frontier Worktrees` beside its managed projects, on a branch named from your first message. The agent, the Files panel, the Changes panel, the terminal and your own project checks all work in that worktree, so parallel sessions never write over each other. The branch chip in the session header says where you are. Right-click the session and choose **Remove worktree** to delete the folder; the branch and its commits stay in the repository.

**Fan-out.** With more than one agent connected and a repository open, the split icon beside the composer sends the same message to several agents at once. Each gets its own session on its own branch in its own worktree, named from the message and the agent, so the results sit side by side in the sidebar and their diffs in the Changes panel.

### Team mode

With more than one agent connected, the people icon beside the composer turns on team mode for the next message. The agent in the selector becomes the lead: it takes a Read-only turn and answers with a plan of one to six tasks, each with instructions that stand alone, what it demands most (coding, debugging, review and so on), an optional agent it insists on, and whether it can run beside the others. Frontier assigns each task, the lead's named agent or the cheapest connected one whose capability profile covers the need, and opens a session per task. In a repository each worker gets its own worktree and independent tasks run at the same time; the worker's checkpointed changes are then applied to the lead's folder as a three-way patch, unstaged, so unrelated local edits stay put. The lead reviews every report and the resulting diff under Read only, may send one round of fixes back to the workers, and writes the final reply. The card in the conversation shows the plan, who has each task, its status, and a link to every worker session; worker sessions are listed under **Team sessions** in the sidebar. Adaptive cannot lead, and a team needs Edit files or Full auto.

### Pull requests

With the GitHub CLI installed and signed in, the Changes panel shows the branch's pull request: number, title, checks, review state, and the size of the change. Without one, **Create pull request** pushes the branch and opens it; **Write description** has the current agent draft the title and body from the branch's commits and diff. **Address review comments** collects the review comments and puts a prompt in the composer that asks the agent to act on them.

### Editing a message from earlier

Hover one of your own messages and choose **Edit from here**. That message and everything after it leave the conversation, the folder is put back to how it was before that turn (from the checkpoint in a repository, from the recorded before-text otherwise), and the text lands in the composer to change and resend.

### Referencing what you are looking at

The panels can put typed references beside your next message instead of pasted text. The Files panel's **Reference** button adds the open file, or the selected lines from the editor; the terminal's adds the selected output; the diff view's adds the diff. They appear as chips under the composer, and the message shows them as chips afterwards. A paste longer than 4,000 characters is attached as a text file instead of filling the composer. Up to 100 attachments go with a message; images up to 10 MB, other files up to 50 MB, kept beside the database and copied into the project for the agent.

### Project settings

Right-click a project for **Project settings**: the default agent and posture for new sessions; a **setup command** to run in every new worktree and the ignored files, such as `.env`, to **copy into worktrees** so a fresh branch runs at once; the **dev server command** the Preview panel starts, stops and restarts, with its output and a way to free a stuck port; whether `.env` files are kept away from Claude under every posture; the **project memory** every agent is given; and a one-time **import** of the conversations Claude Code and Codex kept about this folder.

### Sessions, notifications, the tray

Right-click a session to rename, pin, archive, or snooze it for an hour or until tomorrow morning; **Remember this session** asks the agent for a few facts worth keeping and adds them to the project memory. Pinned sessions stay at the top; archived and snoozed ones move to their own lists at the bottom of the sidebar. **Settings → General → Workspace** can archive sessions idle for a number of days on its own, optionally remembering them first, remove the worktrees of archived sessions after a number of days while keeping their branches, and holds the **rules** every agent in the workspace is given at the top of every turn. A session whose pull request merges archives itself the next time the Changes panel looks. **Settings → General** also sets the interface size, spellcheck in the composer, and whether the computer is kept awake while an agent or automation works. A turn that finishes while Frontier is in the background raises a desktop notification with a short chime; **Settings → General** turns the chime or the notification off.

Closing the window keeps Frontier running in the system tray: automations keep firing, finished turns still notify you, and the tray icon's menu offers **Open Frontier** and **Quit Frontier**. A left click on the icon reopens the window. **Settings → General → Window** turns this off, after which closing the window quits. Launching Frontier while it is already running brings the existing window to the front instead of starting a second copy.

### Resume from the command line

**Resume from CLI** in the sidebar, or `/resume` in the composer, lists the recent conversations you had with Claude Code or Codex in this project's folder, newest first, with the first message and date. Pick one and it becomes the current session. Frontier's own turns, which also run those tools, are left out of the list.

A resumed conversation keeps the tool's own memory. When the same tool takes the next turn in the project folder, Frontier continues that tool's session natively (`claude --resume`, `codex exec resume`) and sends only what was said since, so everything the tool read, ran and thought is still there. Any other agent picks the conversation up from the transcript, as usual, and from then on every agent does, because the tool's own session no longer holds the whole conversation. A session the tool can no longer find falls back to the transcript on its own.

### Since you were last here

Open a project after six hours or more away and, if anything happened, a strip says what: turns, commits, files changed, automation runs. **Summarize** has an agent, under Read only, turn those facts into a short catch-up: what was done and by whom, what changed, what was left unfinished, and the next sensible step.

### A browser for the agent

In the **Preview** panel, **Let agents use a browser** gives every editing turn in that project Playwright's browser tools, driving the Microsoft Edge that ships with Windows. The agent is told to open what it changed, check the console, take a screenshot and report what it saw. Screenshots land in `.frontier/browser` in the project and show in the Preview panel; click one to enlarge it. **Show the agent's browser window** runs it visibly instead of hidden. The browser's tools never wait on an approval card. It needs Node.js; the first turn downloads Playwright's server.

### Frontier as a tool for other agents

Frontier is also an MCP server. **Settings → MCP & Skills → Use Frontier from other agents** gives the exact command to add it to Claude Code or Codex, and the JSON for any other client. Its tools list projects and sessions, read a conversation, send a message to any connected agent here (Adaptive by default) and wait for the reply, show a project's git changes, and catch up on what happened since a number of hours ago. It works while Frontier is running and only for programs run by you on this computer: it authenticates with a token in Frontier's data folder, and only over loopback.

### Automations

**Settings → Automations** runs a prompt on its own: daily at a time, every N minutes, or when a webhook is called. Each run opens a session in the chosen project and sends the prompt as one turn under the posture you set, so the result reads like any other conversation. Schedules fire while Frontier is open or in the tray; a run missed while it was closed happens once at the next start. The webhook is a POST to the URL shown on the card, whose secret is the whole credential.

### Remote access

**Settings → Remote access** lets a browser on another device on your network or tailnet use this Frontier. Turn it on, set a password for your user, restart Frontier, and open one of the listed addresses. The connection is plain HTTP, so use it on a network you trust or over Tailscale. In a browser the terminal, updater and desktop notifications are unavailable; everything else works.

### Agent tools

**Settings → Agents & Providers** shows each installed agent tool's version against the latest on npm, with a one-click update for tools installed through npm.

### MCP servers and skills

**Settings → MCP & Skills** lists the Model Context Protocol servers this workspace hands to its agents on every turn: a command spoken to over stdio, or an HTTP endpoint. **Catalog** fills the form for common ones, GitHub, a filesystem, Postgres, a Playwright browser, Slack, fetch, memory, sequential thinking and Context7, leaving the token, path or connection string for you. Claude receives them through `--mcp-config`, Codex through `-c mcp_servers.*` overrides, and OpenCode through `OPENCODE_CONFIG_CONTENT`; Gemini CLI reads its own settings file and is not configured from here. The same page lists the skills and slash commands the agents already discover in the project and your home folder (`.claude`, `.codex`, `.gemini`).

### Usage and themes

**Settings → Usage** adds up every finished turn in the workspace: tokens in and out, agent time, and cost per agent, per project, and per day. Subscription turns carry no rate and show no cost. **Settings → General** offers Dark, Midnight, Warm and Light themes and an accent colour that recolours buttons, links and highlights in any of them.

## Execution and security boundaries

- **An agent runs project code as the signed-in operating-system user. This is not an OS or container sandbox.** The postures above are the agent tool's own permission settings. Use Read only for a project you do not trust.
- Switching agent replays the conversation into the one taking over, and nothing else. Tool calls and file reads belong to the tool that made them; the project folder already holds their result, and the new agent is told to read it rather than trust a summary. A conversation too long to send drops its oldest turns and says so, rather than refusing to continue; Compact keeps their gist.
- A turn's file changes are recorded by reading the folder before and after it, because a project folder need not be a git repository. In a repository the working tree is also checkpointed as hidden refs before and after the turn. Anything else that writes to the folder during a turn is attributed to it.
- The backend owns workspace checks. Foreign resource IDs raise `TenantIsolationViolationException`; switching workspaces clears cached resources and open event streams.
- Project file APIs reject traversal, linked paths, sensitive names, private app storage, and overlapping project roots across workspaces. These bound what Frontier itself reads, shows and saves; the agent reaches the folder through its own tools.
- One turn at a time per conversation. A second backend on the same data folder says so and exits. A turn interrupted by a restart is closed out, never replayed: its subprocess died with the application, and whatever it had already written to the folder is still there.
- The Terminal panel in the desktop app is your own shell, running as you with no restriction beyond your account's. In a browser it falls back to a bounded project check runner: Python pytest, unittest, compileall, and npm test/build/test/lint/typecheck, with a 120-second timeout and no shell operators. Both are separate from the commands an agent runs through its own tool.
- The approval tool speaks to the backend over loopback with a token minted for that one turn; a webhook's secret is its only credential; remote access is plain HTTP behind the user's password.
- File views are bounded: up to 2,000 tree entries, UTF-8 text under 300 KB, and text-based PDFs up to 5 MB and 100 pages, read as extracted text. A turn may run for 90 minutes before it is stopped; Project settings can raise that for a project, up to 8 hours.

Native application state lives under `%LOCALAPPDATA%\dev.frontier.harness`. Managed project folders and worktrees are stored in the adjacent `Frontier Projects` and `Frontier Worktrees` directories, segregated by workspace. Back up the database and `secret.key` together. Deleting workspace records does not delete project folders.

## Run from source

Prerequisites: Node.js 22+, Python 3.11+, Rust, Windows WebView2, and the Visual Studio C++ build tools with a Windows SDK. This checkout was exercised on Windows with Python 3.14.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
npm ci
npm run desktop:dev
```

The last command builds the interface and opens a normal **Frontier** window. Tauri starts and owns the internal backend; no separate server command or browser tab is needed. The development launcher rebuilds Rust changes; run `npm run build` and refresh the native window for frontend changes.

An explicit convenience import is available for an existing Anthropic environment credential: launch with `HARNESS_IMPORT_ENV=1`. On first native launch, if the initial workspace has no models, this copies `ANTHROPIC_API_KEY` into its encrypted model configuration. `HARNESS_DEFAULT_MODEL` can specify the imported model ID.

### Build and release

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
npm run desktop:build
.venv\Scripts\python scripts/test_package.py
```

The build bundles the backend with PyInstaller, then creates a Tauri NSIS installer. Release builds refuse to fall back to a development checkout if their bundled backend is missing. The bundled Python runner supports unittest, pytest, and compileall without a system Python installation, and doubles as the approval MCP server.

Updates are signed with a minisign key that is not in this repository. Set `TAURI_SIGNING_PRIVATE_KEY` to the private key's text (PowerShell: `$env:TAURI_SIGNING_PRIVATE_KEY = Get-Content ~/.tauri/frontier.key -Raw`) before `npm run desktop:build` so the installer gets its `.sig`; an already built installer can be signed afterwards with `npx tauri signer sign -f ~/.tauri/frontier.key <installer>`. Then run `.venv\Scripts\python scripts/write_update_manifest.py` to write `latest.json` beside it. A GitHub release for tag `v<version>` needs the installer, its `.sig`, and `latest.json`; installed copies read `releases/latest/download/latest.json`. The public key lives in `src-tauri/tauri.conf.json`; losing the private key means shipping a new key with a manually installed version.

## Verification

```powershell
.venv\Scripts\python -m pytest -q
npm run build
npm run test:e2e
```

The Python suite exercises workspace boundaries, a turn's file record, agent switching and its handover, cancellation, restart recovery, subscription account switching, each posture's translation into every tool, the project file and command boundaries, git checkpoints and worktrees, the approval tool's protocol, automations, usage, remote access and the tray flag. The Playwright suite uses an isolated server and an explicitly injected test agent. Production has no simulated provider path or hard-coded model response. Each release is also driven at runtime in the installed window over the DevTools protocol, and the approval chain was proven with a real Claude turn. See the [verification notes](docs/verification.md) for what each version added and how it was checked.

## Code map

| Area | Implementation |
|---|---|
| Native startup, backend lifecycle, terminals, tray, single instance | `src-tauri/src/main.rs`, `backend/desktop.py` |
| Native launch authentication and the project, session, git, search and automation API | `backend/desktop_routes.py` |
| Contracts | `backend/schemas.py` |
| One message, one agentic turn, the switch, approvals, compaction, fan-out and revert | `backend/agent.py` |
| Launching an agent tool, postures, MCP injection, subscription accounts | `backend/broker.py` |
| Adaptive routing and capability profiles | `backend/adaptive.py` |
| Git status, staging, commit, push, checkpoints, worktrees | `backend/gitops.py` |
| The approval MCP server Claude calls during a turn | `backend/permission_tool.py` |
| Frontier's own MCP server for other agents | `backend/frontier_mcp.py` |
| Team mode: plan, delegate, apply, review | `backend/team.py` |
| Pull requests through the GitHub CLI | `backend/pullrequests.py` |
| The project's dev server | `backend/devserver.py` |
| Tool versions and updates, history import and the resume picker | `backend/maintenance.py` |
| Scheduled and webhook turns, idle archiving | `backend/automations.py` |
| Remote access and tray flags | `backend/remote.py` |
| Workspace-scoped persistence and encryption | `backend/store.py` |
| Project file reading, writing, search and the user's own checks | `backend/projects.py` |
| Authenticated HTTP API and replayable SSE | `backend/app.py` |
| Project shell, conversation, contextual panels | `src/desktop/` |
| Settings pages | `src/features/settings/` |
| Workspace-aware query cache and live turns | `src/app/context.tsx`, `src/app/useLiveSession.ts` |
