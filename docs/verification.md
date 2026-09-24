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

Subscription providers were added after that cycle and were tested against the real command line tools, not only against stubs. Frontier's broker drove the installed Claude and Codex tools end to end and each returned a structurally valid build artifact with real files and commands, while a provider API key was deliberately present in the parent environment to confirm it is stripped before the tool starts. Claude reported token counts; Codex reported none, which is expected, and the run still completed with a monthly budget configured because a subscription login is rated at zero. Codex rejects a schema whose required list omits a property, so its schema is closed before use; open dependency maps are left untouched, matching the existing rule for provider-constrained decoding.

The installer was then rebuilt, reinstalled and verified again, and both subscription providers were exercised inside the installed frozen backend rather than only from source. Each connected, and each ran a complete four-stage workflow in propose mode: every stage returned a contract-valid artifact, files were proposed, and the project folder stayed empty. Those runs confirmed the accounting end to end. Claude reported token counts and Codex reported none, yet both recorded a known cost of zero, so a configured budget stays usable either way and only the token counts show as partial. Whether the reviewer approves varies between runs, as it does for any provider, so the check asserts that every stage completed with a valid artifact rather than asserting approval.

One resolution gap surfaced during that work and was fixed. The Codex tool installs as a script shim that Windows cannot start directly, so it is run through its own package entry point under Node. When Node was absent the failure claimed the tool was not installed, which is misleading when it is present. The two causes now report separately.

The two subscription providers are not equally reliable, and the difference is structural. Codex accepts a schema file, so its artifact is constrained by the provider. The Claude tool has no equivalent, so its artifact is requested in the prompt and validated only after the fact. One run during this work failed for that reason, reporting an invalid structured artifact, while repeats of the same instruction succeeded. The engine handles it correctly by failing the run with a clear message and preserving the raw output for inspection, but a workflow using the Claude tool should expect the occasional rejected artifact. Extraction that tolerates commentary around the JSON object would reduce it.

A silent partial upgrade was observed and has since been fixed. Installing 0.1.1 over 0.1.0 returned exit code 0, updated the backend sidecar, and registered the new version, while leaving the previous desktop executable in place. The installer was confirmed to contain the correct new binary, so the write was skipped rather than the build being wrong, most likely because the file was briefly locked. Running the installer a second time replaced it correctly. The installed-file verification in [installer-manifest.json](installer-manifest.json) is what caught it, by comparing installed bytes against the build rather than trusting the exit code, so that check belongs in every release.

The cause is that a silent install ignores a failed overwrite: NSIS records the error and continues, and the generated script sets no overwrite mode, so the default applies. Two guards now close it. Before anything is written, each executable about to be replaced is opened for writing, and the install refuses outright if one is held, so nothing is half-replaced. After the files are written, the installed main binary's version resource is compared against the version being installed, which covers any skipped write in the moment between that check and the copy. The sidecar carries no version resource, so it is covered by the first guard only.

Both were tested rather than reasoned about. Holding a write-denying lock on the installed executable made the installer exit with code 2 and leave that file byte-identical, where the same situation previously exited 0 with a stale binary. An unlocked install still exits 0, and a genuine cross-version upgrade replaced both executables and reported the new version. The version comparison is also proven by the passing case, since arithmetic that produced the wrong string would have aborted a good install. Its refusal path is inferred rather than triggered, because the race it exists for could not be reproduced on demand.

Everything was rebuilt after the defect fixes above and the verification cycle was repeated end to end. The frontend assets, backend sidecar, desktop binary and installer were rebuilt; the packaged-executable checks were re-run against the new sidecar; the installer ran silently for the current user and returned exit code 0; and installed-file verification confirmed the installed backend and desktop match the current build and the tested package. Both [installer-manifest.json](installer-manifest.json) and [packaged-verification.json](packaged-verification.json) now describe this build.

The launch-port fallback was exercised against the installed build rather than only compiled. With the remembered port held by an unrelated listener, the app still started, recorded the new port it chose, answered its health check, wrote no launch error, and stopped its backend on exit.

Three limits apply to that cycle. The packaged checks were re-run without the optional live provider test, so the real-provider evidence in [live-verification.json](live-verification.json), and the live section previously embedded in the packaged report, describe the earlier build; the earlier report remains in version history. No Frontier process was running when the installer ran, so the hooks that close a live backend were not re-exercised, only their earlier recorded run. An earlier install also left a directory named for an unexpanded environment variable beside the installed binaries, containing only Windows shell cache files; this install wrote nothing to it and its cause is not yet identified.

Visual inspection resumed against the installed release after the interrupted inspection. The native window restored its project and conversation. Checked actual source previews, historical unified and split diffs, full structured plans, reviewer approval, recorded command output, contextual panel resizing and closing, the AI team dialog, and the new-session screen. These views rendered without obvious layout defects at the inspected window size. The app was left on a new session with the prompt composer focused. This was a visual inspection of existing results, not another live model execution.

A final same-version upgrade was tested while the installed desktop and frozen backend were running. The installer closed both processes, completed successfully in one pass, and installed files matching the current build and the live-tested backend checksum. Installer hooks handle backend shutdown before replacement to prevent retaining a locked older executable. The updated installed desktop was then relaunched.

Code signing, OS process sandboxes, automatic dependency installation, arbitrary interactive shells, file deletion, and autonomous web browsing are not implemented. Only the configured Anthropic provider was live-tested; other provider routes are implemented through the official OpenAI SDK and covered by isolated routing tests. Local project checks run with the current OS user's permissions. API/data tenant isolation is tested; adversarial process isolation requires an additional sandbox.

Dependency diagnostics remaining: two upstream Python testing deprecation warnings and non-failing Zod/Rollup comment-annotation warnings. There are no TypeScript/build/test failures.

Version 0.1.3 fixes a silent context gap found by a user, not by a test. A project folder holding a PDF produced a run that never reviewed it: the snapshot dropped every file its reader could not decode as UTF-8 text and recorded nothing about the drop, so the model received an empty project and produced a confident explanation for the document's absence instead of reporting that it could not be read. Uploaded attachments already extracted PDF text; only the project-folder path did not. That extraction is now shared by both, the reader accepts text-based PDFs up to 5 MB and 100 pages, and every file the snapshot cannot read or cannot fit is now listed to the model by name and reason. Builds refuse to write a `.pdf`, because a PDF now carries a content baseline and a build that returned one would otherwise replace the document with its own extracted text.

That cycle was verified end to end. The full suite passes at 53 tests, including one that fails without each part of the fix: the extracted text reaching context, the unreadable binary being declared, and the rejected PDF write. The frontend assets, backend sidecar, desktop binary and installer were rebuilt at 0.1.3; the packaged-executable checks were re-run against the final sidecar; the installer ran silently for the current user and returned exit code 0; and installed-file verification confirmed both installed executables match this build and the tested package. The installed frozen backend was then driven over HTTP from a temporary workspace with no repository, Python or Node.js on PATH: it returned the extracted text of a project PDF and still rejected a binary file as not UTF-8 text.

