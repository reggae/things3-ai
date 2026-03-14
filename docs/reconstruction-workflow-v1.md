# Reconstruction Workflow v1

## Purpose

This document turns the broader intake-reconstruction spec into a concrete first workflow.

Version 1 is intentionally pragmatic. It should help process the current messy backlog with a terminal-first review flow, persistent local artifacts, LLM-assisted interpretation, and safe retirement of legacy scraps.

## Status note

- This document captures the earlier packet/session-centric reconstruction design.
- The current user-facing v1 direction is the task-centric workflow described in `docs/task-processing-workflow-v1.md`.
- Packet/session artifacts still matter as implementation history and backend scaffolding, but they should no longer define the primary CLI UX.

## Decisions locked for v1

### 1. Default intake universe

Pass 1 scope is:

- all incomplete items currently in **Today**

This is a bounded first reconstruction pass, not the forever definition of intake.

Steady-state capture policy after reconstruction:

- new captures go to **Inbox** only
- **Today** becomes reserved for deliberate daily commitments

### 2. Initial clustering strategy

Clustering means grouping source items into the best review unit, usually a project or a likely project.

V1 clustering is **conservative hybrid**:

- items already in the same existing project are grouped together
- loose/unprojected items start as single-item packets
- the terminal review can merge items into a project during processing
- the LLM may suggest likely grouping or splitting when the situation is ambiguous

This avoids over-trusting early clustering while still allowing project-level reconstruction.

### 3. Packet format v1

Each packet should preserve both the original source truth and the proposed corrected interpretation.

Required sections:

- packet metadata
- source items
- proposed interpretation
- review actions/dispositions
- review notes / captured ideas
- reconcile preview

### 4. Human review surface

V1 review is **terminal-first, artifact-backed**.

That means:

- the terminal drives the conversation
- answers are written into local JSON/Markdown artifacts as they happen
- the terminal may ask immediate follow-up questions when needed
- the packet artifact is then used as LLM input and audit history

### 5. Safe retirement path

Legacy source items are treated like scraps used to build the correct project structure.

V1 retirement path is:

- retire/move old source items into a dedicated holding destination such as `Retired` or `Trash`
- keep local packet and archive artifacts as the durable safety net
- avoid relying on destructive deletion as the default path

## Packet Format v1

### A. Packet metadata

- packet id
- packet status (`new`, `reviewed`, `proposed`, `staged`, `applied`, `skipped`)
- review unit type (`existing_project`, `candidate_project`, `single_action`, `trash`, `ambiguous`)
- confidence score or confidence label
- created at / updated at timestamps

### B. Source truth

- source item UUIDs
- original titles
- original notes
- original list location (`Today` for pass 1)
- original project / area if any
- existing tags
- existing dates (`when`, `deadline`)
- checklist items if present

### C. Proposed interpretation

- proposed project title
- project outcome / what complete means
- candidate next action
- supporting tasks / subtasks
- required systems / contexts / environments
- due / when recommendation
- reasoning summary
- open questions / low-confidence flags

### D. Review dispositions

Each packet must support explicit user decisions such as:

- keep active
- mark complete now
- retire/trash
- convert to project
- keep as single next action
- split into multiple tasks
- merge into an existing or newly named project

### E. Review capture

Freeform capture fields should exist for:

- extra ideas
- notes to self
- project clarification
- constraints
- follow-up information provided in terminal review

### F. Reconcile preview

- projects to create/update
- tasks to create/update/move
- next action designation
- legacy items to retire
- manual-review blockers

## Required interpretation fields

The following ideas must be captured during packet review or proposal building:

- **task**: what actionable work exists here
- **project**: what larger outcome or ongoing responsibility this belongs to
- **what complete means**: especially at the project level
- **systems needed**: what environment/context/tool is required (`phone`, `browser`, `vscode`, `orca`, `school`, etc.)
- **when it is due**: deadline or scheduling signal if known
- **complete now**: if the user decides it is already done or can be done immediately
- **trash now**: if the user decides it should be retired rather than reconstructed
- **idea capture**: new thoughts surfaced during review should be captured, not lost

## Terminal review flow v1

The terminal should be the primary working surface.

### Step 0 — Start a reconstruction session

The CLI loads the v1 intake universe:

- all incomplete Today items

It generates packets and stores them locally before asking the first review question.

### Step 1 — Show the next packet

The terminal shows a compact summary of:

- source title(s)
- original notes
- current location / project
- any obvious dates or tags

### Step 2 — Ask the first core questions

The terminal should ask only a few questions at a time, for example:

1. Is this best understood as:
   - a project
   - a single next action
   - complete already
   - trash/retire
   - unclear
2. If it is a project, what is the outcome / what does done mean?
3. What is the very next action?

### Step 3 — Parse and ask follow-up questions immediately if needed

If the answer is incomplete or ambiguous, the terminal should ask follow-ups right away, such as:

- what other tasks belong under this project?
- should this merge with an existing project?
- what system/context is required?
- is there a due date?
- should the original source item be retired after reconstruction?

