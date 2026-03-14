## Roadmap

### Vision

Create a personal task intelligence system on top of Things 3 so your brain does not need to remember personal task-management state.

### Product principles

- Personal-first, not company-facing
- Natural, task-first UX: the user-facing noun is `task`
- Notebook-friendly for debugging and transparency, but not notebook-dependent
- Atomic skills before compound workflows
- Durable conversation and decision history
- Flat-file friendly inputs/outputs for human editing and LLM processing
- Shared editable working files instead of split human/AI artifacts
- Prompt-driven AI behavior that stays user-tunable
- Terminal output should stay compact, scannable, and never wrap
- Safe to reorganize Things aggressively when the system is ready

### Execution routine

- The roadmap is the source-of-truth task list; the live task tracker should mirror it.
- I proceed autonomously by default and only stop to ask for help when there are materially competing implementation options or a risky action needs approval.
- A task is not considered complete until it passes a **Task Completion Audit**.

### Task Completion Audit

For any task that changes behavior, code, or user-facing workflow, completion means:

1. The intended code and/or docs changes are landed in the repo.
2. Tests are added or updated when behavior changed, or the task is explicitly marked **test N/A** with a reason.
3. A targeted validation command is run and passes.
4. The roadmap and task list are updated to reflect the new state.
5. The next immediate step or blocker is recorded.

For this repo, the default audit expectation is:

- code change -> add/update tests
- run the smallest relevant `unittest` target or validation command
- if live Things export behavior changed, run a privacy-safe live export verification too

### Current checkpoint

- **Phase 0**: complete
- **Phase 1**: complete
- **Phase 2**: complete
- **Phase 3**: complete
- **Phase 4**: not started
- **Phase 5**: not started
- **Phase 6**: not started

Implemented so far:

- stdio MCP adapter in `src/`
- JSON/Markdown snapshot export under `data/`
- normalization for todos, projects, areas, and tags
- preserved and reconciled area/project/task hierarchy in normalized snapshots
- read-side query/control helpers using canonical lookup and child-path traversal
- safe-by-default create-task path using canonical area/project/heading selectors
- create-task path validated with targeted tests, full suite, and privacy-safe live dry-run checks
- safe update-task/project path using canonical selectors
- update-task/project path validated with targeted tests, full suite, and privacy-safe live dry-run checks
- repo-local `.env` / model config scaffold for the LLM bridge
- `task-context` payload builder plus local JSON/Markdown artifact writers under `data/llm`
- markdown prompt/response/action debug logging for Phase 2 inspection
- Phase 2 scaffold validated with targeted tests and the full suite
- privacy-safe live `task-context` inspection passed against real Things data, including project-context capture, area toggle behavior, and local artifact/log writing
- minimal `task-llm` wrapper with prompt construction, provider inference, stdlib HTTP request shaping, preview-by-default behavior, and explicit `--execute` gating for real calls
- `task-llm` validated with targeted tests, full-suite regression, and a privacy-safe live preview-only smoke check against real Things data
- minimal structured response contract for task decisions emitted by `task-llm`
- shared task request bundle that can be consumed either by an external LLM or by Augment directly
- JSON/Markdown task-request artifacts under `data/llm/task-requests`
- request-bundle path validated with targeted tests, full-suite regression, and a privacy-safe live preview-only smoke check against real Things data
- suggestion-only task proposal interpreter that maps shared request bundles + structured decisions onto existing safe request builders
- non-mutating `task-proposals` CLI with JSON/Markdown proposal artifacts under `data/llm/task-proposals`
- proposal path validated with targeted tests, full-suite regression, and a privacy-safe live smoke check against real Things data
- explicit review handoff from proposal previews into repo-local dry-run/apply command previews
- approval-handoff path validated with targeted tests and full-suite regression
- Today-first inbox question-set generation with JSON/Markdown artifacts for offline clarification
- conservative inbox-answer ingestion that preserves unanswered items, keeps delete intent as manual delete-only review, and downgrades unresolved area/project moves into explicit partial follow-up instead of hard failure
- durable archive bundles under `data/archives/YYYY-MM-DD/` with JSON + Markdown output
- analysis-only restore preflight under `data/restore-plans/YYYY-MM-DD/`, including automatic pre-restore safety backup archives and restore feasibility/structure-gap reporting
- archive/restore safety milestone validated with targeted tests, full-suite regression, and a privacy-safe live smoke check against real Things data

Current next focus:

- lock the task-centric workflow docs and prompt so implementation can move quickly
- build the new `things task ...` slice for real inbox processing before daily planning
- immediate implementation order:
  1. durable local item template + stable keys like `T-184`
  2. `things task list`, `things task next`, and `things task show`
  3. `things task review`
  4. editable AI polish prompt at `prompts/task-ai-polish.md`
  5. `things task open` using `vim <path>` and post-exit polish prompt
  6. `things task accept` with a UX-facing summary and safe apply path