Three limits apply. The unreadable-file notice is proven by the test suite against source rather than re-exercised through the installed build, which runs the same reader. A scanned PDF with no text layer still cannot be read, since OCR is not implemented; the reader now says so instead of staying silent. And this cycle ran no live provider workflow, so the real-provider evidence continues to describe the earlier build.

Version 0.1.4 finishes what 0.1.3 started and corrects that release's claim. The 0.1.3 fix was applied to the file reader, verified against the file reader, and shipped, while the directory walker that feeds the reader still dropped entries the reader never saw. A folder pruned by name, an entry the resolver refused, and the two-thousand-file stop all removed files before the reader's notice could mention them, so a project holding its documents in a folder named `data` reproduced the original symptom exactly, on a release whose notes said it was fixed. The walk now returns what it refused alongside what it found, and both sets reach the same notice. The lesson is recorded rather than just the fix: a defect class lives in a data path, not in a function, and verifying the layer that was reported proves that layer rather than the path.

A sweep of first-use paths then found a defect of a different kind. Opening a folder that does not exist, or one on a drive that is not connected, raised `FileNotFoundError` from a strict path resolution. Only `ValueError` has a handler, so an ordinary action returned an unexplained server error, as did listing or opening files after a project folder was removed. Those paths now report what happened. Two attachment messages were also wrong rather than merely terse: a scanned PDF was described as containing no extractable text, which reads as a broken file rather than a missing text layer, and Word or Excel documents were rejected only because their bytes happen to fail a UTF-8 decode, so a small office file that did decode would have reached the model as gibberish. Attachments are now refused by format, and the scanned-PDF message names OCR as what is missing.

Verification followed the build rather than preceding it, which matters here: the bundler's pre-build command rebuilds both the frontend and the backend sidecar, so packaged checks run before it describe bytes that are then discarded. The suite passes at 55 tests, the packaged checks ran against the final sidecar, the installer returned exit code 0, and installed-file verification confirmed both executables match this build and the tested package. The original user symptom was then reproduced against the installed build rather than against source: a project holding `data/q3.md`, a PDF, a PNG and a Markdown file was driven through a real instruction with an unreachable provider, and the transcript recovered from the installed backend's own database shows the PDF's extracted text in context and `data/ - excluded folder name.` declared to the model. The same installed build returned a readable message for a missing folder and for an absent drive.

Two limits stand. The two-thousand-file stop is declared but still truncates, so a project larger than that limit remains partly invisible even though the model is now told so. A Word document inside a project folder is declared as not UTF-8 text rather than advising an export to PDF, which the attachment route now does; the model still reports that the file was not read, which is the part that matters.

Version 0.2.0 is the first release since 0.1.1 to add capability rather than repair it, and the capability it adds sits on the one boundary this application does not own: the credentials of an agent command line tool. A subscription login can now carry an account name, each name gets its own credential directory inside the workspace, and the launch points `CLAUDE_CONFIG_DIR`, `CODEX_HOME` or `XDG_DATA_HOME` at it. Connecting a second subscription is therefore a second model row on the same provider and identifier under a different account name, and when one of them answers that its usage window is spent the broker moves to the next and the stage records the login that actually answered. OpenCode joins Claude and Codex as a third subscription provider, invoked as its read-only `plan` agent in the same empty working directory the other two get. Dictation was added alongside it: the main composer and the workflow objective and context transcribe through an OpenAI-compatible model the workspace has already connected, so speech reaches a prompt without a second credential and the recording is dropped with the request.

The mechanism was verified against the real command line tools rather than against stubs, and that is what found its two defects. The stubs said a refusal arrives with exit code zero; the Claude tool reports a refusal inside its JSON and exits nonzero as well, so the exit-code branch was discarding the only text naming the reason, and a spent subscription would have been indistinguishable from a broken install. The stubs also said a fresh credential directory is enough; the tool aborts on a directory with no configuration file at all, before it can report the sign-in that is actually missing, so the directory is now seeded. A third defect was found by reading rather than running: the exhaustion phrase list contained `429`, which matches token counts and session identifiers inside the tool's own JSON and would have moved a run onto a second subscription for no reason. The list is now worded signals only, and a Claude refusal is classified from its `result` field rather than from the whole payload, so a plan that discusses rate limiting cannot read as a spent subscription. A login that is simply not signed in is reported rather than switched away from, because that is a sign-in the workspace still has to do.

An interface change was verified the same way, by driving the running application. The dictation control compiled and bundled while sitting three levels inside a settings dialog, on a form that is not where anyone types; it is now on the main composer as well. That composer's input is controlled, so the first wiring, which set the element value and dispatched a synthetic input event, would have been discarded silently on the next render; it uses the owning state setter instead.

Verification followed the build, for the reason 0.1.4 recorded. The suite passes at 63 tests, including the switch, the cooldown ordering, the per-account directories, the OpenCode event stream, an unsigned account not being switched away from, the sign-in route and dictation refusing a model that cannot transcribe. The packaged-executable checks ran against the final sidecar, the installer returned exit code 0 for the current user, the registry records 0.2.0, and installed-file verification confirmed both executables match this build and the tested package. The new surfaces were then exercised in the installed frozen backend with no repository, Python or Node.js on its PATH: three subscription accounts were connected, each returned its own credential directory scoped to the workspace with the correct environment variable and a seeded configuration file, a key on a subscription and an account name on a keyed provider and a traversal inside an account name were all refused, and the dictation route rejected a model that cannot transcribe.

Four limits apply. No second real subscription exists on the build machine, so the handover itself is proven by the test suite and by `scripts/switch_demo.py`, which stands a scripted tool in for the real one; what was proven against the real tools is the credential isolation, the refusal shapes the switch keys on, and that an unnamed account still uses the machine's own sign-in unchanged. The cooldown is a fixed hour rather than each provider's reported reset, which none of the three report in a shared form. It is held in the running process, so a restart costs one probe against a subscription that may still be spent. And this cycle ran no live provider workflow, so the real-provider evidence in [live-verification.json](live-verification.json) continues to describe an earlier build.

Version 0.3.0 removes the product that 0.2.0 improved. The multi-stage workflow engine — discussion, planner, builder, reviewer, iterating until a review approved — is gone, along with saved workflows, reusable agents, saved prompts, run history, usage accounting and budgets. What replaces it is a harness: one conversation about one project folder, with a selector that chooses which agent answers each turn. `engine.py` is deleted; `agent.py` takes its place at a fifth of the size, and the backend as a whole is smaller than the engine's routes alone were.

