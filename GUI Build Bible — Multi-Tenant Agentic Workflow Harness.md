# SYSTEM SPECIFICATION: GUI / UX FOR MULTI-TENANT AGENTIC WORKFLOW HARNESS

You are an expert Principal Frontend Architect, Product Designer, and UX Engineer.

Your task is to design and implement a production-grade graphical user interface for an agentic AI workflow platform that orchestrates multiple AI models through sequential stages such as:

**Discussion → Planning → Building → Review → Re-Plan**

The application is intended to make sophisticated multi-model workflows approachable to users who may understand AI models but should not need to understand the underlying orchestration code.

The interface must feel:

* Modern
* Premium
* Professional
* Fast
* Intuitive
* Visually clean
* Easy to learn without documentation
* Powerful without appearing complicated

The GUI must not feel like an internal developer dashboard, generic Bootstrap admin panel, or unfinished AI prototype.

\---

# 1\. PRIMARY UX PHILOSOPHY

The application should follow this principle:

> Complex machinery underneath. Simple workflow on top.

A new user should be able to understand how to create and run a workflow within approximately 30 seconds of opening the application.

The interface should progressively reveal complexity.

Common actions should be obvious.

Advanced settings should be available but should not dominate the primary interface.

Avoid overwhelming the user with:

* JSON
* raw API payloads
* database IDs
* excessive configuration fields
* developer terminology
* model-provider implementation details

Unless the user intentionally opens an Advanced or Developer panel.

\---

# 2\. APPLICATION SHELL

Use a modern SaaS-style application shell.

## Left Navigation Sidebar

Persistent desktop sidebar approximately 230–260 px wide.

Primary navigation:

### Workspace

* Dashboard
* New Workflow
* Workflows
* Runs

### AI

* Models
* Agents
* Prompts

### Administration

* Tenants
* Usage
* Settings

Place Administration lower in the sidebar and visually separate it from everyday workflow tools.

Bottom of sidebar:

* Current tenant selector
* User profile/avatar
* Settings shortcut

The current tenant must ALWAYS be identifiable.

Example:

**Workspace**
Acme Development  
`Production`

Use a subtle tenant badge rather than displaying raw `tenant\_id` values everywhere.

\---

# 3\. GLOBAL TOP BAR

The top navigation bar should contain:

Left:

* Page title
* Breadcrumb when necessary

Right:

* Global search
* Notifications
* Documentation/help icon
* User avatar

When viewing a workflow, also display:

* Workflow status
* Save state
* Run button

Example:

`Website Builder`

● Ready

**Save**      **▶ Run Workflow**

The Run Workflow button should be visually dominant.

\---

# 4\. VISUAL DESIGN LANGUAGE

Design should resemble a polished modern developer/productivity application rather than an enterprise admin console.

Think in the design language of products such as:

* Linear
* Vercel
* Raycast
* Notion
* modern GitHub
* modern AI applications

Do NOT clone any one application.

## General Style

Use:

* generous whitespace
* subtle borders
* restrained shadows
* rounded corners
* crisp typography
* subtle hover states
* smooth transitions
* clear hierarchy

Avoid:

* giant gradients everywhere
* excessive glassmorphism
* neon cyberpunk styling
* huge cards
* thick borders
* cluttered dashboards
* excessive animations

\---

# 5\. COLOR SYSTEM

Support both Dark Mode and Light Mode.

Dark Mode should likely be the default because this is an AI/developer-oriented application.

## Dark Mode

Recommended general palette:

Background:

`#09090B`

Elevated background:

`#111113`

Cards/panels:

`#18181B`

Secondary card:

`#202024`

Borders:

`#2A2A30`

Primary text:

`#FAFAFA`

Secondary text:

`#A1A1AA`

Muted text:

`#71717A`

Primary accent:

Use a tasteful blue, indigo, violet, or electric blue.

Example:

`#6366F1`

Hover:

`#818CF8`

Do NOT make the entire interface purple.

Accent color should be reserved for actions, selections, workflow states, and important emphasis.

\---

# 6\. TYPOGRAPHY

Use a modern sans-serif typeface.

Recommended:

* Inter
* Geist
* Manrope

Typography hierarchy:

Page title:
24–30 px / medium or semibold

Section title:
18–20 px / semibold

Card heading:
14–16 px / medium

Body:
14 px

Secondary metadata:
12–13 px

Never rely only on font size.

Use weight, spacing, muted text, icons, and grouping to create hierarchy.

\---

# 7\. DASHBOARD

The dashboard should answer:

1. What am I currently running?
2. What happened recently?
3. Is anything failing?
4. How much AI usage am I consuming?
5. Can I quickly start something new?

Top:

# Good evening, \[Name]

Subtitle:

`Here's what's happening in your workspace.`

Primary action:

**+ New Workflow**

\---

## Dashboard Summary Cards

Use 4 compact cards:

### Active Workflows

`8`

### Runs Today

`27`

### Success Rate

`94%`

### AI Spend

`$18.42`

Each card may show a small comparison such as:

`↑ 12% from yesterday`

Cards should remain compact.

\---

# 8\. RECENT WORKFLOWS

Display as a clean list or table.

Columns:

* Workflow
* Status
* Current Phase
* Last Run
* Models
* Duration
* Actions

Example:

|Workflow|Status|Phase|Models|Last Run|
|-|-|-|-|-|
|Landing Page Generator|Running|Review|Claude → Codex|2 min ago|
|API Refactor|Complete|Approved|Sonnet → GPT|14 min ago|
|Database Migration|Failed|Planning|Claude|1 hr ago|

Status indicators should be recognizable at a glance.

\---

# 9\. WORKFLOW CREATION

This is the most important part of the product.

Clicking **New Workflow** should open a dedicated workflow creation screen.

Do NOT initially present users with dozens of configuration fields.

Start with:

# Create a Workflow

`Tell the AI team what you want to accomplish.`

Large prompt box:

> Describe what you want to build, analyze, research, or accomplish...

Below the text area:

* Attach Files
* Add Context
* Choose Template

Primary action:

**Continue**

\---

# 10\. GUIDED WORKFLOW SETUP

