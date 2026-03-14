## Things 3 + AI

Local-first automation project for working with Things 3 through Things MCP, with a Python CLI, durable archive artifacts, and LLM-assisted review workflows.

### Current priorities

- Make the current CLI feel fast, local, and safe for everyday use
- Build a task-centric `things task ...` workflow for fast inbox processing
- Keep a durable record of roadmap decisions and implementation status
- Keep the UX natural: one shared working file, no packet jargon, no terminal wrapping
- Build toward browse-first daily planning and next-action workflows
- Preserve full-fidelity exports and durable archive bundles before more aggressive automation
- Keep AI-assisted workflows reviewable with local JSON/Markdown artifacts

### Documents

- `docs/roadmap.md` — source-of-truth phased roadmap and current checkpoint
- `docs/conversation-log.md` — summarized conversation record and references
- `docs/things-cli-spec.md` — working CLI spec, wishlist, and UX backlog
- `docs/task-processing-workflow-v1.md` — locked v1 task-processing interaction model
- `prompts/task-ai-polish.md` — editable AI polish prompt for GTD-shaped item refinement

### Current CLI entrypoint

Use the repo-local wrapper:

- `./things --help`
- `./things archive`
- `./things restore --archive 20260309`

The wrapper calls the existing `things_ai` CLI from this repo without requiring packaging first.

### Local development

- Run the CLI through the repo wrapper: `./things --help`
- Run the test suite with: `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'`
- Generated local artifacts under `data/` are intentionally ignored by Git

### Implemented capabilities

- Export normalized Things snapshots to JSON and Markdown under `data/`
- Create durable archive bundles with `./things archive`
- Run restore preflight analysis with automatic pre-restore safety backup via `./things restore --archive ...`
- Safely prepare or apply task creation with canonical area/project/heading selectors
- Safely prepare or apply task and project updates with canonical selectors
- Generate Today-first Inbox clarification question sets and review answered Markdown files
- Build task-context payloads, LLM request bundles, and suggestion-only action proposals
- Write local JSON/Markdown/debug artifacts for inspection and audit

### Archive / restore status

- `archive` is implemented as a real durable export capability
- `restore` currently means restore **preflight + reconcile planning + safety backup**
- `restore --apply` currently records execution metadata only; destructive restore automation is not implemented yet
- Full destructive wipe-and-rebuild restore is **not** implemented yet because the current Things MCP surface does not expose the full delete/archive/structure-creation capabilities required for that safely

### Near-term implementation target

1. Build the durable local item format for task processing
2. Implement `things task list`, `things task next`, and `things task show`
3. Implement `things task review`, the editable AI polish prompt, and `things task open`
4. Implement `things task accept` so reviewed items can be applied cleanly back into Things
5. Resume daily planning on top of the new task-processing flow