- after that, resume daily planning on top of the new task-processing flow

### Known platform facts

- Preferred integration is `hald/things-mcp`, already configured locally via `uvx things-mcp`
- Things MCP exposes read operations for todos, projects, areas, tags, list views, search, and recent items
- Things MCP exposes write/update operations for todos and projects
- Things MCP supports stdio by default and optional HTTP transport if we want a Python-friendly bridge later
- Things URLs still need to be enabled in Things because the MCP relies on that capability underneath

### Atomic skills

1. Export every task from Things with all available details
2. Export every project from Things
3. Export tags, areas, and related reference objects
4. Create a task
5. Update an existing task or project
6. Serialize Things data to JSON and Markdown snapshots
7. Build flat-file question sets for enrichment and clarification
8. Send enriched payloads to an LLM and capture decisions

### Compound workflows

- Process captures into GTD-shaped tasks and projects
- Process inbox
- Daily plan: “what’s our plan today?”
- Brain dump to structured tasks
- Priority shaping and task enhancement
- Things reorganization and cleanup

### Proposed phases

#### Phase 0 — Foundation

Status: **complete**

- [x] Create roadmap and conversation log
- [x] Choose repository structure
- [x] Confirm integration approach: Things MCP as the primary interface
- [x] Define canonical export schema for Things objects
- [x] Reuse the local `.env` pattern and the external `orca` LLM client/logging approach

#### Phase 1 — Core Things adapter

Status: **complete**

- [x] Reusable adapter in `src/` to call Things MCP operations
- [x] Script/CLI demo for debugging in VS Code
- [x] Read/export tasks, projects, tags, and areas
- [x] Save full snapshots to `data/` as JSON and Markdown
- [x] Add small validation checks/examples
- [x] Preserve and reconcile area/project/task hierarchy in normalized snapshots
- [x] Add safe read-side query/control helpers using canonical IDs and path-like selectors
- [x] Create a task
- [x] Update an existing task or project
- [x] Add safe write-side helpers on top of canonical selectors

#### Phase 2 — LLM bridge

Status: **complete**

- [x] Reuse or mirror the existing `orca` credential/payload pattern
- [x] Build a single “task context payload” including task + project context
- [x] Exclude area from first-pass decision payload unless needed later
- [x] Record prompts, responses, and actions for debugging
- [x] Build the smallest safe real LLM call wrapper
- [x] Define a minimal structured response contract for task decisions
- [x] Emit a shared request bundle for external LLM or Augment consumption
- [x] Write task-request JSON/Markdown artifacts for audit and reuse
- [x] Interpret shared task request bundles into suggestion-only action proposals
- [x] Define the approval handoff from reviewed proposals into explicit dry-run/apply commands

#### Phase 3 — Inbox processor

Status: **complete**

- [x] Present questions in a flat text or Markdown file
- [x] Let you answer in batch asynchronously
- [x] Process completed answers and leave remaining items untouched
- [x] Stack Today items above the rest of Inbox during processing

#### Phase 4 — Task-centric capture processing

Status: **not started**

- [ ] Create durable local item files with stable keys like `T-184`
- [ ] Render compact no-wrap task rows with numbered slots and ellipsis truncation
- [ ] Implement `things task list`, `things task next`, and `things task show`
- [ ] Implement `things task review` as the structured task-vs-project interview
- [ ] Create the editable prompt at `prompts/task-ai-polish.md`
- [ ] Implement `things task open` via `vim <path>` with optional post-edit AI polish
- [ ] Implement `things task accept` with UX-facing summaries and safe apply behavior
- [ ] Ensure every accepted task lands in a project home, defaulting to `<Area> / Single Actions`
- [ ] Keep v1 conservative: no auto-merge of multiple captures into one local item

#### Phase 5 — Daily planning

Status: **not started**

- [ ] Generate a practical day plan from Today, Inbox, and priorities
- [ ] Limit work to a realistic daily “win” amount
- [ ] Feed germinating ideas back into capture/enrichment flow

#### Phase 6 — Autonomous reorganization

Status: **not started**

- [ ] Clean up Today and Inbox misuse
- [ ] Reprioritize and reschedule tasks in Things
- [ ] Maintain audit logs so changes are explainable and reversible

### Initial architecture direction

- `src/` for reusable atomic skills and adapter code
- `notebooks/` for optional debugging-first workflows in classic `.ipynb`
- `data/` for exported snapshots and prompt/response artifacts
- `docs/` for roadmap, decisions, and conversation summaries

### Immediate next build target

Build the task-centric `things task` workflow, starting with durable item files plus `task next` and `task review`, then layer `open`, `accept`, and daily planning on top.