After entering the objective, use a simple step-by-step builder.

Recommended steps:

### 1\. Objective

What are we trying to accomplish?

### 2\. AI Team

Which models or agents participate?

### 3\. Workflow

How should they collaborate?

### 4\. Review

Confirm settings and run.

Show progress visually at top:

`Objective  →  AI Team  →  Workflow  →  Review`

Avoid a complicated configuration wizard.

\---

# 11\. AI TEAM CONFIGURATION

Users should select models through visual cards rather than raw provider configuration fields.

Example:

## Planner

Choose which AI plans the task.

┌─────────────────────────────┐
│ Claude Sonnet               │
│ Anthropic                   │
│                             │
│ Excellent reasoning         │
│ Fast • Balanced cost        │
│                         ✓   │
└─────────────────────────────┘

Other cards might include:

* Claude Opus
* GPT / Codex
* OpenCode
* Local Model
* Custom Model

Allow model search.

\---

# 12\. ROLE-FIRST MODEL SELECTION

The user should think in terms of ROLE first and MODEL second.

Example:

### Planner

Creates the execution strategy.

`Claude Sonnet ▼`

### Builder

Executes the plan.

`GPT / Codex ▼`

### Reviewer

Checks the work.

`OpenCode ▼`

Advanced users can configure:

* model
* temperature
* token limit
* endpoint
* fallback model

through an Advanced Settings drawer.

\---

# 13\. WORKFLOW PIPELINE VISUALIZATION

The GUI must visually represent the state machine.

Primary visualization:

```text
Discussion
    ↓
Planning
    ↓
Building
    ↓
Review
    ↓
Approved
```

If review fails:

```text
Review
   ↓
Changes Required
   ↓
Re-Plan
   └──────────────→ Planning
```

Represent these as attractive connected nodes.

Example:

`💬 Discussion  →  🧠 Plan  →  ⚙ Build  →  🔍 Review  →  ✓ Complete`

Each stage should show its assigned model underneath.

Example:

`PLAN`
Claude Sonnet

`BUILD`
Codex

`REVIEW`
OpenCode

\---

# 14\. WORKFLOW EDITOR

Provide a visual pipeline editor.

Users should be able to:

* add stage
* remove stage
* reorder stage
* change assigned AI
* duplicate stage
* configure stage
* define approval rules

Dragging should be supported where practical.

However, do not turn this into an unnecessarily complicated node-graph editor.

Prefer a structured horizontal or vertical pipeline.

Example:

┌────────────┐
│ DISCUSSION │
│ Claude     │
└─────┬──────┘
↓
┌────────────┐
│ PLANNING   │
│ Sonnet     │
└─────┬──────┘
↓
┌────────────┐
│ BUILD      │
│ Codex      │
└─────┬──────┘
↓
┌────────────┐
│ REVIEW     │
│ OpenCode   │
└────────────┘

Clicking a node opens its settings in a right-side inspector.

\---

# 15\. STAGE INSPECTOR

When a workflow stage is selected, open a right panel.

Example:

# Planning

Role:
`Planner`

Model:
`Claude Sonnet`

Prompt:
`Generate a detailed implementation plan...`

Context:

☑ Workflow Discussion  
☑ Uploaded Files  
☑ Previous Agent Output

Advanced:

Temperature  
Max Tokens  
Timeout  
Fallback Model

Buttons:

**Save Changes**

**Test Stage**

\---

# 16\. WORKFLOW EXECUTION SCREEN

This screen should be one of the most visually impressive parts of the application.

When a workflow runs, show live progress.

Top:

# Build Authentication System

`Running`

Elapsed:

`02:18`

Estimated spend:

`$0.31`

Then display the workflow pipeline.

Example:

✓ Discussion  
✓ Planning  
● Building  
○ Review  
○ Complete

The current stage should animate subtly.

Do NOT use excessive flashing or distracting animations.

\---

# 17\. LIVE ACTIVITY STREAM

Below or beside the pipeline, display live activity.

Example:

### Activity

`8:41:02 PM`

Claude Sonnet completed planning.

`8:41:03 PM`

Plan validated successfully.

`8:41:04 PM`

Codex started implementation.

`8:41:19 PM`

Codex created 14 files.

This should feel similar to watching a CI/CD deployment or AI team working.

\---

# 18\. AI OUTPUT VIEW

Users must be able to inspect what every model produced.

Use tabs:

**Output | Files | Logs | Metadata**

Output should render Markdown cleanly.

Code should use syntax highlighting.

Large model outputs should not appear as giant walls of raw text.

Use collapsible sections such as:

### Summary

### Decisions

### Implementation

### Risks

### Next Steps

\---

# 19\. REVIEW EXPERIENCE

Review is a core differentiator of the application.

Make review results extremely easy to understand.

Example:

# Review Result

✓ APPROVED

Quality Score

`96 / 100`

Security  
`Excellent`

Correctness  
`Excellent`

Maintainability  
`Good`

\---

If rejected:

# Changes Required

`Review score: 71 / 100`

### Critical

❌ Missing tenant validation in API route

### Important

⚠ Retry logic does not respect maximum workflow loops.

### Suggested

○ Add additional logging around model timeouts.

Primary action:

**Re-Plan with Feedback**

Secondary:

**Override \& Approve**

If permissions allow.

\---

# 20\. REPLAN VISUALIZATION

When review triggers another planning cycle, visually preserve history.

Example:

Iteration 1

Plan → Build → Review ❌

Iteration 2

Plan → Build → Review ✓

Allow clicking each iteration.

Never overwrite previous execution history.

\---

# 21\. WORKFLOW RUN DETAIL

Each workflow run should have its own persistent page.

Example URL structure:

`/workflows/website-builder/runs/1842`

Display:

* objective
* status
* tenant
* models
* runtime
* cost
* tokens
* iterations
* artifacts
* logs

\---

# 22\. WORKFLOW HISTORY

Users should be able to compare runs.

Example:

|Run|Status|Quality|Cost|Duration|Started|
|-|-|-:|-:|-:|-|
|#1842|✓ Complete|96|$0.41|3m 42s|Today|
|#1841|✓ Complete|91|$0.38|3m 10s|Yesterday|
|#1840|✕ Failed|—|$0.17|1m 04s|Sep 7|