The change inverts the contract 0.1 was built on, deliberately and at the user's instruction. A subscription tool used to be launched with its tools disabled, in an empty directory, returning an artifact that a reviewer and an execution mode decided whether to apply. It is now launched in the project folder with its own tools, so it reads, writes and runs commands there directly. How much it may do is chosen per turn and translated into each tool's own setting: Claude's `--permission-mode` of `acceptEdits` or `bypassPermissions`, or for read only the ordinary agent with its writing and shell tools disallowed; Codex's `--sandbox` of `read-only` or `workspace-write`, or `--dangerously-bypass-approvals-and-sandbox`; OpenCode's `plan` or `build` agent, with `--auto` for the last rung. Nothing escalates on its own, and the posture is on the message, not on the project. This is the agent tool's own permission system, not an operating-system sandbox, and the release notes and Security panel both say so.

Switching agent mid-conversation is the feature the rest exists to serve, and the reason it works is that nothing which matters lives inside a tool's session store: the three do not share one, and a Claude session cannot be handed to Codex. The conversation is held here and replayed into whichever agent is selected, with a preamble that tells the incoming one it is taking over and that the folder — not a summary of it — is the shared state. Tool calls and file reads from earlier turns are deliberately not replayed; they belong to the tool that made them, and the folder already holds their result. A conversation too long to send drops its oldest turns and says so rather than refusing to continue, because a chat that stops being answerable is worse than one that has forgotten its beginning.

Three consequences were accepted rather than worked around. An API key is not an agent: a keyed model has no tool loop, no file access and no shell, so keyed providers are refused before a turn starts, with that reason, and stay configured for dictation. A local model becomes an agent through OpenCode, which already has the loop and can address Ollama or any OpenAI-compatible endpoint, rather than through a second agent loop written here. And a turn's file changes are recorded by reading the folder before and after it, because a project folder need not be a git repository — which means anything else writing to that folder during a turn is attributed to it.

Both halves were verified against the real tools before the interface existed. Each of Claude Code, Codex and OpenCode was made to write a file under its editing posture, which is what established the flags rather than the documentation. Then the harness itself: Claude took a turn in edit mode and modified a file in the project folder, and OpenCode was selected for the next turn in the same conversation and correctly reported what Claude had added and that a previous assistant had added it. A keyed model asked to take a turn was refused with a readable reason. Two defects were caught this way rather than by the suite: Codex's `-a never` is a global flag that is ignored after the subcommand, which would have hung a turn on an approval nobody can answer, and a turn scheduled from a synchronous route has no running event loop, which would have failed every message.

The suite is 43 tests and covers what the new shape actually risks: the record of what a turn changed, the handover and its absence when the agent is unchanged, read only neither touching the folder nor reporting changes, a failed turn closing its message instead of leaving the conversation busy forever, one turn at a time, a stopped turn keeping what was already written, a restart closing out an interrupted turn rather than replaying it, each posture's translation into all three tools, subscription account switching, and the project file and command boundaries. The Playwright test drives the new window and asserts the selector offers agents but never a keyed model. Frontend type checking and the production build are clean.

Verification followed the build, as since 0.1.4. The packaged-executable checks ran against the final sidecar, the installer returned exit code 0 for the current user, and installed-file verification confirmed both executables match this build and the tested package.

Four limits stand. There is no streaming: a turn shows that it is working and its reply lands when it finishes, which for an agentic turn is minutes, and all three tools can stream in formats that were confirmed but not yet consumed. The handover is proven by the suite and by one real cross-provider exchange, not by long use. Reading the whole project twice per turn is bounded by the walker's file cap and the reader's size limit rather than by the project's real size. And the optional live provider test now requires a signed-in agent tool rather than an API key, so it exercises a different thing than it did before 0.3.0.

Version 0.3.1 gives the harness a window. The 0.3.0 release shipped a shell with no stylesheet: `global.css` had only ever covered the settings forms and the sign-in page the original dashboard was built from, and none of the classes the desktop shell uses — the layout itself, the sidebar, the conversation, the composer, the inspector — existed in it. Nothing regressed; those rules were never written. Every element rendered in document order at full window width, which is why the window looked like a stack of unrelated controls rather than an application. It was reported by a user with a screenshot, not caught by a test, because the Playwright suite selects by role and label and passes just as happily against an unstyled page.

`shell.css` is the missing half, and it targets the shape a chat with an agent actually needs. The conversation is a centred column of fixed maximum width on a calm background, because an answer set across the full width of a wide window is the single thing that makes a chat unreadable. The composer is a card at the foot of that same column, with the agent picker, the posture picker and the send control inside it rather than stacked below it, so choosing who answers is part of writing the message. The sidebar carries the project and its conversations without borders or chrome competing with them, and the inspector is a quiet right panel. Four defects visible only once it rendered were fixed with it: an existing conversation with no messages showed a blank screen rather than the opening state, the agent picker printed "Claude · Claude" for a model named after its provider and is now grouped by provider, a turn that took no measurable time reported "0m 0s", and the agent's answer was indented out of line with the message above it and the composer below.

It was verified by looking at it. The window was driven at 1500 by 940 against a seeded project with a two-agent conversation, a file change record and a project check, and screenshotted at each state: the conversation, the inspector on Changes, the file tree with a preview open, and the opening state. Each fix above was confirmed in a second pass of the same screenshots rather than reasoned about. The browser test now asserts the agent picker offers exactly the connected agents, grouped, and never a keyed model.

One limit worth recording. A stylesheet is not covered by any automated check here: the suite proves the window works, and only a person or a screenshot proves it is usable. That a whole shell could ship unstyled through a passing suite, a clean type check, a clean production build and an installed-build verification is the finding, not the missing rules themselves.

Version 0.3.2 finishes removing the product 0.3.0 replaced, in the two places the rewrite did not reach. The first was the user's own data. Models in a store created before 0.3.0 carry the name their owner typed, and the old Connect-model form asked for a job title — its placeholder was "e.g. Architecture planner" and its description said to give each model "a role it does best" — so a store held agents named Developer, Architect and Project Manager. The engine that gave those names meaning was gone; the agent picker showed the names, so the roles appeared to still be there when only the words were. Saved workflows and runs survived the same way, unreachable by any remaining code. `scripts/cleanup_legacy.py` reports both and, with `--apply`, removes rows whose kinds no longer have code and renames a role-named model after the model it actually reaches. Projects, conversations and credentials are untouched, the rename is reversible in the interface, and the store is backed up first.

The second was the interface's own words. The sign-in page still described giving every model a role and drew a Plan → Build → Review pipeline. The Connect-model form still invited a job title, still called the backend a workflow engine, still mentioned workflow exports and budget enforcement that no longer exist. One line was worse than stale: it told the user that a subscription tool "runs with its own tools disabled so only the reviewed artifact reaches your project", which had been true until 0.3.0 and was by then a false statement about what the application does to their files. It now says the tool runs inside the project folder with its own tools and that the posture is chosen per message. A safety claim that describes a removed design is more dangerous than no claim, because it is read and believed.

