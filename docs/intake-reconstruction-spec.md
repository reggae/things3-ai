# Intake Reconstruction Spec

## Purpose

This document defines the current highest-priority workflow for this repo: reconstructing a fragmented Things database into a trusted GTD-style system.

The first concrete operational version of this plan is captured in `docs/reconstruction-workflow-v1.md`.

This is **not** the steady-state daily-planning workflow yet.

Right now, Things is acting as a mixed capture store spread across Inbox, Today, and existing projects. Many items are incomplete, ambiguous, duplicated, mis-grouped, or not actually valid next actions. The system must first reconstruct that mess into coherent projects, tasks, and next actions before a true daily-planning workflow can work.

## Core principles

### 1. Brain should not do memory work

The system must make it psychologically safe to capture anything into Things and trust that it will resurface for review.

The user should not need to remember:

- whether an item has been processed
- what project it belongs to
- what the next action is
- whether something important is waiting in an obscure list

### 2. Current location is not authoritative

Inbox, Today, and existing project placement are useful signals, but they do **not** prove that an item is correctly processed.

For the current reconstruction phase, all candidate items should be treated as potentially unprocessed.

### 3. A captured task is not automatically a next action

Many current Things items are not true atomic next actions. They may instead be:

- raw ideas
- vague reminders
- project seeds
- fragments of a larger project
- duplicates
- support material disguised as tasks

The intake system must determine what each item actually represents before it can be trusted.

### 4. The right review unit is often the project, not the task

The next action usually emerges from understanding the larger project or responsibility. Therefore the system should prefer project-level or cluster-level review over isolated task-by-task cleanup whenever possible.

### 5. Rebuild trust before optimizing daily use

Until the current messy state is reconstructed into a coherent system, steady-state CLI ergonomics like a polished `things today` command are secondary.

## Current stage

This project is in an **intake-first reconstruction stage**.

That means:

- we are gathering a large backlog of fragmented commitments
- we are grouping them into correct projects and outcomes
- we are identifying the true next action for each active project
- we are retiring or replacing misleading legacy fragments
- we are building a trusted system to use going forward

Only after this phase is complete should the system treat Today as a real curated daily commitment list.

## Target end state

When reconstruction is complete, the system should have:

- a coherent set of projects representing real outcomes or ongoing responsibilities
- meaningful tasks grouped under the correct projects
- one clearly defined next action for each active project
- safe handling for duplicates, obsolete fragments, and manual-review items
- a trustworthy capture-and-resurface loop
- a future path to a true Today list with 3-4 deliberate daily commitments

## Scope of the intake universe

The reconstruction workflow should pull from a combined intake universe rather than trusting any single list.

Primary intake sources:

- Inbox
- Today
- incomplete tasks currently living inside projects
- incomplete loose tasks currently living inside areas

Default exclusions unless explicitly requested later:

- completed items
- canceled items
- someday/maybe items if they are already explicitly parked and trusted

Important note:

- During this stage, Inbox and Today should be treated as conceptually similar capture sources.
- Today items should usually be shown first because they may represent slightly higher urgency or recency, but not because they are already valid daily commitments.

## Processing model

The reconstruction workflow should happen in six stages.

### Stage 1 — Gather

Build a combined intake inventory from all in-scope untrusted tasks.

Each item should preserve source metadata such as:

- original list location
- project/area relationships if any
- notes, tags, dates, checklist items
- identifiers needed for later reconciliation

### Stage 2 — Cluster

Group items into likely review units.

Preferred cluster types:

- existing project clusters
- candidate new project clusters
- standalone single-action items
- ambiguous / unclear bucket

Clustering may use:

- existing project membership
- title similarity
- note similarity
- repeated nouns / themes
- tags / areas / contextual hints
- LLM-assisted grouping if needed

### Stage 3 — Build review packets

For each cluster, generate a review packet that frames the problem at the right level.

Each packet should answer or propose:

- what outcome or project this cluster appears to represent
- which existing items belong together
- whether the cluster maps to an existing project or should become a new one
- what items appear to be duplicates or obsolete fragments
- what tasks should remain as actionable tasks
- what the next action should be
- what uncertainties require human review