Clicking a run opens details.

\---

# 23\. MODEL MANAGEMENT

Create a Models page.

Header:

# Models

`Connect and configure the AI models available to your workflows.`

Cards grouped by provider:

### Anthropic

Claude Sonnet  
Connected

Claude Opus  
Connected

### OpenAI

GPT  
Connected

Codex  
Connected

### Custom

OpenCode  
Connected

Local Llama  
Offline

\---

# 24\. CONNECT MODEL EXPERIENCE

Click:

**+ Add Model**

Open modal:

# Connect AI Provider

Provider:

* Anthropic
* OpenAI
* OpenAI Compatible
* Ollama
* Custom

Then reveal only necessary fields.

Example for OpenAI Compatible:

Display Name

`OpenCode`

Base URL

`http://localhost:8000/v1`

API Key

`••••••••••••••`

Model Name

`opencode`

Button:

**Test Connection**

Successful result:

✓ Connection successful  
`Response time: 422ms`

Then:

**Save Model**

\---

# 25\. TENANT MANAGEMENT

The backend has strict tenant separation.

The GUI must visually reinforce this.

Tenant selector should exist prominently in the sidebar.

Switching tenants should refresh:

* workflows
* models
* API credentials
* usage
* runs
* agents
* settings

Never allow resources belonging to another tenant to remain visually present after switching.

Use a clear transition/loading state during tenant switching.

\---

# 26\. TENANT SCREEN

Example:

# Tenants

Cards/table:

### Acme Development

Workflows: 14  
Models: 5  
Members: 7  
Monthly Spend: $187

`Open Workspace`

\---

Tenant settings:

## General

Name  
Slug  
Environment

## Limits

Maximum workflow iterations:

`3`

Monthly AI budget:

`$500`

Maximum model cost per run:

`$10`

## Models

Model routing defaults.

\---

# 27\. MODEL ROUTING MATRIX

Provide a simple matrix for default model routing.

Example:

|Role|Default Model|
|-|-|
|Discussion|Claude Sonnet|
|Planning|Claude Sonnet|
|Building|Codex|
|Review|OpenCode|

Make this editable using dropdowns.

\---

# 28\. AGENTS

Allow users to define reusable agents.

Examples:

* Senior Architect
* Backend Engineer
* Security Reviewer
* Product Manager
* QA Engineer

Each agent contains:

* Name
* Icon/avatar
* Role
* System Prompt
* Preferred Model
* Tool Permissions
* Context Rules

Agents should appear visually as members of an AI team.

\---

# 29\. WORKFLOW TEMPLATES

Provide templates so users do not always start from scratch.

Examples:

### Software Development

`Architect → Build → Code Review`

### Research

`Research → Synthesize → Fact Check`

### Content

`Draft → Edit → Quality Review`

### Debugging

`Diagnose → Fix → Test → Review`

### Custom

Start empty.

Templates should appear as polished cards.

\---

# 30\. COMMAND PALETTE

Support keyboard shortcut:

`Ctrl/Cmd + K`

Command palette actions:

* New Workflow
* Search Workflows
* Switch Tenant
* View Runs
* Add Model
* Open Settings

This dramatically improves usability for experienced users.

\---

# 31\. GLOBAL SEARCH

Search across:

* workflows
* runs
* models
* agents
* prompts

Results grouped by category.

Example:

WORKFLOWS

API Builder

RUNS

API Builder · #1842

MODELS

Claude Sonnet

\---

# 32\. EMPTY STATES

Every empty page must explain what to do next.

Bad:

`No workflows found.`

Good:

# Build your first AI workflow

Create a team of AI models that can plan, build, and review work together.

**+ Create Workflow**

Optional link:

`Explore templates`

\---

# 33\. LOADING STATES

Avoid blank screens.

Use:

* skeleton loaders
* status indicators
* progress bars
* subtle stage animations

Never freeze the interface while a model is working.

Workflow execution may take significant time.

The user should always understand that progress is occurring.

\---

# 34\. ERROR STATES

Errors must be written for humans.

Bad:

`HTTP 500 MODEL\_BROKER\_PROVIDER\_ERROR`

Good:

# Claude couldn't complete this step

The Anthropic request failed before the planning stage completed.

`API authentication failed.`

Actions:

**Retry**

**Change Model**

**View Technical Details**

Technical information should be collapsible.

\---

# 35\. TENANT ISOLATION ERROR

If tenant isolation is violated, make the error unmistakable.

Example:

# Workspace access blocked

This resource belongs to a different tenant and cannot be accessed from the current workspace.

No data from the other tenant should be rendered.

Button:

**Return to Workspace**

Technical details may contain:

`TenantIsolationViolationException`

but only inside a Developer Details panel.

\---

# 36\. COST VISIBILITY

Because multiple models can incur significant usage costs, display costs without making the product feel like a billing dashboard.

Workflow run:

`Tokens 42.8K · Cost $0.48`

Clicking opens breakdown:

Planning  
Claude Sonnet  
`$0.11`

Building  
Codex  
`$0.29`

Review  
OpenCode  
`$0.08`

\---

# 37\. USAGE PAGE

Charts:

### AI Spend

Today / Week / Month

### Token Usage

### Runs

### Spend by Model

### Spend by Workflow

Allow filtering by:

* tenant
* model
* provider
* workflow
* date range

\---

# 38\. RESPONSIVE DESIGN

Primary target:

Desktop application.

Secondary:

Tablet.

Mobile should support:

* monitoring runs
* approving reviews
* reading outputs
* simple workflow creation

Do not attempt to cram the full workflow editor onto a phone.

On mobile, convert pipeline into vertical stages.

\---

# 39\. ACCESSIBILITY

Follow WCAG AA wherever practical.

Requirements:

* adequate contrast
* visible keyboard focus
* keyboard navigation
* labels for icons
* ARIA attributes
* usable screen-reader hierarchy
* never communicate status with color alone

Example:

✓ Approved

not merely a green dot.

\---

# 40\. KEYBOARD INTERACTIONS

Recommended shortcuts:

