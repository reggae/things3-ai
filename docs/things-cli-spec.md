# Things CLI Spec and Wishlist

## Purpose

This document is the working spec and backlog for a powerful personal `things` CLI.

Use it for three purposes:

1. Record what the CLI can do right now.
2. Capture desired commands, workflows, and ergonomics.
3. Let Gregory add stream-of-consciousness ideas that later get turned into concrete CLI features.

## Naming

- Desired executable name: `things`
- Current internal Python package name can remain `things_ai`
- Goal: user-facing CLI should feel short, obvious, and local-first

## Current directional lock

The main user-facing workflow is now task-centric.

- user-facing noun: `task`
- preferred command family: `things task ...`
- main doorway: `things task next`
- stable local keys should look like `T-184`
- numbered screen slots (`1`, `2`, `3`, ...) are short-lived convenience selectors
- the primary editable artifact is one shared working file per capture

For the full interaction model, see `docs/task-processing-workflow-v1.md`.

## Locked v1 task workflow

### Core commands

- `things task list`
- `things task next`
- `things task show <n|key>`
- `things task review <n|key>`
- `things task open <n|key>`
- `things task accept <n|key>`

### Deliberately de-emphasized in v1

- `accept --as project|single`
- `split`
- `materialize`
- `skip` as a core command

Possible later addition:

- `snooze`

### Rendering rules

- terminal output should never wrap
- rows should truncate with ellipsis instead
- preferred row format:
  - `1 | 📎 Work / Single Actions | T-184 | Clarify how discount affects revenue documentation`

### Kind and state are separate

- `kind`: `unknown`, `task`, `project`
- `state`: `new`, `reviewing`, `proposed`, `active`, `done`, `retired`

### Shared item document

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

### Core behavior

- `review` is the structured interview that decides whether the capture is shaping into a task or a project
- `open` should literally run `vim <path>`, wait for Vim to exit, then ask whether to run AI polish
- `accept` means final approval/apply, not classification
- accept summaries should stay UX-facing and should not expose storage mechanics
- every active task should have a project home; fallback is `<Area> / Single Actions`
- do not auto-merge multiple captures into one local item in v1

### AI polish

- the first editable prompt lives at `prompts/task-ai-polish.md`
- the AI should be strong on GTD structure and gentle on rewriting voice
- key policy sentence: `Clarify aggressively, but do not overwrite intent.`

## Current archive/restore status

- `archive` is now a real first-class CLI capability.
- `restore` is now implemented honestly as an **analysis-only preflight** that also writes a **pre-restore safety backup**.
- A full wipe-and-rebuild restore is **not** implemented yet because the current Things MCP surface does not expose delete/archive mutations or creation APIs for all structure types.
- That means the current guarantee is:
  - `things archive` gives us a durable JSON + Markdown archive bundle
  - `things restore --archive ...` gives us a safety backup + restore plan + structure-gap analysis
  - it does **not** erase and rebuild Things yet

## Official operating model

- The canonical inventory we care about is: areas, projects, todos, headings, and tags.
- For todos, the MCP field `list` / `list_id` means the destination container and can point to either a project or an area.
- When titles are ambiguous, the CLI should resolve the destination by UUID/ID and prefer ID-based mutation requests.
- Archive should preserve a normalized snapshot of the planning structure so we can compare archive state against current state safely.

## Official next-action model

- Every active project should have exactly one incomplete todo tagged `next-action`.
- That tagged todo is the authoritative next action for the project.
- Context is represented separately via tags such as `phone`, `browser`, `school`, or `orca`.
- Project notes may contain support material and planning context, but they are not the source of truth for the next action.
- Validation states:
  - zero `next-action` todos in a project = project needs review
  - exactly one `next-action` todo in a project = valid
  - more than one `next-action` todo in a project = invalid and requires cleanup
- Daily planning should be driven from the set of valid project next actions, not from projects in the abstract.

## Official restore target: Trash-based reconcile restore

- Restore should be implemented as a reconcile operation, not as a destructive wipe-and-rebuild.
- The restore flow should start by creating a pre-restore safety backup of the current state.
- The restore comparator should use:
  - requested archive snapshot
  - current Things snapshot
  - a designated Trash project for displaced current todos
- Reconcile goals:
  - recreate archived projects that are missing and can be recreated with the available MCP tools
  - recreate archived todos that are missing and can be recreated with the available MCP tools
  - move current todos that do not exist in the archive into the Trash project
  - report manual blockers for structure the MCP cannot currently recreate cleanly, especially missing areas or headings
- This restore target should not delete data directly.
- This restore target should treat the Trash project as a safety holding area, not as native Things trash.

## Design principles