### Stage 4 — Human + LLM review

Each packet should be reviewable by the user and optionally enriched by the LLM.

The LLM should receive the packet as a project-sized context, not just as isolated tasks.

The review should produce a proposed corrected structure containing:

- project title
- optional project outcome / definition
- cleaned task list
- designated next action
- optional supporting tasks
- context tags or relevant metadata
- items to retire, move, or leave for manual review

### Stage 5 — Safe reconcile/apply

After review, the system should safely reconcile Things toward the corrected structure.

Desired actions:

- create missing projects and tasks where supported
- update existing projects and tasks where supported
- move tasks into the correct destination where supported
- preserve audit artifacts of what changed
- route obsolete or replaced legacy items into a trash/manual-delete/manual-review flow

Important constraint:

- The current Things MCP surface does not support a guaranteed full destructive wipe-and-rebuild workflow.
- Therefore the practical implementation target is **safe reconcile-and-replace**, not blind destructive reset.

### Stage 6 — Trusted steady state

Once reconstruction is complete, the system can transition into a lighter operational mode:

- new captures are processed incrementally
- projects already have structure
- next actions remain trustworthy
- Today becomes a real curated daily selection step

## Canonical packet contract

A reconstruction packet should be the main review artifact for this phase.

Each packet should include at least:

- packet id
- cluster type (`existing_project`, `candidate_project`, `single_action`, `ambiguous`)
- source item list with stable UUIDs
- current placement summary
- proposed project identity
- proposed cleaned task list
- proposed next action
- proposed retire/archive/trash list
- open questions / low-confidence flags
- explicit dry-run/apply handoff

Artifacts should be available in both:

- JSON for machine processing
- Markdown for human review and editing

## Human interaction goals

The workflow should be optimized for:

- high trust
- low mental friction
- high throughput over a large messy backlog
- easy stopping and resuming
- clear auditability

The user should be able to review a packet and quickly decide:

- yes, this grouping is right
- no, split this cluster
- no, merge this with another cluster
- yes, this should become project X
- no, this is a standalone action
- this item is obsolete / duplicate / support material

## CLI implications

The CLI should be designed around the reconstruction phase before daily-planning ergonomics.

Near-term command family should likely focus on:

- `things intake` or similar combined intake inventory command
- packet generation commands
- packet review / ingest commands
- safe dry-run/apply reconcile commands

Command ideas worth exploring:

- `things intake`
- `things intake-clusters`
- `things intake-packets`
- `things intake-review --packet ...`
- `things intake-apply --packet ...`

The exact names are less important than the interaction model.

The CLI should **not** assume that `things today` is yet a meaningful execution view. A true Today workflow belongs after reconstruction is complete.

## Non-goals for this stage

The reconstruction phase is not primarily trying to optimize:

- polished day planning
- final steady-state command naming
- perfect interactive selector ergonomics
- aggressive autonomous reprioritization across an already-trusted system

Those can come later once the data itself is trustworthy.

## Success criteria

This phase is successful when:

- the user trusts that all captures will resurface for review
- the majority of fragmented current tasks have been grouped into coherent projects or resolved standalone actions
- active projects have explicit next actions
- obsolete legacy fragments are safely retired or routed to manual review
- Today can finally be reset and rebuilt as a true daily commitment list

## Open design questions

- How broad should the default intake universe be on the first pass?
- Should clustering begin with deterministic rules, LLM assistance, or a hybrid?
- What packet size is easiest to review without becoming overwhelming?
- Should human review happen mainly in Markdown files, terminal flows, or both?
- What is the safest practical retirement path for replaced legacy items given current MCP limitations?
- How do we represent ongoing responsibilities versus finite projects during reconstruction?

## Immediate next build target

Before more CLI expansion, define the first concrete reconstruction workflow around this spec:

1. combined intake inventory
2. initial clustering model
3. packet artifact format
4. human review loop
5. safe reconcile/apply handoff