`Ctrl/Cmd + K`

Command palette

`Ctrl/Cmd + Enter`

Run workflow

`Ctrl/Cmd + S`

Save workflow

`Esc`

Close inspector/modal

`R`

Retry failed stage when appropriate

Display shortcuts in tooltips.

\---

# 41\. TOAST NOTIFICATIONS

Use small unobtrusive notifications.

Examples:

✓ Workflow saved

✓ Model connected

✓ Run completed

⚠ Claude rate limit reached

✕ Workflow failed

Do not use modal dialogs for routine status messages.

\---

# 42\. CONFIRMATION DIALOGS

Only require confirmation for destructive actions.

Examples:

* Delete workflow
* Delete agent
* Disconnect provider
* Delete tenant
* Cancel active run

Do not ask users to confirm ordinary actions.

\---

# 43\. SETTINGS

Settings should include:

### General

Theme  
Default tenant  
Date/time formatting

### AI

Default models  
Fallback behavior  
Default workflow limits

### Security

API credentials  
Session management

### Developer

Logging  
API endpoint  
Debug mode  
Raw event viewer

\---

# 44\. ADVANCED MODE

Provide optional:

`Advanced Mode`

Turning this on exposes:

* raw prompts
* JSON outputs
* provider IDs
* request metadata
* execution payload
* token counts
* temperatures
* endpoint configuration
* trace IDs
* event stream

Default users should not need Advanced Mode.

\---

# 45\. DEVELOPER TRACE VIEW

Advanced users need a complete execution trace.

Example:

```text
20:41:13 Workflow Started

20:41:13 State → DISCUSSION

20:41:15 Model Request
provider: anthropic
model: claude-sonnet

20:41:18 State → PLANNING

20:41:22 PlanArtifact validated

20:41:22 State → BUILDING

20:42:01 Build completed

20:42:02 State → REVIEW

20:42:08 ReviewReport
approved: false

20:42:08 State → REPLAN
iteration: 2
```

Allow:

**Copy Trace**

and

**Download Logs**

\---

# 46\. PLAN ARTIFACT GUI

The backend's structured PlanArtifact should be rendered as human-readable UI.

Instead of:

```json
{
  "title": "...",
  "execution\_steps": \[...]
}
```

Display:

# Authentication Service Implementation

## Execution Plan

1. Create authentication models
2. Implement token service
3. Add API routes
4. Add middleware
5. Write tests

## Dependencies

FastAPI  
Pydantic  
JWT

## Risk Assessment

Moderate

Raw JSON available under:

`View JSON`

\---

# 47\. REVIEW REPORT GUI

ReviewReport should render visually.

Example:

# Code Review

### Quality

`92 / 100`

### Security Findings

2

### Vulnerabilities

⚠ Missing CSRF protection

⚠ Token expiry not validated

### Rejection Reasons

The implementation cannot be approved until authentication expiration is enforced.

Button:

**Send Feedback to Planner**

\---

# 48\. ITERATION LIMIT UX

If the workflow reaches `max\_workflow\_iterations`:

# Workflow stopped

The workflow attempted 3 review cycles without reaching approval.

No additional model calls were made.

Actions:

**Review Issues**

**Increase Limit \& Continue**

**Edit Workflow**

Never silently continue beyond configured limits.

\---

# 49\. CREDENTIAL UX

Never reveal complete API keys after saving them.

Display:

`sk-ant-••••••••93KF`

Actions:

Replace Key

Test Connection

Delete Credential

Use clear provider icons and status indicators.

\---

# 50\. SECURITY UX

Security-sensitive actions must be explicit.

Examples:

When changing tenants:

`Switching to Acme Production`

When disconnecting a model:

`4 workflows currently use this model.`

When deleting a tenant:

Require typing tenant name.

\---

# 51\. FRONTEND ARCHITECTURE

Preferred stack:

### Framework

React + TypeScript

Prefer:

Next.js

or

Vite + React if no server-side UI functionality is required.

### Styling

Tailwind CSS

### Component Foundation

Use:

shadcn/ui

or equivalent accessible component primitives.

### Icons

Lucide Icons.

### State

Use:

Zustand

or

React Context for simple global state.

Use TanStack Query for server state.

### Forms

React Hook Form

with

Zod validation.

\---

# 52\. FRONTEND DIRECTORY STRUCTURE

Recommended structure:

```text
src/

  app/

  components/
    layout/
    workflow/
    models/
    agents/
    tenants/
    usage/
    ui/

  features/
    workflows/
    runs/
    models/
    tenants/
    agents/

  hooks/

  lib/
    api/
    auth/
    formatting/
    validation/

  stores/

  types/

  styles/
```

Do not place the entire GUI into a few giant components.

\---

# 53\. COMPONENTIZATION

Create reusable components such as:

`AppSidebar`

`TopNavigation`

`TenantSwitcher`

`WorkflowCard`

`WorkflowPipeline`

`WorkflowStage`

`StageInspector`

`ModelSelector`

`ModelBadge`

`AgentCard`

`RunStatus`

`RunTimeline`

`ReviewScore`

`PlanViewer`

`ExecutionLog`

`UsageChart`

`EmptyState`

`ConfirmDialog`

\---

# 54\. API LAYER

Frontend must never directly contain provider API keys or call Claude/OpenAI APIs itself.

Frontend communicates only with the harness/backend API.

Recommended client abstraction:

```text
api.workflows.create()

api.workflows.get()

api.workflows.run()

api.runs.get()

api.runs.cancel()

api.models.list()

api.models.test()

api.tenants.list()

api.usage.get()
```

Keep API access centralized.

\---

# 55\. REAL-TIME EVENTS

Workflow execution should update in real time.

Preferred mechanisms:

WebSockets

or

Server-Sent Events.

Events might include:

```text
workflow.started

stage.started

model.request.started

model.request.completed

plan.validated

review.completed

workflow.replanning

workflow.completed

workflow.failed
```

UI should update without refreshing.

\---

# 56\. DESIGN TOKENS

Define reusable CSS variables.

Example:

```css
--background
--foreground

--card
--card-foreground

--muted
--muted-foreground

--border

--primary
--primary-foreground

--success
--warning
--danger

--radius-sm
--radius-md
--radius-lg
```