### Step 4 — Persist the review artifact immediately

The terminal writes the current answers to local packet artifacts in JSON and Markdown.

This should happen even before LLM enrichment so no reasoning is lost.

### Step 5 — Call the LLM with packet + answers

The LLM receives:

- the source packet
- the user’s terminal answers
- any immediate follow-up answers

The LLM returns a proposed cleaned structure:

- project interpretation
- candidate next action
- supporting tasks
- contexts/systems
- retire/keep recommendations

### Step 6 — Show the proposal in terminal

The terminal shows a concise reviewable summary, for example:

- project: X
- outcome: Y
- next action: Z
- support tasks: A, B, C
- retire source item: yes/no

### Step 7 — Accept, edit, skip, or escalate

The user should be able to say:

- accept
- edit
- skip for later
- mark complete
- mark trash
- ask for another proposal

### Step 8 — Stage reconcile actions

Accepted packets generate explicit dry-run reconcile actions.

### Step 9 — Apply in batches

After enough packets are reviewed, the CLI can apply staged changes and then retire old source scraps into the holding destination.

## Exact terminal prompt script v1

This section defines the intended interactive script for `things intake next`.

The goal is to keep the interaction lightweight and conversational while still producing structured artifacts.

### Prompt bundle 1 — classify + orient

The CLI shows one source packet and then asks:

1. **What kind of thing is this?**
   - `1` project / outcome
   - `2` single next action
   - `3` complete already
   - `4` trash / retire
   - `5` unclear
2. **If this is a project or outcome, what does done mean?**
3. **What is the next physical action?**

Behavior notes:

- The user does not need to answer every prompt if a prompt is not relevant.
- Empty answers are allowed and should trigger follow-up only when needed.
- The CLI should accept short freeform answers, not just rigid enumerations.

### Prompt bundle 2 — enrich only as needed

Based on the first answers, the CLI may ask a second small bundle:

- what other tasks belong under this?
- should this merge into an existing project?
- what systems / contexts are needed?
- is there a due date or timing signal?
- should the original source item be retired after reconstruction?
- did any new ideas come up that should be captured?

The CLI should ask only the follow-ups relevant to the classification.

### Prompt bundle 3 — proposal approval

After the packet is enriched and the LLM returns a reconstruction proposal, the terminal shows a concise summary and asks:

- `a` accept
- `e` edit
- `s` skip for later
- `c` mark complete instead
- `t` mark trash instead
- `r` request a revised proposal

### Branching rules

#### If classified as `project / outcome`

The CLI should prioritize asking:

- what does done mean?
- what is the next physical action?
- what other tasks belong under this?
- should it merge into an existing project?
- what systems / contexts are required?
- is there a due date?

#### If classified as `single next action`

The CLI should prioritize asking:

- what is the next physical action in cleaner wording?
- what system / context is required?
- is there a due date or timing signal?
- should the original source scrap be retired?

#### If classified as `complete already`

The CLI should ask:

- should the source item be marked complete and retired?
- is there any follow-on task or idea to capture first?

#### If classified as `trash / retire`

The CLI should ask:

- retire as `trash`, `someday`, or `manual-review`?
- any note worth preserving before retirement?

#### If classified as `unclear`

The CLI should ask clarifying prompts such as:

- what do you think this is really about?
- is this part of a bigger project?
- is there an obvious next action?
- should we keep this for later review instead of deciding now?

### Parsing and follow-up behavior

V1 parsing should be forgiving and pragmatic:

- accept brief natural-language replies
- accept comma-separated supporting tasks
- accept simple timing phrases like `this week`, `tomorrow`, or `before April 15`
- preserve raw answers even if normalization is incomplete

If parsing fails or the answer is ambiguous, the CLI should:

- save the raw answer
- ask one clarifying question
- avoid forcing the user through a large correction flow

### Toy conversation reference

Minimal example:

1. CLI shows source item
2. User says `project`
3. User gives outcome
4. User gives next action
5. CLI asks for supporting tasks and context
6. CLI persists answers
7. CLI calls the LLM
8. CLI shows proposal
9. User accepts, edits, skips, marks complete, or marks trash

## Terminal behavior principles

The terminal experience should feel:

- conversational
- fast
- resumable
- safe
- audit-backed

Important behavioral rule:

- ask a few smart questions at a time, not a giant form all at once

Additional behavioral rules:

- persist progress after every answer bundle
- preserve raw user phrasing alongside normalized fields
- prefer one clarifying question over a full re-prompt
- keep the user focused on one packet at a time

## Artifact model

For every packet, v1 should write:

- packet JSON
- packet Markdown
- review transcript or structured answer record
- proposal JSON/Markdown after LLM enrichment
- staged reconcile preview if accepted

Suggested directories:

- `data/reconstruction/packets/`
- `data/reconstruction/reviews/`
- `data/reconstruction/proposals/`
- `data/reconstruction/staged/`

## Packet JSON schema v1

This is the target persisted structure for one intake packet. Field names may evolve slightly during implementation, but the conceptual contract should stay stable.