Both were verified in the installed build rather than in source. The 0.3.2 binary was started against a copy of the real store and driven: the agent picker returned `["Claude sonnet","Claude Opus","Claude Fable","Codex gpt-5.6-terra","Codex gpt-5.6-sol"]` with no role names left, and the Agents & providers panel was checked for each stale phrase — workflow, reviewed artifact, role it does best, one team, budget — all absent.

The finding is that a rewrite has three surfaces, not one. The code was replaced, and the tests followed it. The words the interface says about itself, and the data an existing user already created under the old design, both outlived it, and neither is reachable by any test that asserts behaviour. Prose and stored user content need their own pass, and the prose pass has to look for claims that became false rather than only for names that became wrong.

Version 0.4.0 adds Adaptive: a selection in the agent picker that chooses the agent per message. The requirement was model-agnostic routing — not a ladder of easy-to-Qwen, medium-to-Codex, hard-to-Claude, but a comparison of what a message needs against what each available agent is good at, so that a future local coder announces itself with numbers on its row rather than with a branch in the router. That is how it is built. `adaptive.py` holds a capability profile per provider and model family, overridable per model row, and a deterministic classifier that reads the request's wording and a shallow walk of the project folder — counts, languages, and whether the paths touch authentication, data or payments — into a validated requirements vector. A workspace may name a small keyed model, a local Ollama model being the intended case, to refine that vector; any failure of it falls back to the heuristics, so routing never blocks a turn and the classifier is a setting rather than a dependency. Candidates without an agent loop are excluded outright. Among the rest the cheapest whose profile covers every requirement within a point wins, and if none does, the closest. A manual pick bypasses all of it.

Escalation lives at the one boundary this harness owns. A turn is one opaque subprocess; there is no point inside it at which the agent has finished inspecting the repository and the harness could reassess, which the specification asked for. The harness therefore inspects the repository itself, before choosing, and reassesses when an attempt ends: if it failed, or the agent said it could not and changed nothing, a stronger agent continues the same turn — one rung up, the weakest sufficient agent stronger than those tried, not the dearest — with a handoff naming the objective, each earlier attempt's outcome, and the files already changed. Two escalations at most, then the turn is reported failed. The message records the requirements, the ranked candidates, the choice and its reason, and every attempt, so a routing decision is always readable afterwards. Specialist handoffs across a plan, an implementation and a review are not built; the workflow engine that did that was removed at the user's instruction in 0.3.0, and nothing here prevents a later, deliberate version of it.

The suite gained twenty-two tests: the specification's own example requests route where it says they should against the three named agents; a two-word request outranks a thirty-clause one; a keyed model is never a candidate; a row's own capability numbers change the outcome; a flagged repository raises the stakes of an otherwise plain request; escalation climbs one rung, carries a handoff the next agent actually receives, is capped, and never touches a manual choice; a bogus classifier falls back to heuristics. One defect was found by the suite before it reached anyone: the first escalation logic climbed to the strongest agent rather than the next one, which would have sent every failed local turn straight to the most expensive agent. A second, in the heuristics, read any six-word implementation request as ambiguous. Both were fixed by the tests that described the intended behaviour.

Two limits stand. Mid-turn reassessment, at the moment an agent discovers a task is larger than it looked, is not possible while a turn is one subprocess; it needs the streaming the harness still lacks, which would expose those boundaries. And the capability profiles are estimates — routing intentions, the specification's own words — which is why every one of them is a number on a row that its owner can change, and why the message shows its reasoning.

Version 0.4.1 corrects one posture, found by the live check that closed 0.4.0. Two Adaptive turns were sent through the real agent tools against a small project: "Explain what calc.py does" was classified as a low-complexity explanation and went to the free OpenCode model, which answered correctly; "Design a zero-downtime migration strategy" was classified as high-complexity planning with data risk, for which the free model fell fourteen points short, and went to Claude. Both routes were the ones the specification's examples describe, and both replies record the requirements, the ranked candidates and the reason.

Claude's reply is what exposed the defect. Read only had been mapped to Claude Code's `plan` permission mode, and in that mode the tool does not answer a question: it behaves as a planner, writes a plan file into its own home directory, attempts to call a tool that a headless session does not have, and then explains all of that before getting to the answer. That is a side effect outside the project folder, invisible to the before-and-after that records a turn's changes because it looks only at the project, and it is the wrong voice for a question. Read only is now the ordinary agent with its writing and shell tools disallowed and permission prompts refused rather than asked. It was verified against the real tool with a request that both asked for an explanation and asked for a file to be created: the explanation came back, the tool said plainly that it could not create the file under this mode, and the folder was unchanged.

The finding generalises the one recorded at 0.2.0. A tool's mode names describe the tool's intent, not the caller's; a mode called "plan" was assumed to mean "read but do not write", and it meant "produce a plan". The unit test that pinned the mapping passed throughout, because it asserted the flag and not the behaviour. Only a real turn, read by a person, showed the difference.

Version 0.4.2 teaches Adaptive that a configured local model is not an available one. The user's machine holds a dozen local models behind separate llama.cpp and Ollama servers, of which exactly one can hold the GPU at a time. Every one of them is a legitimate agent row, and the free local model is exactly what Adaptive prefers for a cheap message — so, as first shipped, it would have routed to whichever local row scored best, sent the turn to a server that was not running, waited for the tool to fail, and escalated to a cloud agent, spending time and money to discover what a one-second question would have told it. The specification listed current availability and provider health among the scoring inputs; the harness had no source for either.

It now has one. OpenCode's configuration names the server behind each local provider, and nothing else about that file is read but the address. Before choosing, Adaptive asks every loopback server in parallel — one and a half seconds at most — whether it is up and lists the model, and drops the ones that are not, recording them on the message as offline. A manual pick of an offline local agent fails at once, naming the provider and the address, instead of after the tool's own timeout. The picker greys those agents and never chooses one by default, and the agent card shows whether its server is running. Cloud agents carry no answer and are assumed up, because there is nothing local to ask.

Verified against the machine itself with the real configuration: eleven local providers were probed in one and a half seconds; the running llama.cpp server answered for its alias, the stopped ones and an Ollama model that has not been pulled came back offline, and the cloud agent came back not applicable. The suite gained five tests and an autouse fixture that points the probe at a config that does not exist, so nothing in the suite depends on what happens to be running on the machine that runs it.

One limit. The probe knows a model is served only when the server lists it; a server that lists nothing is taken at its word that it is up. And the diagnosis that prompted this — a connection test that failed against a running server — traced to the server still loading its model at the moment of the test, which the probe will now report as offline until it finishes.

Version 0.4.3 changes two sentences, because 0.4.2's installed-build check showed the first of them never reached anyone. The connection test had been taught to name what an endpoint actually serves when the configured identifier is not among them — the exact case of a llama.cpp server launched under an alias — but it said so with the wrong exception type, and the handler that turns every unexpected failure into a generic message swallowed it. It is now raised as the provider error it is. The generic message itself now names the address it tried and says that a local server usually ends in `/v1`, since the address is the thing most often wrong. Verified against the machine's real servers: the wrong identifier against the running server answers `It lists: Prometheus`, the right one connects, and a stopped server answers `Could not reach http://127.0.0.1:9095/v1`.