Never scatter arbitrary colors throughout components.

\---

# 57\. SPACING SYSTEM

Use consistent spacing increments.

Recommended base:

4px

Common:

4  
8  
12  
16  
20  
24  
32  
40  
48

Avoid random margins such as 13px, 19px, 27px without reason.

\---

# 58\. BORDER RADIUS

Recommended:

Small controls:
6px

Inputs/buttons:
8px

Cards:
10–12px

Large panels:
12–16px

Avoid excessively rounded "bubble" interfaces.

\---

# 59\. BUTTON HIERARCHY

Primary:

Filled accent.

Example:

**Run Workflow**

Secondary:

Neutral elevated button.

Example:

**Save**

Ghost:

Minimal.

Example:

`View Logs`

Danger:

Red only for destructive actions.

Example:

**Delete Workflow**

Never have multiple visually dominant buttons in one section.

\---

# 60\. ICONOGRAPHY

Use icons when they improve recognition.

Suggested mappings:

Workflow:
GitBranch / Workflow

Models:
Cpu

Agents:
Bot

Runs:
PlayCircle

Review:
ShieldCheck

Planning:
Brain / ListTree

Build:
Hammer / Code

Discussion:
MessagesSquare

Settings:
Settings

Usage:
BarChart3

Tenant:
Building2

Do not place icons beside every piece of text.

\---

# 61\. STATUS SYSTEM

Use standardized status components.

### Workflow

Draft

Ready

Running

Waiting

Complete

Failed

Cancelled

### Stage

Pending

Running

Completed

Failed

Needs Changes

### Review

Approved

Changes Required

Never invent different terminology on separate pages.

\---

# 62\. ONBOARDING

First-time users should receive lightweight onboarding.

Screen 1:

# Build with an AI team

Different models can plan, build, and review your work together.

Screen 2:

# Assign the right AI to each job

Choose the best model for planning, coding, reviewing, or research.

Screen 3:

# Let them check each other's work

Automated reviews can send work back for improvement.

Button:

**Create My First Workflow**

Do not create a long tutorial.

\---

# 63\. TOOLTIP RULE

If an icon's function might not immediately be obvious, provide a tooltip.

Examples:

`Duplicate stage`

`View raw output`

`Retry stage`

Do not require users to memorize icons.

\---

# 64\. CONTEXT MENUS

Workflow context menu:

* Open
* Run
* Duplicate
* Rename
* Export
* Archive
* Delete

Run menu:

* Open
* Duplicate Workflow
* View Logs
* Export Results

\---

# 65\. FILE ATTACHMENTS

Workflow input should support drag-and-drop.

Display uploads as compact file chips/cards.

Example:

📄 requirements.pdf  
`1.8 MB`  ×

📄 backend.py  
`22 KB`  ×

Show upload progress.

\---

# 66\. ARTIFACT OUTPUT

If a workflow creates artifacts such as:

* source files
* documents
* plans
* reports
* generated JSON
* configuration

show them in an Artifacts panel.

Example:

# Artifacts

`auth\_service.py`

`requirements.txt`

`implementation\_plan.md`

`review\_report.json`

Actions:

Open

Copy

Download

\---

# 67\. RUN COMPLETION SCREEN

Successful workflow:

# Workflow complete

✓ Approved by reviewer

Quality score:

`96 / 100`

Duration:

`3m 42s`

Models:

Claude Sonnet → Codex → OpenCode

Cost:

`$0.41`

Primary:

**View Results**

Secondary:

**Run Again**

\---

# 68\. PAGE TRANSITIONS

Transitions should be subtle.

Recommended:

150–250 ms.

Avoid:

* bouncing
* excessive sliding
* dramatic scaling
* long fades

The interface should feel immediate.

\---

# 69\. PERFORMANCE

Optimize for responsiveness.

Requirements:

* code splitting
* lazy-load heavy screens
* virtualize large log lists
* debounce search
* cache API requests
* optimistic UI where safe

The GUI must remain responsive while workflows execute.

\---

# 70\. PRODUCTION QUALITY REQUIREMENTS

Do not ship:

* placeholder pages
* dead buttons
* fake dropdowns
* mock navigation
* lorem ipsum
* console errors
* inaccessible controls
* unfinished mobile behavior
* inconsistent spacing
* hard-coded fake model data mixed with real data

Every visible control must work.

\---

# 71\. UX TEST

Before declaring the GUI complete, verify that a new user can complete this flow without documentation:

1. Open application.
2. Create workflow.
3. Describe objective.
4. Select planner.
5. Select builder.
6. Select reviewer.
7. Start workflow.
8. Watch current stage.
9. Inspect AI output.
10. Understand review result.
11. See whether re-planning occurred.
12. View final artifacts.

If any step is confusing, redesign it.

\---

# 72\. PRIMARY WORKFLOW MOCKUP