```json
{
  "schema_version": "things-ai.intake-packet.v1",
  "packet_id": "packet-20260311-001",
  "status": "new",
  "review_unit_type": "single_action",
  "source_items": [],
  "review": {},
  "proposal": {},
  "staged_actions": {}
}
```

### Top-level fields

- `schema_version`: packet schema version
- `packet_id`: stable packet identifier
- `status`: packet lifecycle state
- `review_unit_type`: current interpretation class
- `source_items`: original Things source data used for reconstruction
- `review`: user answers and normalized interpretations gathered in terminal
- `proposal`: LLM-generated cleaned structure and confidence notes
- `staged_actions`: dry-run reconcile actions ready for later apply

### `source_items[]`

Each source item should preserve:

- `uuid`
- `title`
- `notes`
- `source_list`
- `project`
- `area`
- `tags`
- `when`
- `deadline`
- `checklist_items`
- `raw_snapshot_ref` if needed for traceability

### `review`

The `review` section should capture both raw answers and normalized fields.

Recommended subfields:

- `classification`
- `project_title`
- `project_outcome`
- `next_action`
- `supporting_tasks`
- `systems_needed`
- `timing_signal`
- `complete_now`
- `trash_now`
- `retire_source`
- `captured_ideas`
- `open_questions`
- `raw_answers`
- `transcript`

### `proposal`

The proposal section should contain:

- `status`
- `reasoning_summary`
- `confidence`
- `proposed_project`
- `proposed_outcome`
- `proposed_next_action`
- `proposed_supporting_tasks`
- `proposed_contexts`
- `proposed_due`
- `retire_recommendation`
- `manual_review_flags`
- `llm_request_ref`
- `llm_response_ref`

### `staged_actions`

This section should contain explicit dry-run-ready actions such as:

- `create_projects`
- `update_projects`
- `create_tasks`
- `update_tasks`
- `move_tasks`
- `retire_legacy_items`
- `manual_steps`

### Packet lifecycle states

Recommended packet state progression:

- `new`
- `reviewing`
- `reviewed`
- `proposed`
- `accepted`
- `staged`
- `applied`
- `skipped`
- `retired`

## `things intake next` command behavior v1

This section defines the first concrete interactive command built from the workflow.

### Purpose

`things intake next` should load the next unresolved packet, guide the user through a short terminal review, persist the results, optionally call the LLM, and stage a proposal for acceptance.

### Preconditions

- a reconstruction session exists, or the command can create one automatically
- packet artifacts for pass 1 have been generated from incomplete Today items
- unresolved packets remain in the session queue

### High-level lifecycle

1. Load the next unresolved packet
2. Show packet summary in terminal
3. Run prompt bundle 1
4. Persist immediate answers
5. Run relevant follow-up prompts
6. Persist follow-up answers
7. Build an LLM-ready review bundle
8. Call the LLM or prepare the request if running in preview mode
9. Show the proposal summary
10. Accept/edit/skip/complete/trash/revise
11. Write updated packet + proposal artifacts
12. If accepted, write staged reconcile actions

### Preview-by-default behavior

V1 should remain safe by default:

- `things intake next` should not mutate Things immediately
- it should stage changes and retirement actions first
- apply should happen later through a separate explicit command

### Resume behavior

If the command is interrupted, it should be able to resume from the latest saved packet state without losing:

- source packet data
- raw terminal answers
- normalized review fields
- LLM proposal artifacts
- staged reconcile previews

### Editing behavior

If the user chooses `edit`, the CLI should allow targeted edits to the proposal, for example:

- project title
- outcome text
- next action text
- supporting task list
- systems/context list
- due/timing signal
- retire/keep choice

### Skip behavior

If the user chooses `skip`, the packet should:

- record the skip reason if provided
- remain unresolved
- be available again later in the queue

### Complete / trash behavior

If the user chooses `mark complete` or `mark trash`, the packet should still write:

- the review artifact
- the chosen disposition
- the staged retirement/complete action

This preserves auditability even for fast decisions.

## Candidate CLI primitives for v1

These names are provisional, but the workflow should support capabilities like:

- `things intake start`
- `things intake next`
- `things intake review --packet ...`
- `things intake propose --packet ...`
- `things intake stage --packet ...`
- `things intake apply`
- `things intake resume`

Possible simplification:

- `things intake next` could both show the packet and drive the interactive prompt flow in one command.

## Success criteria for v1

Workflow v1 is successful if it allows the user to:

- reliably work through all current Today items
- convert vague scraps into meaningful project structures or real next actions
- capture new ideas during review without losing them
- retire source scraps safely after reconstruction
- build enough trusted structure that Inbox-only capture can become the new steady-state rule

## Immediate implementation priority

The first implementation slice should be:

1. generate packet artifacts from incomplete Today items
2. drive a terminal-first review loop for one packet at a time
3. persist answers before and after follow-up questions
4. emit an LLM-ready packet bundle
5. stage safe reconcile previews and retirement actions