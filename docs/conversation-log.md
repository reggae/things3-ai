## Conversation Log

### 2026-03-09 — Kickoff

#### Reference

- `ref: kickoff-2026-03-09-01`

#### User goals summary

- Build a personal Things 3 + AI system using Python notebooks because notebooks are easier to debug and understand.
- Prioritize a roadmap first so future features have structure.
- Keep a record of conversations, with summaries and references.
- Keep the system organized around atomic skills.
- Start with essential Things capabilities:
  - get every task with full details
  - get every project
  - get every tag, area, etc.
  - create a task
- Later add an LLM payload/action layer using the user’s existing API credential pattern from another repo.
- For LLM task decisions, include full task details and project context, but not area in the first pass.
- Support general prioritization and enhancement, including full “head dump” processing.
- Offload personal task-management memory entirely from the user’s head.
- Revisit GTD ideas, replacing rigid heuristics with richer capture and LLM processing.
- Allow aggressive reorganization inside Things, especially because Today and Inbox are currently unreliable.
- Build a flat text or Markdown question/answer interface where the user edits answers, then the system calls the LLM.
- Save Things tasks into JSON and/or Markdown with every available property.
- Add an atomic “process inbox” workflow that asks questions, handles batches, and leaves unfinished items alone.
- Support a daily planning command like “what’s our plan today?”

#### Decisions captured so far

- This is a personal project, not intended for company sharing.
- Notebook-first workflow is a feature, not a temporary workaround.
- Roadmap and conversation logging are first-class project requirements.
- Atomic skills are the core design unit.

#### Initial open questions

- Which existing repo contains the LLM credential/payload pattern to mirror?
- Do you want read access to favor AppleScript, URL scheme, or a hybrid adapter?
- What exact task fields must be included in the canonical export schema?
- Where should snapshots and prompt artifacts live on disk?
- What should the first flat-file Q&A format look like: Markdown checklist, YAML frontmatter, or plain text?

### 2026-03-09 — Architecture decisions

#### Reference

- `ref: decisions-2026-03-09-02`

#### Follow-up answers captured

- Existing LLM credential/payload repo: `/Users/gregadamson/icloud/orca`
- Keep local tokens in this repo’s `.env` where possible
- Use Things MCP as the primary Things integration path
- Local setup note is `things-mcp-setup.md` in the repo root
- Notebooks are optional; if used, prefer classic `.ipynb` in VS Code with the existing conda environment
- Canonical export format should be normalized schema plus raw source payload
- Include projects as collections of tasks and include areas
- First flat-file Q&A format can be Markdown; plain text also works as an interim artifact
- Snapshots should stay local under `data/` for now
- Selective committing of example artifacts is acceptable later
- History/logging is desirable

#### Implementation findings captured

- The local Things MCP setup is already marked connected via `uvx things-mcp`
- The upstream Things MCP tool surface is broader than the local setup note and includes projects, areas, tags, search, recent items, and add/update operations
- The external `orca` repo already contains a reusable `.env`-based LLM client wrapper and Markdown logging pattern worth mirroring instead of rebuilding from scratch