The ideal workflow page should roughly follow this information hierarchy:

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Authentication API                              Ready    ▶ Run       │
├───────────────┬──────────────────────────────────────────────────────┤
│               │                                                      │
│ Dashboard     │  Authentication API                                  │
│ Workflows     │                                                      │
│ Runs          │  Build a secure JWT authentication API.              │
│               │                                                      │
│ Models        │  DISCUSS → PLAN → BUILD → REVIEW                     │
│ Agents        │                                                      │
│               │  ✓          ✓       ●        ○                       │
│ Tenants       │                                                      │
│ Usage         │  ┌───────────────────────────────────────────────┐   │
│ Settings      │  │ CURRENT STAGE                                 │   │
│               │  │                                               │   │
│               │  │ Building                                      │   │
│               │  │ Codex                                         │   │
│               │  │                                               │   │
│               │  │ Implementing authentication middleware...     │   │
│               │  └───────────────────────────────────────────────┘   │
│               │                                                      │
│               │  Activity                                            │
│               │                                                      │
│               │  ✓ Plan created                    8:41:18 PM         │
│               │  ✓ Plan validated                  8:41:19 PM         │
│               │  ● Codex building                  8:41:20 PM         │
│               │                                                      │
├───────────────┴──────────────────────────────────────────────────────┤
│ Tokens 21.4K                 Cost $0.19                 01:42         │
└──────────────────────────────────────────────────────────────────────┘
```

\---

# 73\. FINAL DESIGN PRINCIPLE

The product should make the user feel like they are directing a small team of specialized AI workers.

They should always understand:

**What are we doing?**

**Which AI is doing it?**

**What stage are we on?**

**What did it produce?**

**Did another AI approve it?**

**If it failed, what happens next?**

Those questions should be answerable visually without opening logs or reading documentation.

\---

# 74\. IMPLEMENTATION MANDATE

Build the frontend as a production-quality application.

Requirements:

* React
* TypeScript
* responsive layout
* reusable component architecture
* polished dark and light themes
* strong accessibility
* real backend API integration layer
* real-time workflow state support
* appropriate loading states
* robust errors
* tenant-aware state management
* no direct model API calls from browser
* visually polished workflow execution
* readable AI output
* clear review/replan UX
* intuitive configuration

Do not merely generate wireframes.

Implement the complete GUI structure, reusable component system, routing, state management, forms, dialogs, workflow visualization, run monitoring screens, model configuration, tenant management, and supporting UI required for a functional application.

The final result should look and behave like software ready for real users, not an engineering proof of concept.

\---

# 75\. PRIORITY ORDER

If development time or scope becomes constrained, prioritize implementation in this exact order:

1. Application shell/navigation
2. Workflow creation
3. Workflow pipeline editor
4. Run/execution screen
5. Live stage status
6. Plan viewer
7. Review/replan interface
8. Model configuration
9. Run history
10. Tenant switching
11. Error handling
12. Usage dashboard
13. Agent management
14. Templates
15. Advanced/developer tools

Core workflow usability must never be sacrificed for secondary administrative features.

\# 76. DESKTOP APPLICATION \& WINDOWS INSTALLER



The completed application must be capable of being distributed as a normal Windows desktop application through a standard `.exe` installer.



The end user must NOT be required to manually install:



\* Python

\* Node.js

\* npm

\* pnpm

\* development tools

\* command-line dependencies



The intended user experience is:



Download installer

→ Run installer

→ Launch application

→ Configure AI providers

→ Create workflows

→ Run workflows



\---



\# 77. DESKTOP ARCHITECTURE



Preferred desktop architecture:



\*\*Tauri + React/TypeScript Frontend + Bundled Python Backend\*\*



The existing React frontend should be used as the desktop UI.



The Python workflow harness should remain the authoritative backend.



Tauri should act as the native desktop application shell.



Do not rewrite the Python workflow engine in JavaScript or Rust solely for packaging purposes.



Architecture:



```text

Windows Desktop Application

&#x20;       │

&#x20;       ├── Tauri Native Shell

&#x20;       │

&#x20;       ├── React / TypeScript GUI

&#x20;       │

&#x20;       └── Bundled Python Workflow Service

&#x20;               │

&#x20;               ├── ModelBroker

&#x20;               ├── Workflow Engine

&#x20;               ├── Tenant Isolation

&#x20;               ├── State Machine

&#x20;               ├── Database

&#x20;               └── Provider Integrations

```



The desktop shell should automatically manage the Python backend lifecycle.



\---



\# 78. BACKEND PROCESS MANAGEMENT



When the desktop application launches:



1\. Determine whether the bundled backend service is already running.

2\. If not running, start it automatically.

3\. Wait until the backend reports healthy.

4\. Connect the frontend.

5\. Display the application normally.



When the desktop application closes:



1\. Gracefully terminate the bundled backend process if owned by this application.

2\. Flush pending database/log writes.

3\. Avoid leaving orphan Python processes running.



The user should never need to manually start or stop the backend.



\---



\# 79. LOCAL BACKEND COMMUNICATION



For the desktop build, the frontend should communicate with the local Python backend using localhost.



Preferred:



```text

127.0.0.1

```



Do not expose the desktop backend to the local network by default.



Bind only to localhost unless the user explicitly enables remote access.



Example:



```text

http://127.0.0.1:<dynamic-port>

```



Prefer a dynamically selected available local port where practical.



The Tauri shell should communicate the selected backend port to the frontend.



Do not hard-code a port if doing so creates unnecessary conflicts.



\---



\# 80. BACKEND HEALTH CHECK



Implement a lightweight health endpoint.



Example:



```text

GET /health

```



Example response:



```json

{

&#x20; "status": "ok",

&#x20; "version": "1.0.0"

}

```



The desktop shell should wait for this endpoint before considering the application fully started.



If the backend fails to launch, show a human-readable recovery screen.



Example:



\# Application service failed to start



The local workflow engine could not be started.



Actions:



\*\*Retry\*\*



\*\*Open Logs\*\*



\*\*Exit\*\*



Do not show a blank window.



\---



\# 81. PYTHON DISTRIBUTION



The Windows installer must include everything required for the Python backend.



Users must not need a system Python installation.



Use a production packaging method such as:



\* PyInstaller

\* Nuitka

\* equivalent reliable Python executable bundler



The resulting backend executable should run without opening a visible terminal window.



Example internal executable:



```text

workflow-engine.exe

```



The Tauri application may launch this binary as a sidecar process.



\---



\# 82. TAURI SIDECAR



Prefer bundling the Python backend as a Tauri sidecar.



Example conceptual structure:



```text

resources/

&#x20;   workflow-engine.exe

```



Tauri is responsible for:



\* launching the backend

\* tracking its process ID

\* detecting crashes

\* terminating it when appropriate

\* locating the correct packaged binary

\* relaying backend startup errors to the UI



Do not depend on absolute developer-machine paths.



\---



\# 83. DEVELOPMENT VS PRODUCTION



The project must clearly separate development execution from packaged execution.



Development:



```text

React dev server

\+

Python development server

```



Production:



```text

Tauri compiled frontend

\+

Bundled workflow-engine.exe

```



Provide scripts such as:



```text

npm run dev



npm run desktop:dev



npm run build



npm run desktop:build

```



Names may vary, but the workflow should be simple and documented.



\---



\# 84. WINDOWS INSTALLER



Generate a professional Windows installer.



Preferred outputs:



```text