Version 0.4.4 adds a second Adaptive classifier: TypeSafe (docs.typesafe.ai), alongside the
existing "any keyed chat model" path. The existing classifier works by asking a chat model to
write JSON matching the requirements schema and parsing whatever comes back; TypeSafe's System
One API instead returns typed, calibrated answers to a fixed set of questions, so the same job
needs no JSON-writing and no parsing to fail. `adaptive.py` was refactored first: the bump/
complexity/risk rules that turn a task type and a handful of yes/no signals into a requirements
vector were pulled out of `heuristic_requirements` into a shared `_derive`, so the regex path and
the new TypeSafe path only ever differ in how they detect a signal, never in what the signal is
worth. `typesafe_requirements` asks one Choice (task type, matching `TASK_BASE`'s eight
categories) and six Nouls (broad scope; ambiguous — does the request name a concrete target or a
vague symptom; and one each for security/data/money/external risk) in a single call, then runs
the answers through `_derive` exactly as the regex path does.

The question wording was iterated against the real API, not shipped on the first draft. The
first `ambiguous` phrasing — "is it unclear without inspecting the code exactly what needs to
change" — read almost every short request as ambiguous, including ones with an obvious concrete
target, because that framing is technically true of any one-line instruction detached from an
actual codebase. Rephrased to ask whether the request names a broad symptom or a concrete target
("fix authentication" vs. "change this button from blue to green"), it separated cleanly, but
then read high by nature for explain/analysis/review/planning requests, which are open-ended on
purpose. `typesafe_requirements` now applies the ambiguous signal only to the four task types
that name a determinate change (debugging, implementation, refactor, edit_simple) — the same
restriction the regex heuristic already places on its own ambiguous detection, arrived at
independently and for the same reason.

Everything was verified against the real API rather than only against mocks. Twelve
representative coding requests were classified before any code was written: task type matched
all twelve; risk detection matched every seeded case; the final `ambiguous` wording separated
"fix authentication" / "improve performance" / "clean up the code" (all correctly high) from
every request with a named target (all correctly low). After wiring, `typesafe_requirements` was
called directly against the real API and matched by hand on four cases including a specialist
one (a payment webhook correctly flagged security, money, and external risk together). Then the
full chain was exercised end to end: a real encrypted TypeSafe key stored on a model row, a real
`tenant.router_model_id` lookup, a real System One call, a real Adaptive turn routed on the
result — `routing.classified_by` read back as the connected model's own name and
`routing.requirements.reason` carried the classifier's stated confidence. The `/models/{id}/test`
connection check was exercised against both a real key (200, connected) and a deliberately wrong
one (502, "TypeSafe rejected this API key") through TypeSafe's own `/v1/models` endpoint, since
it has no chat-completion surface to discover a model against the way the OpenAI-compatible path
does. The suite gained ten tests covering the shared derivation, the ambiguity restriction, the
"return None rather than raise" failure path, and `classify`'s dispatch by provider, all against
a scripted TypeSafe response rather than the network — the real-API calls above are what checked
that the scripted responses describe what the real service actually returns.

TypeSafe was added as a model provider (`typesafe` in `ModelConfig.provider`) rather than a new
per-workspace setting, so it goes through the credential storage every other keyed provider
already has — encrypted on this device, never returned to the interface — instead of a new
plaintext environment variable. It is excluded from the agent picker and from Adaptive's own
candidate list the same way every keyed provider already is (no agent loop), and it appears in a
workspace's Adaptive classifier dropdown the same way any other non-agent model already does,
so neither list needed a new special case, only a new entry in `providerNames`.

One limit stands, inherited from the classifier design generally: TypeSafe's own confidence is
recorded in the routing reason but not yet used to decide whether to trust the classification at
all — a low-confidence TypeSafe answer is treated the same as a high-confidence one, the same way
a low-confidence answer from the existing chat-model classifier already is. Gating the
classification itself on that confidence, the way Adaptive already gates escalation on whether
an attempt succeeded, is a natural next step once there is real usage to tune a threshold against.

Version 0.4.5 fixes a turn that ended with the agent asking the user to run git by hand. Under
Edit files, Codex runs in `--sandbox workspace-write`, and a probe of the installed Codex 0.155
with `codex sandbox` showed that posture keeps every `.git` directory read-only no matter what
`writable_roots` says, blocks the network by default, and even with network on cannot push
because its restricted token cannot reach the credential store. Claude's Edit files posture has
the same limit for a different reason: headless `acceptEdits` has no one to approve a shell
command. There is no configuration that lets Edit files commit; Full auto does, and always did.
So the agent is now told its posture at the top of every prompt: under Edit files it is told that
commit and push are off and to ask for that part to be resent under Full auto, rather than
discovering the wall and handing the user three git commands. One test covers the three
postures, and the README's posture table says the same in one sentence.

The same release carries the desktop settings modal fix that turn had left uncommitted: the
modal no longer depends on the router, so Settings opens from the toolbar without a route change
and closes without navigating. Local agent-tooling files (graft's wiring for Claude Code,
OpenCode and Gemini) are now gitignored.

The 0.4.5 installer was built, its bundled backend passed the packaged checks with no Python
or Node on the path, and it was installed over 0.4.4; the manifest records that the installed
executables match the build that was tested.

Version 0.4.6 ships two features an agent wrote from inside Frontier, and the lesson from how
that turn ended. Screenshots can be pasted into the composer: an image on the clipboard is
attached as a file, stored as a data URL, and written into `.frontier/attachments` under the
project before the turn so the agent can open the actual file; the prompt names where it landed.
Attachments in general now travel by id rather than by name pasted into the message. The
project list shows a pulsing dot on any project with a turn still running, fed by a small
activity endpoint that reads the runner's live tasks, polled every four seconds.

The lesson: asked to "install it so I can click my desktop shortcut", the agent built the
installer and then stopped Frontier's processes to free the executable — and Frontier is the
process hosting its turn, so the turn died with it and the installer never ran. A second agent,
told to continue, did the same. Neither is a crash: there were no Windows or WebView2 crash
records, only two clean kills, one `taskkill` and one `Stop-Process`, in the agents' own logs.
Every prompt now says that the agent runs inside Frontier as one of its subprocesses, that
stopping or reinstalling Frontier ends the turn, and that an install request means build it and
hand the installer to the user. One test covers it. The five files that agent changed were
reviewed, pass the suite and the typecheck, and are what this build carries.

