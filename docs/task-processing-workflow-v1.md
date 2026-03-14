# Task Processing Workflow v1

## Purpose

This document locks the user-facing v1 workflow for processing captures into GTD-shaped tasks and projects.

The goal is a natural terminal-first experience that is fast enough for long inbox-processing sessions without exposing packet/session implementation details.

## Locked user-facing model

- primary command family: `things task ...`
- main doorway: `things task next`
- user-facing noun: `task`
- each source capture becomes one local item in v1
- multiple captures may converge on the same project later, but v1 must not auto-merge them

## Identifiers and rendering

Each local item should have:

- a short-lived screen slot like `1`, `2`, `3`
- a stable local key like `T-184`

Preferred row format:

- `1 | 📎 Work / Discount Revenue | T-184 | Clarify how discount affects revenue documentation`

Rendering rules:

- terminal output must never wrap
- truncate with ellipsis instead
- preserve slot number and key first, then enough of home, then as much title as fits

## Kind and state

Kind and state are separate.

`kind` values:

- `unknown`
- `task`
- `project`

`state` values:

- `new`
- `reviewing`
- `proposed`
- `active`
- `done`
- `retired`

## Shared item document

Each local item should be one shared working document, not separate human/AI/proposal/final files.

Recommended metadata:

- `key`
- `state`
- `kind`
- `area`
- `project`
- `tags`
- `source_uuid`

Recommended sections:

- `# Title`
- `## Outcome`
- `## Next Action`
- `## Steps`
- `## Notes`
- `## Original Capture`

Rules:

- the current best version belongs near the top
- `Original Capture` must be preserved exactly
- the document should stay readable in Vim
- `project` may be blank while unresolved, but every accepted task needs a project home

## Project-home rule

Every active task should have a project home.

If no better project is known, use:

- `<Area> / Single Actions`

Examples:

- `📎 Work / Single Actions`
- `🏠 Home / Single Actions`

## Core v1 commands

- `things task list`
- `things task next`
- `things task show <n|key>`
- `things task review <n|key>`
- `things task open <n|key>`
- `things task accept <n|key>`

## `things task next`

`task next` should:

- choose the next reviewable item
- show it immediately
- display a compact action menu

Preferred action menu:

- `r` review
- `o` open
- `a` accept _(only if proposed)_
- `d` done
- `x` retire
- `q` quit

## `things task review`

`review` is the structured CLI interview.

It should begin with:

1. `Does this feel more like a task or a project? [task/project/unsure]`
2. `What is this really about?`
3. `What area does it belong to, if known?`
4. `What is the next physical action?`

If shaping toward a task, ask follow-ups such as:

- can this actually be done in one action?
- is there hidden waiting, dependency, location shift, or multi-step structure?
- if it stays a task, what is the clearest next-action wording?

If shaping toward a project, ask follow-ups such as:

- what does done look like?
- what are likely later steps?
- are any of those still uncertain?
- are there timing, waiting, or dependency constraints?

After review:

- update the item document
- optionally run AI polish
- set state to `proposed`
- show the polished summary

## `things task open`

`open` should:

- resolve the item file path
- literally run `vim <path>`
- wait for Vim to exit
- ask `Run AI polish on this edited item? [Y/n]`

If the user answers yes:

- re-read the edited file
- run the AI polish prompt
- write back to the same file
- show a concise polished summary
- set state to `proposed`

## `things task accept`

`accept` means final approval/apply.

It should:

- apply the current polished version to Things
- update local state to `active`
- keep the summary UX-facing rather than implementation-facing

The accept summary should show only user-visible outcomes, such as:

- final kind
- target home
- project title if applicable
- next action
- whether additional steps are being kept in notes for now

It should not expose storage mechanics like source-object reuse.

## AI polish prompt

The editable prompt file should live at:

- `prompts/task-ai-polish.md`

Prompt stance:

- strong on GTD structure
- strong on detecting fake single tasks that are really projects
- willing to preserve uncertainty and open questions
- gentle on rewriting voice
- must preserve `Original Capture` exactly

Key policy sentence:

- `Clarify aggressively, but do not overwrite intent.`

## V1 boundaries

- no auto-merge of multiple captures into one local item
- no packet/session jargon in the primary UX
- no separate human-notes vs AI-notes document split
- no requirement that `accept` expose how the underlying Things objects are reused

## Recommended implementation order

1. durable local item template and stable key generation
2. `things task list`, `things task next`, and `things task show`
3. `things task review`
4. editable AI polish prompt
5. `things task open`
6. `things task accept`

Once that slice is solid, daily planning can build on top of it.