- Things should always have Today being ~3 MAIN tasks that MUST be completed today as the primary focus - if I complete those things, Today is considered a success and should be marked as a success in my "diary"
- For the roadmap, we should create a diary to track what was done and whether the day was a success.
- Things should keep an empty "inbox" as much as possible, we should process these tasks at a minimum with a tickler file to pop these back up.
- We should think through the task principles and when the same type of task appears over and over again, we should be able to speed up the processing of that the next time.
- Let's start with REALLY good JIRA ticket creation capabilities - we will use this in line with our CLI orca (located on this same drive, ask me for integration - orca is a company used CLI for AI processing, but things is a personal/work/shcool/MY choice of task manager independent app is going to ) so the idea is still for me to want to use things to write a jira ticket idea/need to write in there, tag it "JIRA" and then we process it quickly through the right channel and put it where orca can find it and can act on it seamlessly. orca will handle the jira integration, we do not handle jira integration from things
- Fast for everyday use
- Human-readable by default
- JSON and Markdown available when needed
- Safe-by-default for mutations
- Powerful enough for chained personal workflows
- Stable selectors underneath, friendly numbered output on top
- Projects should always know which areas they belong to, specifying a project should always know which Area it belongs to
- My life divides into Work, School, Music, Travel, Health, Media (reading/movie/tv lists), Jokes (I want a tell me a joke function (at some point, clearly not important now) and it pulls from my joke list), Personal, and a few other Business venture style projects - "Reggae" and an app I'm going to be working on. The projects that exist in these areas must remain identifable "name taken" even below that area level If we have "Work (area) - Tasks (project)" then it shoul be "Work (area) - Work Tasks (project)" if there is another "Tasks" floating around or that kind of thing

## I'm rereading the below and I see what was implemented, but i'm not sure it's all as it needs to be, plus i'm adding a few new ones in line

- `things list`
  - list available Things MCP tools
- `things export`
  - export a full Things snapshot to JSON and Markdown. this is actually not really useful for command line output, but what we should really get working here is archive quality export, that way we can be a bit more flexible and action oriented. i'll just add few commands here that are likely to be called by tools rather than me
- `things archive` - essentially dumps the entirety of the things tasks/projects/areas and puts them into a dated, read-only archive
- `things restore --archive "YYYYMMDD` - currently implemented as a restore preflight: it first creates a special pre-restore backup archive, then compares the requested archive with current Things state and writes a restore plan. future goal remains full erase/rebuild, but that is blocked until the MCP tool surface exposes the required delete/archive + structure-creation operations.
- `things create-task --title --project ...`
  - prepare or create a task using area/project/heading selectors
  - this is much more critical for chaining tasks rather than outright how i might choose to enter in things, but if it's really so clean I can see using it for quick notes from the terminal
- `things update-task ...`
  - prepare or update an existing task
  - i'm not sure how we select which task to process here, that mechanism isn't clear - I mean - I'm not sure what's best, but maybe I have to specify a project, and then it gives me a numbered list of tasks, and then i say which one i want to update? not sure because the numbered list of tasks would be best if it were 1, 2, 3 -- but that's not the hash that uniquely identifies the task, so then we have to maintain a temporary mutable list of what "1" would refer to. that might be fine, but not sure if it's a best practice or there is a better technique for the CLI 
- `things update-project ...`
  - prepare or update an existing project
  - what does this even mean?
- `things task-context ...`
  - build a task context payload and optional debug artifacts
  - not sure what that means
- `things task-llm ...`
  - i do not like this name, and i think it probably needs a -llm flag on another CLI instead, maybe update-task has either a human direction where i input or edit text, and a -llm direction where it enriches it 
  - prepare an LLM request and optionally execute it
- `things task-proposals --request-file ... --decision-file ...`
  - convert reviewed decisions into suggestion-only action proposals
  - not sure what this does
- `things inbox-questions`
  - build a Today-first Inbox question set for offline clarification
  - not sure what this does
- `things inbox-answers --input-file ...`
  - read answered Inbox Markdown and build reviewed action artifacts
  - not sure what this does

## Highest-value next capabilities

These are the best next steps for making the CLI feel powerful at the keyboard:

My things workflow should always be the following
1. Every project I choose to review should be enhanced in a GTD methodology "you can't DO projects, you can only do next actions" and we should ALWAYS double check to make sure the next action is clearly defined and there is a context of when and where it can get done
2. Today should be agreed upon by presenting a list of next actions across projects and either maintaining a prioritization scheme (labels as high priority and near due date are presented as "suggested today" alongside all possible "todays") - a "possible" today is a Project + Next Action. We just simply won't try to do anything where on one project I have to do two next actions in a row. That's a limitation to the system, but when i finish a next action on a project, I'll like understand if i need to keep going, but I don't want to forget about it.
3. So again, each day should begin with an empty "Today" list - then I'm presented a lightly prioritized list of all of the possible next actions, and I choose 3 or 4 to become promoted to "Today"
4. Whenever I run something like "things today" we are going to make sure all of the tasks are full enriched and we have a context tag that says what i have to do or where to run it "orca" (for anything LLM command line) or "phone" if i need to make a phone call, "browser" now i'm just making stuff up, but let's say i need to research something, "school" for schoolwork almost always from VSCode or a browser. I am just saying that I should be able to do what David Allen describes as "I'm away from keyboard and i can knock something out, I should be able to bring up "Phone" and get only the tasks that i can complete from the phone.
5. I should be urged to bring in a weekly review for unprocessed tasks that have no been enriched or fully tagged. it's ok for me to have an idea and put it in here, but it's no ok for it to linger without tagging and the possibility that it resurface. 
6. i want my brain to never feel the need to remember anything, i want confidence that i'm going to be prompted to remember as long as i capture it in this system