Version 0.4.7 ships a project the user could remove from a session but never from Frontier
itself: a right-click on a sidebar project now offers "Remove from Frontier", which untracks the
project and deletes its conversations and events, but leaves the folder and its files exactly
where they are on disk — this had been built and tested by an earlier turn and was carried
forward uncommitted; it was reviewed here, the suite (76 tests, including its own
`test_removing_a_project_untracks_it_but_keeps_the_folder`) passed, and it was folded into this
release rather than left stranded in the working tree.

It also fixes Settings → Workspaces always showing "Not Found". `Tenants.tsx` fetched its list
through `useResource('/tenants')`, which builds a tenant-scoped URL, `/api/t/{id}/tenants`; no
such route exists, only the unscoped `/api/tenants` does, so the tab 404'd every time it opened.
It now reads the tenants list already held on the shared workspace context — the same list the
sidebar's workspace switcher already used successfully — instead of re-fetching it from the
wrong URL. Confirmed directly against a running instance of the built app: the old URL returns
404, the corrected one returns the workspace list, and `tsc --noEmit` is clean.

The 0.4.7 installer was built, its bundled backend passed the packaged checks (runtime, unittest,
pytest, compileall, frontend assets, authentication, tenant file isolation — the same suite that
exercises `/api/tenants` end to end) with no Python or Node on the path, and it was installed
over 0.4.6 for the current user; the manifest records that the installed executables match the
build that was tested.

Version 0.5.0 adds five things the desktop shell lacked next to T3 Code and PandaOS. Enter while
a turn is running now queues the message on the session (`queue` on the instruction, drained by
the turn's done callback), and Ctrl+Enter steers: since a turn is one opaque subprocess, steering
stops it and sends the new message with the conversation so far; stopping drops the queue.
Finished turns carry `cost`, computed from the served model's per-million rates (0 for a
subscription login, absent when a rate is unknown), and the message shows duration, tokens in and
out, cost, and Adaptive's attempt count. A turn that finishes while the window is unfocused raises
a desktop notification (Tauri notification plugin, with a chime synthesised by the audio API so no
sound file ships), driven by the same activity poll as the sidebar dots. Sessions can be renamed,
pinned, and archived through `PATCH …/sessions/{id}`. The app checks
`releases/latest/download/latest.json` on launch through the Tauri updater plugin and installs a
minisign-signed package in place; the public key is in `tauri.conf.json`, the private key stays
outside the repository, and `scripts/write_update_manifest.py` writes the feed. Three new tests
cover the queue, stop-drops-queue, and cost; the API test covers rename, pin, archive, and their
workspace boundary. The suite is at 79.

Version 0.6.0 makes sessions git-native. `backend/gitops.py` wraps the git command line with the
same allow-listed child environment as every other subprocess. The Changes panel now opens with
the repository: status parsed from `git status --porcelain=v1 -z --branch`, per-path diffs (a
new file diffs against `/dev/null`, which git resolves on every platform), stage and unstage,
commit, push with the upstream set on first push, and a commit message drafted by the current
agent under Read only from the staged diff alone. Before and after each editing turn the working
tree is checkpointed through a temporary index into `refs/frontier/checkpoints`, so revert
restores every changed path with `git checkout <before> -- paths` and deletes what the turn
added, which covers binaries the readable fingerprint never saw; a folder with no repository
reverts from the recorded before-text instead. A session created with `worktree: true` gets a
git worktree under `Frontier Worktrees` beside the managed projects, on a branch derived from the
first message, and `ProjectFiles.root` routes the agent, file reads, project checks, attachments
and git status to it. Four new tests cover status and staging, exact revert in a repository
(including a binary the fingerprint excluded), revert without a repository, and a worktree
session end to end through the HTTP API, including the workspace boundary. The suite is at 83.

Version 0.7.0 is the "working alongside the agent" batch. The Terminal panel became a real shell:
four Tauri commands (`pty_open`, `pty_write`, `pty_resize`, `pty_close`) wrap `portable-pty`, each
tab is one ConPTY running PowerShell in the conversation's folder, output streams to the page as
`pty-data` events, and the app's own commands are named in `build.rs` and granted in the
capability because the page is a remote loopback origin to Tauri. Shells and their xterm elements
live in a module-level registry so the panel can close and reopen without killing them, and every
child is killed on exit. Files can be saved through `PUT …/file`, which goes through
`ProjectFiles.resolve` like every read and refuses PDFs and binary content; the `FileWrite`
schema deliberately does not strip whitespace so a trailing newline survives. `GET …/search`
scans every readable text file from the walk for the query, and `GET /search/sessions` scans the
workspace's conversations for a message containing it. Inline code that looks like a relative
path with an extension, optionally `:line`, renders as a button that opens the Files panel at
that line. Two new tests cover editing and search inside the project boundary (including a
refused `.env` write and the workspace boundary) and message search across sessions. The suite
is at 85. The terminal itself was verified at runtime by launching the installed build with
WebView2 remote debugging and driving it over the Chrome DevTools Protocol with Playwright.

Version 0.8.0 is the polish batch, plus the new application icon. Adaptive is the default
selection whenever an agent is connected: the selector no longer restores a project's last
manual pick on open, and a manual pick holds only while that project stays open. The first turn's
prompt asks for a `Title:` line, `take_title` strips it from the reply and names the session
unless the user has already renamed it (`auto_named` is cleared by `PATCH`). `context_usage`
measures the transcript the agent would be sent against `TRANSCRIPT_LIMIT` and is attached to
the session detail; the composer shows it as a meter with a Compact action. `AgentRunner.compact`
asks the conversation's agent, under Read only, for a handoff summary and stores it on the
session with the id of the last message it covers; `conversation()` then hands `build_prompt`
only later messages plus the summary, and the commit-message writer shares the same agent
choice through `AgentRunner.writer`. A fifth native command, `open_with`, launches `code`,
`cursor` or the file manager on a folder from a fixed list of tools. Prompt history and the
draft stash are composer-only state, the stash in localStorage per project. The icon set was
regenerated with `tauri icon` from the supplied artwork after flood-filling its black canvas
to transparent. Four new tests cover the title line, the meter, and compaction. The suite is at 89.

Version 0.10.0 adds the tray and a single-instance guard. The tray is built after the backend is
up (`build_tray` in `main.rs`, `tray-icon` and `image-png` features, the 32 px icon embedded with
`include_bytes!`), with Open and Quit menu items and a left click that shows the window. Close
requests are intercepted in `on_window_event`: when `tray.json` in app data allows it, the default,
the window hides instead of closing; the backend writes that flag through `/api/tray` and the
General settings page toggles it. `tauri-plugin-single-instance` is registered first so a second
launch hands off to the running one and exits, which also ends the "second instance dies on the
store lock" failure seen since 0.5.0. For the case that remains, a backend started against a data
folder another backend holds, the lifespan now catches the lock error, prints one plain line, and
exits with code 3; the launcher shows that line instead of "see backend.log". Two tests cover the
flag round trip and the second backend's exit. The suite is at 101. At runtime the installed build
was checked over CDP and with process listings: closing the window left the process alive with
no visible window, a second launch produced no second process, and the tray reopened the window.
The sidebar's brand row, which repeated the window title, is gone; the collapse control moved into
the session header beside the project name.