AgentHarness-Setup-x64.exe

```



Optionally also generate:



```text

AgentHarness-x64.msi

```



The `.exe` installer is the primary distribution artifact.



The installer should support:



\* normal Windows installation

\* Start Menu shortcut

\* optional desktop shortcut

\* Programs \& Features registration

\* clean uninstall

\* upgrade installation

\* application icon

\* version metadata

\* publisher information

\* configurable installation directory where appropriate



\---



\# 85. APPLICATION DATA STORAGE



Never store user-generated data inside the installation directory.



Use the appropriate Windows per-user application data directory.



Conceptually:



```text

%LOCALAPPDATA%\\AgentHarness\\

```



Suggested structure:



```text

AgentHarness/

&#x20;   config/

&#x20;   data/

&#x20;   logs/

&#x20;   cache/

&#x20;   backups/

```



Persistent items may include:



\* application database

\* tenant configuration

\* workflow definitions

\* run metadata

\* user preferences

\* encrypted secrets references

\* logs



Updating or reinstalling the application must not erase user data.



\---



\# 86. DATABASE STORAGE



If using SQLite for local desktop storage, place the database in the user's application-data directory.



Example:



```text

%LOCALAPPDATA%\\AgentHarness\\data\\agentharness.db

```



Do not embed a writable production database inside the application bundle.



Implement migrations so future versions can safely upgrade existing databases.



\---



\# 87. API KEY SECURITY



Do NOT store provider API keys in:



\* source code

\* frontend bundles

\* plaintext configuration files

\* localStorage

\* unencrypted SQLite columns



Preferred Windows credential storage:



\*\*Windows Credential Manager\*\*



or a secure credential-storage library backed by native OS facilities.



Examples of secrets:



\* OpenAI API key

\* Anthropic API key

\* custom provider credentials



The application database should store only an identifier/reference to the secured credential when possible.



\---



\# 88. SECRET DISPLAY



Once an API key has been saved, never display the complete value again.



Display:



```text

sk-proj-••••••••••••4F29

```



Actions:



\* Replace

\* Test

\* Remove



The frontend must never receive raw stored credentials unless technically unavoidable.



Provider requests should be performed by the backend.



\---



\# 89. APPLICATION CONFIGURATION



Non-sensitive application configuration may be stored locally.



Examples:



\* selected theme

\* last active tenant

\* UI preferences

\* default workflow

\* window dimensions

\* sidebar state



Sensitive information must remain in secure storage.



\---



\# 90. LOGGING



Create structured application logs.



Suggested path:



```text

%LOCALAPPDATA%\\AgentHarness\\logs\\

```



Separate when practical:



```text

desktop.log

backend.log

errors.log

```



Implement log rotation to prevent unlimited growth.



Do not log:



\* complete API keys

\* authorization headers

\* sensitive credentials

\* unnecessary private prompt contents



Where sensitive payload logging is available for debugging, it must be explicitly opt-in.



\---



\# 91. CRASH HANDLING



If the Python backend crashes while the GUI remains open:



Show:



\# Workflow engine stopped



The local workflow service unexpectedly exited.



Actions:



\*\*Restart Service\*\*



\*\*Open Logs\*\*



The user should not need to restart the entire computer or open a terminal.



If practical, automatically attempt one safe restart before surfacing the error.



Avoid infinite restart loops.



\---



\# 92. SINGLE INSTANCE



Prevent multiple accidental instances from launching conflicting backend services.



Prefer single-instance application behavior.



If the application is already running and the user launches it again:



\* focus the existing window



rather than launching another complete backend instance.



\---



\# 93. APPLICATION ICON



Create a professional application icon set.



Provide required sizes for:



\* Windows executable

\* installer

\* taskbar

\* Start Menu

\* title bar



Use a recognizable product mark that remains readable at small sizes.



Do not ship default framework icons.



\---



\# 94. APPLICATION METADATA



Executable metadata should include:



Product Name



Company / Publisher



Application Version



Copyright



Description



Example:



```text

Product: Agent Harness

Version: 1.0.0

Description: Multi-model AI workflow orchestration platform

```



Actual branding may be changed later through configuration.



\---



\# 95. VERSIONING



Use semantic versioning.



Example:



```text

1.0.0

1.0.1

1.1.0

2.0.0

```



Maintain one authoritative application version where possible and propagate it to:



\* frontend

\* backend

\* Tauri application

\* installer metadata



Avoid manually maintaining conflicting version numbers.



\---



\# 96. UPDATE-SAFE STORAGE



Application upgrades must preserve:



\* tenants

\* workflows

\* agents

\* prompts

\* settings

\* provider configurations

\* run history

\* user-created templates



Installer upgrades must never delete the application-data directory without explicit user choice.



\---



\# 97. AUTO UPDATE READINESS



Structure the application so automatic updates can be added later.



Do not require auto-update functionality for the first release unless otherwise specified.



However, do not design the packaging architecture in a way that prevents Tauri's update system from being added later.



Future behavior may include:



```text

Update available

Version 1.2.0



What's New



\[Install Update]

\[Later]

```



\---



\# 98. CODE SIGNING



The build pipeline should support Windows code signing.



Unsigned development builds are acceptable during development.



Production distribution should eventually sign:



\* application executable

\* installer executable



This reduces Windows SmartScreen warnings and verifies publisher identity.



Keep code-signing configuration external to source control.



Never commit signing certificates or private keys.



\---



\# 99. WINDOWS SMARTSCREEN



The application should be structured with eventual code signing in mind.



Do not attempt to bypass or disable Windows security warnings.



A professional production deployment should use a legitimate code-signing certificate.



\---



\# 100. FIREWALL BEHAVIOR



Because the backend should bind to localhost, normal usage should not require exposing the application to the network.



Avoid causing unnecessary Windows Firewall permission dialogs.



Remote network access, if implemented later, must be an explicit user-controlled feature.



\---



\# 101. OFFLINE BEHAVIOR



The application should launch even if the computer has no internet connection.



If remote model providers cannot be reached, clearly indicate:



\# No internet connection



Cloud AI providers are currently unavailable.



Local models may still be used if configured.



The GUI, workflow history, settings, and previous outputs should remain accessible where possible.



\---



\# 102. LOCAL MODEL SUPPORT



Desktop packaging should remain compatible with local AI providers.



Examples:



\* Ollama

\* LM Studio

\* vLLM

\* OpenAI-compatible local endpoints



The application should not necessarily bundle these model runtimes.



Instead it should be capable of detecting/configuring them.



Example:



\### Ollama



Status:



✓ Detected



Endpoint:



`http://127.0.0.1:11434`