### 1. Read and browse commands

- `things today`
- `things inbox`
- `things anytime`
- `things someday`
- `things upcoming`
- `things recent --period 7d`
- `things tasks --project "Project Name"`
- `things tasks --area "Area Name"`
- `things tasks --tag "Tag Name"`
- `things tasks --status incomplete`
- `things projects`
- `things areas`
- `things tags`
- `things headings --project "Project Name"`

### 2. Numbered result sets

Every list-style command should be able to return numbered results, for example:

- `things tasks --project "Home"`
- output shows `1.`, `2.`, `3.` with short summaries
- YES, love this, this is what i was saying earlier

This enables fast follow-up commands like:

- `things show 1`
- `things update 1 --notes "..."`
- `things move 1 --project "Errands"`
- `things complete 1`

## Selector model

Recommended behavior:

- human selects by number
- CLI resolves the number back to a stable UUID from the last result set (YESSSSS)
- result sets are stored locally in a small cache file
- mutation commands can require either:
  - explicit UUID/title, or
  - a numbered item from the most recent result set

This gives convenience without losing correctness.

## Recommended command families

### Read

- `things today`
- `things inbox`
- `things tasks ...`
- `things projects`
- `things areas`
- `things tags`
- `things show <selector>`
- `things search "text"`

### Create

- `things add task "Title"`
- `things add project "Title"`
- `things capture "raw thought here"`

### Update

- `things update <selector> --title ...`
- `things update <selector> --notes ...`
- `things schedule <selector> --when tomorrow`
- `things deadline <selector> --date 2026-03-20`
- `things tag <selector> --add Foo`
- `things move <selector> --project "Project Name"`
- `things complete <selector>`
- `things cancel <selector>`

### Workflow

- `things inbox-questions`
- `things inbox-answers --input-file ...`
- `things plan day`
- `things plan week`
- `things review today`
- `things review inbox`

### AI / augmentation

- `things task-context ...`
- `things task-llm ...`
- `things task-proposals ...`
- future: `things ask ...`
- future: `things suggest ...`

## Power-user interaction ideas

- `--json` for machine-readable output
- `--md` for Markdown output
- `--one-line` for compact terminal output
- `--limit N`
- `--sort due|when|updated|created`
- `--interactive`
- `--copy-uuid`
- `--open`
- `--dry-run`
- `--apply`
- `--from-last` to use the last numbered result set

## Example workflows we want

### Project task browsing

- `things tasks --project "Kitchen"`
- `things show 1 --from-last`
- `things update 1 --from-last --notes "Need measurements first"`
- `things move 1 --from-last --project "Someday / Home Ideas"`

### Fast daily control

- `things today`
- `things complete 2 --from-last`
- `things schedule 4 --from-last --when tomorrow`
- `things add task "Buy screws" --project "Kitchen"`

### Capture and refine

- `things capture "idea about a better studio monitor workflow"`
- later: `things inbox-questions`
- later: `things inbox-answers --input-file ...`

## Specific feature request already identified

We want this style of flow:

1. Get tasks by project.
2. Return them numbered.
3. Act on task `1` directly in the CLI.
4. Continue processing more items without losing context.

This should become a first-class UX target.

## Likely implementation order

1. Create the durable local item format and stable key generation.
2. Implement no-wrap rendering plus `things task list`, `things task next`, and `things task show`.
3. Implement `things task review` and its structured task-vs-project interview.
4. Create `prompts/task-ai-polish.md` and wire prompt-driven polish behavior.
5. Implement `things task open` via `vim <path>` and post-exit AI polish.
6. Implement `things task accept` with UX-facing summaries and safe apply behavior.
7. Resume browse-heavy daily planning on top of the new task-processing flow.
8. Return to broader read/update command families once the core inbox-processing loop feels excellent.

## Open questions

- What is the smallest durable local store that cleanly supports stable keys like `T-184`?
- How should task rows truncate area/project/title within a fixed terminal width?
- Should `task list` default to all reviewable local items or to a narrower active slice?
- What is the cleanest UX for choosing an existing project home during review/accept?
- When should `accept` create a new project versus target an existing one with the same or similar title?

## Gregory scratchpad

Add any stream-of-consciousness thoughts below this line. Treat them as product input, not final command design.

---

Ok, sorry, did everything above in stream of consciousness inline.

i did not review all of the commands, they seem like an awful lot, i know we build some of them, but it would be great if we could first solidify the typical workflow, guarantee archive and restore, then work to making everything fluid as i create my daily plans and brain dump into the inbox, etc. 

Implemented first guarantee:

- archive: yes
- pre-restore safety backup: yes
- restore-plan / feasibility analysis: yes
- destructive wipe-and-rebuild restore: not yet possible with the current MCP tools