Also in 0.10.0: a screenshot taken for the README exposed that `src/desktop/desktop.css` was never
imported. It was an orphan from an earlier design pass, and every rule appended to it since 0.5.0,
the update banner, queue and stash chips, git panel, editor, terminal tabs, preview, approval
cards, settings pages, themes' accent row, had been absent from the bundle; the components rendered
with the base button and text styles. The live sheet is `src/styles/shell.css`. The appended rules
moved there, the few legacy selectors in the orphan that the live sheet lacked (context menus and
the unified diff among them) were carried over, and the orphan was deleted. The bundle was then
checked for the class names, and the git panel's computed layout was read in the installed window.
The icon set was regenerated again from new artwork for this release. Its black canvas carried a
faint patch in one corner that a flood fill left behind, so the tile is now cut out by a rounded
rectangle fitted to its own glow ring before the corners are cleared. The desktop and Start menu
shortcuts point at the executable with icon index 0, so they take the embedded icon; after
installing, the icon was extracted from the installed executable and compared by eye.

Version 0.11.0 carries the twelve items chosen from the remaining gap list. Team mode
(`backend/team.py`) runs inside an ordinary turn: the lead answers a planning prompt with JSON,
`normalise_plan` bounds it to six tasks, `assign` honours the lead's named agent or asks Adaptive's
`choose` with a requirements vector built from the task's needs, `waves` orders tasks by
dependency and parallelism, and each task is a worker session, in a worktree when the project is
a repository, whose checkpointed diff is applied three-way to the lead's folder under a lock and
left unstaged. Parallel workers update their own task on a fresh read of the session so nothing is
lost. The lead reviews reports plus the diff and may send one fix round. Pull requests go through
`gh` (`backend/pullrequests.py`): status, create with push, review comments, and a prompt that asks
the agent to address them. Rewind (`AgentRunner.rewind`) drops messages from one of the user's
onward and rolls the folder back with `gitops.rollback` or the recorded before-text. Context chips
are typed references rendered into the prompt by `context_note` and kept on the user message.
Worktree creation copies the project's named ignored files (`gitops.hydrate`) and runs its setup
command through the shell. The dev server manager (`backend/devserver.py`) keeps one shell-run
server per folder with a port sniffed from its output. Housekeeping on the automation tick wakes
snoozed sessions and archives idle ones, remembering them first when the workspace asks. `.env`
files are listed by variable name only and, when the project asks, denied to Claude through
`--disallowedTools Read(...)`. Project memory and workspace rules are injected at the top of every
prompt. Tool versions come from `--version` against `npm view`, with `npm install -g` for updates;
Claude and Codex histories are read from their own JSONL layouts and imported once per
conversation. Attachments allow 100 per message, images to 10 MB, and other files to 50 MB kept on
disk. Fifteen new tests cover all of it, including a full team run with two workers in worktrees.
The suite is at 114. Per the user's instruction the installer was not built or installed; the
source was verified by the suite, the frontend build, and a browser run against a source backend.

Version 0.12.0 is ten small items. A spent subscription with no login left now hands the turn to
another connected agent (`AgentRunner.fallback`, up to twice, with the Adaptive handoff and an
`out of usage` attempt on the message) instead of failing it; the attempt loop became a counted
`while` so a fallback adds a round without widening Adaptive's escalation budget. A merged pull
request archives its session when the PR panel next reads it. Housekeeping removes the worktrees
of archived sessions after the workspace's `worktree_cleanup_days`, keeping the branch. A sixth
native command, `keep_awake`, pings the Windows idle timer from a thread while the page reports a
running turn. `POST /projects/clone` clones a URL into a managed folder and registers it, removing
both on failure. The terminal runs an agent's CLI on request and activates a detected venv;
`ProjectFiles.python_env` also names the environment to every agent at the top of the prompt.
Selecting text in a reply offers Cite in composer, which becomes a selection chip. Interface size
applies as body zoom and spellcheck as the composer's attribute, both per device. The MCP catalog
fills the add-server form for nine common servers. Six new tests cover fallback with and without
another agent, the environment note, clone validation and a failed clone leaving nothing behind,
worktree cleanup, and archive on merge; the browser suite checks the clone field, citation chips,
interface size, and the catalog. The suite is at 120. Built and installed only on request.

Version 0.12.1 fixes the Agents & Providers page. Measuring the installed window showed the
settings dialog was 800 pixels wide: the `:has()` rule meant to widen it never matched, so the
fixed three-column model grid squeezed each card until the footer's delete button sat outside it.
Wide dialogs now open at up to 1280 pixels through a plain rule, and each has a drag handle down
both edges (`Modal` in `ui.tsx`), so the user pulls it to any width from 560 pixels to the window,
remembered per dialog on the device. The grid fits as many 340-pixel columns as the dialog allows
and the footer wraps. The browser suite asserts the opening width, drags the edge, checks the new
width is kept after reopening, and checks every footer control stays inside its card; the same
was driven in the installed window.

Version 0.12.2 changes how a failed Claude turn is reported. `read_claude` used to answer every
`is_error` result with "confirm the subscription is signed in", which sent a signed-in user the
wrong way; it now carries Claude's own `result` text, names a sign-in problem only when that text
says so, and states the exit code when there is no text. `run_agent` writes the tool's stderr
tail to the backend log on failure, never to the conversation. The same Read-only call the team
lead makes, with the Fable model and the harness's stripped environment, was reproduced by hand
on this machine and succeeded, so the earlier failures were not a sign-in problem.
The cause of the user's failed turns was found by rebuilding the lead's prompt from a copy of the store and running the same call: the model row carried the identifier "Fable 5.1", which Claude Code reports as unrecognised; the alias is `fable`. That case is now named explicitly.

Version 0.12.3 fixes the team card's worker link, which looked up the worker in the sidebar's session list and did nothing when that list predated the worker; it now selects the session by id and refreshes the list.

The in-app updater was exercised for real on 2026-09-23: a running 0.12.1 found 0.12.3 through Settings → General → Check for updates, downloaded the signed package, verified it against the built-in key, ran the installer and reopened on 0.12.3. This is the first update Frontier performed on itself outside a test.

Version 0.12.4: `team.assign` gives review tasks (needs `review`, or a title naming review, audit, verification, integration or the final gate) to a model family other than the lead's and the authors' where one is connected, falling back to one other than the lead's; tasks are assigned work-first so a review knows its authors. The planning prompt tells the lead so. The footer during a team turn names the running workers and says the lead is waiting, which matches the process table: the lead's CLI only runs for its planning and review calls.

Version 0.12.5 repairs the review-title regex in `team.py`: in 0.12.4 its word boundaries had been written as backspace characters by an inline patch, so titles never matched and only the `review` tag routed a task. A test now checks recognition by title alone and that a word like "Previewing" does not match.