Models:



`qwen3`



`llama`



`deepseek`



If not installed:



`Ollama was not detected.`



Do not silently download multi-gigabyte models.



\---



\# 103. DESKTOP SETTINGS



Add a Desktop section under Settings.



Possible options:



\## Startup



☐ Launch at Windows startup



\## Window



☑ Remember window size and position



\## Service



Backend Status:



`● Running`



Port:



`51423`



Version:



`1.0.0`



Button:



\*\*Restart Workflow Engine\*\*



\## Logs



\*\*Open Logs Folder\*\*



\---



\# 104. NATIVE OPERATING SYSTEM ACTIONS



Where useful, the desktop application may provide native actions through Tauri.



Examples:



\* Open logs directory

\* Open generated artifact directory

\* Save file dialog

\* Open file dialog

\* reveal artifact in Explorer

\* desktop notifications

\* system tray integration



Use native capabilities only when they improve usability.



\---



\# 105. FILE SYSTEM SECURITY



The frontend should not have unrestricted filesystem access.



Use Tauri's permission/capability system.



Only expose filesystem operations required by application functionality.



Do not enable broad shell execution or unrestricted arbitrary-command execution from frontend JavaScript.



\---



\# 106. BACKEND AUTHENTICATION



Although the backend is local, do not blindly assume every localhost request is trusted.



Use an application-generated session token or comparable mechanism between the desktop GUI and backend.



The token may be generated when the backend launches and supplied securely to the Tauri frontend.



This helps prevent unrelated local applications from invoking privileged workflow endpoints.



\---



\# 107. DYNAMIC LOCAL PORT



Where practical:



1\. Desktop shell selects an unused localhost port.

2\. Backend starts using that port.

3\. Backend generates/receives an application session secret.

4\. Tauri exposes connection information to the React application.

5\. React establishes API/WebSocket/SSE connections.



Conceptually:



```text

Tauri

&#x20; ↓

Start workflow-engine.exe



Host: 127.0.0.1

Port: 51423

Session: generated-secret



&#x20; ↓



React GUI

&#x20; ↓

localhost API

```



Connection information should exist only for the running application session where practical.



\---



\# 108. DESKTOP NOTIFICATIONS



Support optional Windows notifications.



Useful notifications:



\* Workflow completed

\* Workflow failed

\* Review requires attention

\* Maximum iteration limit reached



Do not spam notifications for individual workflow stages.



Allow notifications to be disabled.



\---



\# 109. SYSTEM TRAY



System tray support is optional for the first release.



If implemented:



Menu:



Open Agent Harness



Active Runs: 2



Pause Notifications



Exit



Closing the main window should have clearly defined behavior.



Do not unexpectedly leave the app running in the tray unless the user understands that behavior.



\---



\# 110. RUNS WHILE WINDOW IS MINIMIZED



Workflow execution should continue if the window is minimized.



If the user closes the application while a workflow is actively running, warn them.



Example:



\# A workflow is still running



Closing Agent Harness will stop the active workflow.



\*\*Keep Running\*\*



\*\*Stop \& Exit\*\*



Behavior must be predictable.



\---



\# 111. INSTALLATION TESTING



Before considering desktop packaging complete, test on a clean Windows environment where the following are NOT installed:



\* Python

\* Node

\* npm

\* Rust toolchain



The installer must still successfully install and launch the application.



\---



\# 112. INSTALLER ACCEPTANCE TEST



A release candidate must pass this sequence:



1\. Download `AgentHarness-Setup-x64.exe`.

2\. Install application.

3\. Launch from Start Menu.

4\. Application starts with no terminal window.

5\. Backend starts automatically.

6\. GUI successfully connects.

7\. Create a tenant/workspace.

8\. Add an AI provider.

9\. Test provider connection.

10\. Create a workflow.

11\. Run workflow.

12\. Observe live progress.

13\. Complete workflow.

14\. Close application.

15\. Reopen application.

16\. Confirm data remains.

17\. Install newer version.

18\. Confirm existing data remains.

19\. Uninstall application cleanly.



No developer tools should be required during this sequence.



\---



\# 113. BUILD PIPELINE



Provide a repeatable release build process.



Ideal end result:



```text

dist/

&#x20;   AgentHarness-Setup-x64.exe

```



The build pipeline should:



1\. Run frontend tests.

2\. Build React frontend.

3\. Build Python backend executable.

4\. Copy backend executable into Tauri resources.

5\. Build Tauri desktop application.

6\. Generate Windows installer.

7\. Produce checksums where appropriate.



A developer should be able to create a release build with one documented command or script.



Example:



```text

npm run release:windows

```



\---



\# 114. CI/CD READINESS



Structure the build so it can later run through GitHub Actions or another CI platform.



Future release workflow should be able to produce:



\* Windows installer

\* versioned release artifact

\* signed binary

\* checksums



Do not hard-code development-machine directories.



\---



\# 115. DESKTOP PRIORITY



Desktop packaging is a core product requirement, not an experimental afterthought.



However, packaging must not compromise the modularity of the original system.



The underlying Python workflow harness must remain transport-agnostic enough to also support:



\* FastAPI server deployment

\* hosted SaaS deployment

\* Celery workers

\* command-line usage

\* tests



The desktop application is one transport/deployment method for the same workflow engine.



\---



\# 116. FINAL DESKTOP EXPERIENCE



The final application should feel like ordinary professional Windows software.



The user should never need to know that the application internally consists of:



\* React

\* Tauri

\* Python

\* localhost services



From the user's perspective:



They install one application.



They launch one application.



They configure their AI providers.



They run their AI workflows.



Everything else should happen automatically.