Version 0.12.6 fixes "command line tool was not found" on a machine where the tool was installed but Frontier could not see it. Frontier stays alive in the tray, so the PATH it inherited at login can predate an install; `broker.locate` now falls back to the registry's current user and machine PATH and to each tool's installer folders. Verified from a PATH stripped to System32: Claude Code was still found at ~/.local/bin through the registry.

Version 0.12.7 adds Adaptive family profiles for Codex's gpt-6-astra, gpt-6-sol and gpt-6-luna, from `codex debug models`. They apply after the generic `mini` rule, a row's own numbers still win, and older identifiers such as gpt-5.6-sol keep the provider default so existing routing does not shift.

Version 0.12.8 fixes a tester's report, diagnosed on their machine by Codex: Claude Code 2.1.280 installed with npm declares `bin/claude.exe` in its package.json, but `resolve_cli` only looked for the legacy `cli.js`, so the npm shim was found and then rejected. `broker.package_bin` now reads the package's own `bin` declaration under both the shim's folder and %APPDATA%
pm. Three tests cover the native layout found through the shim, the native layout with nothing on PATH, and the older JavaScript layout through Node; the tools on this machine still resolve as before.

Version 0.12.9 answers a turn that ended with "I've kicked off a retry… I'll report back once it completes." A turn is one subprocess and Frontier stops its whole process tree when it ends, so a background job an agent starts cannot outlive it and the agent cannot speak afterwards; the process table confirmed nothing was left running. Every prompt now says so. The Codex attempt before it had hit the 30-minute limit on a Unity gate, so a project can set its own limit. Diagnosing it also found the backend's event loop blocked for over a minute at the start of a turn in the Unity project (health checks timed out, one core at 100%): `fingerprint` and `repository_signals` read up to 2,000 files synchronously on the loop. They now run in a thread with `asyncio.to_thread`.

Version 0.12.10 sets a 90-minute floor on every turn (`MIN_TURN_MINUTES`): the effective limit is the larger of 90 and the project's own setting, so projects configured before the floor are raised rather than migrated, and the setting now accepts 90 to 480 minutes.

Version 0.13.0 adds four things and was checked live. **Agents in a browser**: with a project's
`agent_browser` on, `browser_server` adds Playwright's MCP server (`@playwright/mcp`, isolated,
headless unless asked, output to `.frontier/browser`) to every turn, and Claude gets
`--allowedTools mcp__frontier-browser` so browsing never stalls on an approval card; the first
live attempt did exactly that, waiting on a card per click. The second failed because Playwright
defaults to Chrome, the third because the tool environment lacked `PROGRAMFILES(X86)` and
Playwright looked for Edge only under the user profile. Frontier now names Edge and its path,
and passes the Windows program-location variables to every tool (they are not credentials).
The final live run: an outside MCP client asked Frontier to have Claude open a local page; the
reply gave the exact title, heading and console error, and the screenshot appeared on disk and
through the API. **Frontier as an MCP server**: `frontier_mcp.py`, standard library, started as
`frontier-backend.exe --mcp-server`, authenticates with a token in the data folder that the
backend accepts from loopback only. **Catch-up**: `POST …/catchup` gathers turns, files, commits
(filtered by their own timestamps, since `git log --since` silently ignores dates it cannot parse)
and automation runs since a moment, and summarises them under Read only on request.
**First-run wizard**: `maintenance.detect` finds each tool, its version and the identifiers to
offer, reading Codex's from `codex debug models` and OpenCode's default from its config. Six new
tests and the browser suite cover it; the suite is at 136.

Version 0.13.1 adds resuming command line conversations, checked live against both tools. Each tool
first read a file with a word in it and replied without repeating it; the file was then deleted. Tool
results are not imported, so only the tool's own session could know the word. Picked from the
resume list and asked from memory, Claude (`claude -p --resume`) and Codex (`codex exec resume`,
whose resume form takes no `-C` or `--sandbox`, so the sandbox is set by `-c sandbox_mode`) both
answered with the word. Claude's print mode keeps the same session id on resume; Frontier stores
whatever id comes back. The picker leaves out conversations started by `claude -p` (entrypoint
`sdk-cli`) and `codex exec` (originator `codex_exec`), which is how Frontier's own turns are recorded;
before this, a history import brought those in as well. Four new tests cover the list, the native
turn sending only new messages, the fallback when another agent answers or the session is gone,
and each tool's argument spelling; the suite is at 140.

Version 0.13.2 adds the Usage card. The figures come from the same endpoints Claude Code and Codex use
for their own usage views (`api.anthropic.com/api/oauth/usage` with the Claude Code sign-in,
`chatgpt.com/backend-api/wham/usage` with the Codex sign-in), read live on this machine before
wiring: Claude returned its session, week and per-model week; Codex its weekly window and plan. A good
answer is kept five minutes and a refusal until its Retry-After, even when refreshed, which a test
checks. The browser suite runs against fixed figures, so it never uses the tester's sign-in, and
checks the card shows on a wide window and hides on a narrow one.

Version 0.14.0 adds question cards, the task board and pop-out panels, checked live. The first live
question run found that Claude, asked to check with the user, reaches for its own AskUserQuestion
tool, which in print mode goes to the permission prompt tool and showed up as a permission card for
"AskUserQuestion". Frontier now turns each of its questions into a question card and returns the
answers keyed by question text, as Claude reads them; under Read only and Full auto, where no
permission prompt reaches Frontier, that tool is disallowed and Claude uses Frontier's `ask_user`.
Live: Claude asked "What is your favourite animal?", was answered "otter", and created otter.txt.
The board: Claude marked a task done through `board_update`. Codex first refused, because under
`-a never` an MCP tool that needs approval is refused; Frontier's server is now passed to Codex with
`default_tools_approval_mode="approve"`, and Codex then marked its task done. Pop-out panels were
checked in the browser suite through `window.open`; in the desktop shell they open as Tauri windows
named `popout-*`, which the capability file now covers; that path was compiled but not opened here,
because running a second copy of Frontier would hand off to the one in use. 147 tests and the
browser suite pass.

Version 0.15.0 adds the projects dashboard, starter prompts and the skills catalog. The dashboard
endpoint was checked with a busy and a quiet project and with a real dev server process: it reports
the running server and the port it printed, and stops it. Building it found that a repository with
no commits yet reported its branch as "No" (git prints "## No commits yet on main"); the branch is now
read correctly, in the Git panel too. The catalog was checked installing a skill to both
`.claude/skills` and `.agents/skills`, where Frontier's own skill listing then finds it for Claude and
for Codex (`.agents/skills` is where Codex looks in a repository), refusing to overwrite or delete a
copy edited in the project, and removing an unedited one. The browser suite picks a starter prompt,
installs a skill from the catalog, and opens a project from the dashboard. 150 tests pass.
