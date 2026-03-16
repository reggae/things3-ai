from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from .control import create_project, create_todo, resolve_area, resolve_project, update_project, update_todo
from .llm_bridge import complete
from .mcp import StdioMcpClient
from .snapshot import fetch_snapshot, normalize_collection, now_utc, single_line

TASK_INDEX_SCHEMA_VERSION = "things-ai.task-index.v1"
TASK_ITEM_SCHEMA_VERSION = "things-ai.task-item.v1"
TASK_SELECTION_SCHEMA_VERSION = "things-ai.task-selection.v1"
REVIEWABLE_STATES = ("new", "reviewing", "proposed", "active")
ROW_WIDTH = 100
REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_AI_POLISH_PROMPT_PATH = REPO_ROOT / "prompts" / "task-ai-polish.md"


def list_tasks(
    *,
    output_dir: Path,
    today_items: list[dict[str, Any]] | None = None,
    inbox_items: list[dict[str, Any]] | None = None,
    available_tools: list[str] | None = None,
    command_text: str | None = None,
) -> dict[str, Any]:
    synced = sync_task_store(
        output_dir=output_dir,
        today_items=today_items,
        inbox_items=inbox_items,
        available_tools=available_tools,
        command_text=command_text,
    )
    reviewable = reviewable_items(synced["items"])
    selection = write_selection_cache(output_dir=output_dir, items=reviewable)
    return {
        "status": "ok",
        "items": reviewable,
        "selection": selection,
        "rendered": render_task_list(reviewable),
    }


def next_task(
    *,
    output_dir: Path,
    today_items: list[dict[str, Any]] | None = None,
    inbox_items: list[dict[str, Any]] | None = None,
    available_tools: list[str] | None = None,
    command_text: str | None = None,
) -> dict[str, Any]:
    listed = list_tasks(
        output_dir=output_dir,
        today_items=today_items,
        inbox_items=inbox_items,
        available_tools=available_tools,
        command_text=command_text,
    )
    items = listed["items"]
    if not items:
        return {
            "status": "empty",
            "items": [],
            "rendered": "# Things Tasks\n\nNo reviewable items found in Today or Inbox.\n",
        }

    task = items[0]
    return {
        "status": "ok",
        "task": task,
        "rendered": render_task_detail(output_dir=output_dir, item=task, slot=1, include_menu=True),
    }


def show_task(*, output_dir: Path, selector: str) -> dict[str, Any]:
    store = load_task_store(output_dir=output_dir)
    item = resolve_task_selector(output_dir=output_dir, selector=selector, items=store["items"])
    slot = resolve_slot_for_key(output_dir=output_dir, key=str(item.get("key") or ""))
    return {
        "status": "ok",
        "task": item,
        "rendered": render_task_detail(output_dir=output_dir, item=item, slot=slot, include_menu=False),
    }


def review_task(
    *,
    output_dir: Path,
    selector: str,
    input_func: Callable[[str], str] = input,
) -> dict[str, Any]:
    store = load_task_store(output_dir=output_dir)
    item = resolve_task_selector(output_dir=output_dir, selector=selector, items=store["items"])
    slot = resolve_slot_for_key(output_dir=output_dir, key=str(item.get("key") or ""))
    review = dict(item.get("review") or {})
    review["started_at"] = review.get("started_at") or now_utc()

    raw_kind = input_func("Does this feel more like a task or a project? [task/project/unsure] ")
    about = input_func("What is this really about? ")
    area = input_func("What area does it belong to, if known? ")
    next_action = input_func("What is the next physical action? ")

    kind = normalize_review_kind(raw_kind)
    answers: dict[str, str] = {
        "kind": kind,
        "about": clean_text(about),
        "area": clean_text(area),
        "next_action": clean_text(next_action),
    }
    transcript = [
        review_prompt(prompt="kind", answer=raw_kind, normalized=kind),
        review_prompt(prompt="about", answer=about, normalized=answers["about"]),
        review_prompt(prompt="area", answer=area, normalized=answers["area"]),
        review_prompt(prompt="next_action", answer=next_action, normalized=answers["next_action"]),
    ]

    if kind == "task":
        one_action = input_func("Can this actually be done in one action? [yes/no/unsure] ")
        hidden = input_func("Is there hidden waiting, dependency, location shift, or multi-step structure? ")
        clearer = input_func("If it stays a task, what is the clearest next-action wording? ")
        answers.update(
            {
                "one_action": normalize_yes_no_unsure(one_action),
                "hidden_structure": clean_text(hidden),
                "clearer_next_action": clean_text(clearer),
            }
        )
        transcript.extend(
            [
                review_prompt(prompt="one_action", answer=one_action, normalized=answers["one_action"]),
                review_prompt(prompt="hidden_structure", answer=hidden, normalized=answers["hidden_structure"]),
                review_prompt(prompt="clearer_next_action", answer=clearer, normalized=answers["clearer_next_action"]),
            ]
        )
    else:
        outcome = input_func("What does done look like? ")
        later_steps = input_func("What are likely later steps? ")
        constraints = input_func("Are there timing, waiting, or dependency constraints? ")
        answers.update(
            {
                "outcome": clean_text(outcome),
                "later_steps": clean_text(later_steps),
                "constraints": clean_text(constraints),
            }
        )
        transcript.extend(
            [
                review_prompt(prompt="outcome", answer=outcome, normalized=answers["outcome"]),
                review_prompt(prompt="later_steps", answer=later_steps, normalized=answers["later_steps"]),
                review_prompt(prompt="constraints", answer=constraints, normalized=answers["constraints"]),
            ]
        )

    item["title"] = answers["about"] or str(item.get("title") or item.get("source_title") or "(untitled)")
    item["kind"] = kind if kind in {"task", "project"} else str(item.get("kind") or "unknown")
    item["state"] = "proposed"
    if answers["area"]:
        item["area"] = answers["area"]
    item["updated_at"] = now_utc()
    item["reviewed_at"] = item["updated_at"]

    review["answers"] = answers
    review["transcript"] = transcript
    review["completed_at"] = item["updated_at"]
    item["review"] = review

    sections = read_task_document_sections(output_dir=output_dir, item=item)
    sections["Outcome"] = choose_review_outcome(item=item, sections=sections, answers=answers)
    sections["Next Action"] = choose_review_next_action(sections=sections, answers=answers)
    sections["Steps"] = choose_review_steps(sections=sections, answers=answers)
    sections["Notes"] = append_review_notes(existing=sections.get("Notes") or "", answers=answers, reviewed_at=item["updated_at"])

    document_text = build_task_document(item, sections=sections)
    write_task_item(output_dir=output_dir, item=item, create_document=False, document_text=document_text)
    return {
        "status": "ok",
        "task": item,
        "rendered": render_review_summary(output_dir=output_dir, item=item, slot=slot, sections=sections),
    }


def open_task(
    *,
    output_dir: Path,
    selector: str,
    input_func: Callable[[str], str] = input,
    editor_func: Callable[[Path], None] | None = None,
    polish_func: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    store = load_task_store(output_dir=output_dir)
    item = resolve_task_selector(output_dir=output_dir, selector=selector, items=store["items"])
    slot = resolve_slot_for_key(output_dir=output_dir, key=str(item.get("key") or ""))
    path = ensure_task_document_path(output_dir=output_dir, item=item)

    (editor_func or launch_vim_editor)(path)

    answer = input_func("Run AI polish on this edited item? [Y/n] ")
    if not should_run_ai_polish(answer):
        sections = read_task_document_sections(output_dir=output_dir, item=item)
        return {
            "status": "ok",
            "task": item,
            "polished": False,
            "document_path": str(path),
            "rendered": render_open_summary(output_dir=output_dir, item=item, slot=slot, sections=sections, polished=False),
        }

    updated_item, sections = apply_ai_polish(
        output_dir=output_dir,
        item=item,
        path=path,
        polish_func=polish_func or default_ai_polish,
    )
    return {
        "status": "ok",
        "task": updated_item,
        "polished": True,
        "document_path": str(path),
        "rendered": render_open_summary(output_dir=output_dir, item=updated_item, slot=slot, sections=sections, polished=True),
    }


def accept_task(
    *,
    output_dir: Path,
    selector: str,
    command_text: str | None = None,
    create_project_func: Callable[..., dict[str, Any]] | None = None,
    create_todo_func: Callable[..., dict[str, Any]] | None = None,
    update_project_func: Callable[..., dict[str, Any]] | None = None,
    update_todo_func: Callable[..., dict[str, Any]] | None = None,
    project_lookup_func: Callable[[str | None, str, str | None], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    store = load_task_store(output_dir=output_dir)
    item = resolve_task_selector(output_dir=output_dir, selector=selector, items=store["items"])
    if str(item.get("state") or "new") != "proposed":
        raise ValueError("Only proposed tasks can be accepted.")

    slot = resolve_slot_for_key(output_dir=output_dir, key=str(item.get("key") or ""))
    path = ensure_task_document_path(output_dir=output_dir, item=item)
    parsed = parse_task_document_text(path.read_text(encoding="utf-8"), item=item)
    updated_item = apply_document_edits_to_item(item=item, parsed=parsed)
    sections = default_task_sections(updated_item)
    sections.update(parsed.get("sections") if isinstance(parsed.get("sections"), dict) else {})

    final_kind = accepted_kind(updated_item)
    final_area = clean_text(updated_item.get("area"))
    project_title = accepted_project_title(item=updated_item, kind=final_kind)
    next_action = accepted_next_action(item=updated_item, sections=sections)
    project_lookup = project_lookup_func or lookup_existing_project

    existing_project = project_lookup(final_area or None, project_title, command_text)
    project_result = None
    if final_kind == "project":
        project_notes = build_accept_notes(sections=sections)
        if existing_project:
            project_result = (update_project_func or update_project)(
                project_uuid=existing_project.get("uuid"),
                project_title=None if existing_project.get("uuid") else project_title,
                notes=project_notes or None,
                area_title=final_area or None,
                dry_run=False,
                command_text=command_text,
            )
        else:
            project_result = (create_project_func or create_project)(
                title=project_title,
                notes=project_notes or None,
                area_title=final_area or None,
                dry_run=False,
                command_text=command_text,
            )
            existing_project = project_lookup(final_area or None, project_title, command_text)
    elif not existing_project:
        project_result = (create_project_func or create_project)(
            title=project_title,
            area_title=final_area or None,
            dry_run=False,
            command_text=command_text,
        )
        existing_project = project_lookup(final_area or None, project_title, command_text)

    todo_notes = build_accept_notes(sections=sections)
    todo_result = accept_source_todo(
        item=updated_item,
        next_action=next_action,
        notes=todo_notes or None,
        area_title=final_area or None,
        project=existing_project,
        project_title=project_title,
        command_text=command_text,
        create_todo_func=create_todo_func or create_todo,
        update_todo_func=update_todo_func or update_todo,
    )

    updated_item["kind"] = final_kind
    updated_item["state"] = "active"
    updated_item["area"] = final_area
    updated_item["project"] = project_title
    updated_item["updated_at"] = now_utc()
    updated_item["accepted_at"] = updated_item["updated_at"]
    updated_item["accepted"] = {
        "kind": final_kind,
        "target_home": target_home_label(area=final_area, project=project_title),
        "project_title": project_title if final_kind == "project" else None,
        "next_action": next_action,
        "steps_kept_in_notes": bool(clean_text(sections.get("Steps"))),
    }

    document_text = build_task_document(updated_item, sections=sections)
    write_task_item(output_dir=output_dir, item=updated_item, create_document=False, document_text=document_text)
    return {
        "status": "ok",
        "task": updated_item,
        "project_result": project_result,
        "todo_result": todo_result,
        "rendered": render_accept_summary(
            item=updated_item,
            slot=slot,
            next_action=next_action,
            steps_kept_in_notes=bool(clean_text(sections.get("Steps"))),
        ),
    }


def sync_task_store(
    *,
    output_dir: Path,
    today_items: list[dict[str, Any]] | None = None,
    inbox_items: list[dict[str, Any]] | None = None,
    available_tools: list[str] | None = None,
    command_text: str | None = None,
) -> dict[str, Any]:
    if today_items is None or inbox_items is None:
        today_items, inbox_items, available_tools = fetch_reviewable_items(command_text=command_text)
    else:
        today_items = [item for item in today_items if is_reviewable_todo(item)]
        inbox_items = [item for item in inbox_items if is_reviewable_todo(item)]
        available_tools = available_tools or []

    root = tasks_root(output_dir)
    items_dir(root).mkdir(parents=True, exist_ok=True)
    index = load_task_index(output_dir=output_dir)
    items_by_key = {item["key"]: item for item in load_task_items(output_dir=output_dir, keys=index.get("keys", []))}
    source_index = {
        str(key): str(value)
        for key, value in dict(index.get("source_index") or {}).items()
        if key not in (None, "") and value not in (None, "")
    }
    now = now_utc()
    seen_keys: set[str] = set()

    for source_item in build_reviewable_source_items(today_items=today_items, inbox_items=inbox_items):
        source_key = source_identity(source_item)
        key = source_index.get(source_key)
        if not key:
            key = allocate_task_key(index)
            source_index[source_key] = key
        existing = items_by_key.get(key, {})
        task = build_task_item(
            output_dir=output_dir,
            key=key,
            source_key=source_key,
            source_item=source_item,
            existing=existing,
            synced_at=now,
        )
        write_task_item(output_dir=output_dir, item=task, create_document=not bool(existing))
        items_by_key[key] = task
        seen_keys.add(key)
        if key not in index.setdefault("keys", []):
            index["keys"].append(key)

    for key, item in list(items_by_key.items()):
        if key in seen_keys:
            continue
        if item.get("source_present") is False:
            continue
        item["source_present"] = False
        item["last_synced_at"] = now
        item["updated_at"] = now
        write_task_item(output_dir=output_dir, item=item, create_document=False)
        items_by_key[key] = item

    index["schema_version"] = TASK_INDEX_SCHEMA_VERSION
    index["kind"] = "task-index"
    index["updated_at"] = now
    index["available_tools"] = available_tools or []
    index["keys"] = sorted(items_by_key, key=task_key_number)
    index["source_index"] = source_index
    write_task_index(output_dir=output_dir, index=index)
    return {"index": index, "items": ordered_items(items_by_key.values())}


def load_task_store(*, output_dir: Path) -> dict[str, Any]:
    index = load_task_index(output_dir=output_dir)
    keys = list(index.get("keys") or [])
    extra_keys = sorted(path.stem for path in items_dir(tasks_root(output_dir)).glob("T-*.json")) if items_dir(tasks_root(output_dir)).exists() else []
    for key in extra_keys:
        if key not in keys:
            keys.append(key)
    return {"index": index, "items": ordered_items(load_task_items(output_dir=output_dir, keys=keys))}


def fetch_reviewable_items(command_text: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    with StdioMcpClient.from_environment(command_text=command_text) as client:
        tools = client.list_tools()
        today_payload = client.call_tool("get_today", {})["payload"]
        inbox_payload = client.call_tool("get_inbox", {})["payload"]
    today_items = [item for item in normalize_collection("todo", today_payload) if is_reviewable_todo(item)]
    inbox_items = [item for item in normalize_collection("todo", inbox_payload) if is_reviewable_todo(item)]
    available_tools = [tool.get("name") for tool in tools if isinstance(tool, dict) and tool.get("name")]
    return today_items, inbox_items, available_tools


def build_reviewable_source_items(*, today_items: list[dict[str, Any]], inbox_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for source, items in (("today", today_items), ("inbox", inbox_items)):
        for item in items:
            if not is_reviewable_todo(item):
                continue
            key = source_identity(item)
            current = merged.get(key)
            if current is None:
                current = dict(item)
                current["sources"] = []
                merged[key] = current
                order.append(key)
            current_sources = current.get("sources")
            if not isinstance(current_sources, list):
                current_sources = []
                current["sources"] = current_sources
            if source not in current_sources:
                current_sources.append(source)
    return [merged[key] for key in order]


def resolve_task_selector(*, output_dir: Path, selector: str, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    candidates = items or load_task_store(output_dir=output_dir)["items"]
    if selector.isdigit():
        selection = load_selection_cache(output_dir=output_dir)
        slots = dict(selection.get("slots") or {})
        key = slots.get(selector)
        if not key:
            raise FileNotFoundError(f"No cached task slot '{selector}'. Run `things task list` or `things task next` first.")
        selector = str(key)

    wanted = selector.upper()
    for item in candidates:
        if str(item.get("key") or "").upper() == wanted:
            return item
    raise FileNotFoundError(f"Could not find task '{selector}'.")


def render_task_list(items: list[dict[str, Any]]) -> str:
    lines = ["# Things Tasks", ""]
    if not items:
        lines.append("No reviewable items found in Today or Inbox.")
        return "\n".join(lines) + "\n"

    for slot, item in enumerate(items, start=1):
        lines.append(render_task_row(item, slot=slot))
    return "\n".join(lines) + "\n"


def render_task_detail(*, output_dir: Path, item: dict[str, Any], slot: int | None, include_menu: bool) -> str:
    path = Path(str(item.get("document_path") or item_markdown_path(output_dir=output_dir, key=str(item.get("key") or ""))))
    document = path.read_text(encoding="utf-8") if path.exists() else ""
    sources = item.get("sources") if isinstance(item.get("sources"), list) else []
    lines = [render_task_row(item, slot=slot), "", f"Path: {path}", f"State: {item.get('state') or 'new'}", f"Kind: {item.get('kind') or 'unknown'}"]
    if sources:
        lines.append(f"Sources: {', '.join(str(source) for source in sources)}")
    lines.extend(["", document.rstrip()])
    if include_menu:
        menu = ["r review", "o open", "d done", "x retire", "q quit"]
        if item.get("state") == "proposed":
            menu.insert(2, "a accept")
        lines.extend(["", f"Actions: {' | '.join(menu)}"])
    return "\n".join(lines).rstrip() + "\n"


def render_accept_summary(*, item: dict[str, Any], slot: int | None, next_action: str, steps_kept_in_notes: bool) -> str:
    final_kind = str(item.get("kind") or "task")
    project_title = clean_text(item.get("project"))
    lines = [
        "# Task Accept",
        "",
        render_task_row(item, slot=slot),
        "",
        f"Final Kind: {final_kind}",
        f"Target Home: {target_home_label(area=clean_text(item.get('area')), project=project_title)}",
    ]
    if final_kind == "project":
        lines.append(f"Project Title: {project_title}")
    lines.extend(
        [
            f"Next Action: {single_line(next_action)}",
            f"Additional steps kept in notes for now: {'yes' if steps_kept_in_notes else 'no'}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_open_summary(*, output_dir: Path, item: dict[str, Any], slot: int | None, sections: dict[str, str], polished: bool) -> str:
    path = Path(str(item.get("document_path") or item_markdown_path(output_dir=output_dir, key=str(item.get("key") or ""))))
    lines = [
        "# Task Open",
        "",
        render_task_row(item, slot=slot),
        "",
        f"Path: {path}",
        f"State: {item.get('state') or 'new'}",
        f"Kind: {item.get('kind') or 'unknown'}",
    ]
    if item.get("area"):
        lines.append(f"Area: {item['area']}")
    if item.get("project"):
        lines.append(f"Project: {item['project']}")
    if sections.get("Outcome"):
        lines.extend(["", f"Outcome: {single_line(sections['Outcome'])}"])
    if sections.get("Next Action"):
        lines.append(f"Next Action: {single_line(sections['Next Action'])}")
    if sections.get("Steps"):
        lines.append(f"Steps: {single_line(sections['Steps'])}")
    lines.extend(["", f"AI polish: {'applied' if polished else 'skipped'}. "])
    return "\n".join(lines).rstrip() + "\n"


def render_task_row(item: dict[str, Any], *, slot: int | None) -> str:
    slot_text = str(slot or "")
    key = str(item.get("key") or "T-???")
    home = truncate_text(home_label(item), 32)
    title = truncate_text(single_line(item.get("title") or item.get("source_title") or "(untitled)"), title_width(slot_text, key, home))
    return f"{slot_text} | {home} | {key} | {title}"


def reviewable_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in ordered_items(items) if str(item.get("state") or "new") in REVIEWABLE_STATES]


def ordered_items(items: Any) -> list[dict[str, Any]]:
    values = [item for item in items if isinstance(item, dict)] if isinstance(items, list) or isinstance(items, tuple) else [item for item in items if isinstance(item, dict)]
    return sorted(values, key=task_sort_key)


def task_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    state_order = {state: index for index, state in enumerate(REVIEWABLE_STATES)}
    sources = item.get("sources") if isinstance(item.get("sources"), list) else []
    return (
        state_order.get(str(item.get("state") or "new"), len(REVIEWABLE_STATES)),
        0 if item.get("source_present", True) else 1,
        0 if "today" in sources else 1,
        task_key_number(str(item.get("key") or "T-999999")),
        single_line(item.get("title") or item.get("source_title") or ""),
    )


def build_task_item(
    *,
    output_dir: Path,
    key: str,
    source_key: str,
    source_item: dict[str, Any],
    existing: dict[str, Any],
    synced_at: str,
) -> dict[str, Any]:
    relationships = source_item.get("relationships") if isinstance(source_item.get("relationships"), dict) else {}
    tags = relationships.get("tag_names") if isinstance(relationships.get("tag_names"), list) else []
    title = existing.get("title") or source_item.get("title") or source_item.get("uuid") or "(untitled)"
    task = {
        "schema_version": TASK_ITEM_SCHEMA_VERSION,
        "kind": existing.get("kind") or "unknown",
        "record_kind": "task-item",
        "key": key,
        "state": existing.get("state") or "new",
        "title": title,
        "area": relationships.get("area_title") or existing.get("area") or "",
        "project": relationships.get("project_title") or existing.get("project") or "",
        "tags": tags or existing.get("tags") or [],
        "source_uuid": source_item.get("uuid") or existing.get("source_uuid") or "",
        "source_key": source_key,
        "source_title": source_item.get("title") or existing.get("source_title") or "",
        "source_status": source_item.get("status") or existing.get("source_status") or "",
        "source_when": source_item.get("when") or existing.get("source_when") or "",
        "source_present": True,
        "sources": source_item.get("sources") or existing.get("sources") or [],
        "last_synced_at": synced_at,
        "created_at": existing.get("created_at") or synced_at,
        "updated_at": synced_at,
        "document_path": str(existing.get("document_path") or item_markdown_path(output_dir=output_dir, key=key)),
        "item_path": str(item_json_path(output_dir=output_dir, key=key)),
        "source_item": source_item,
    }
    return task


def write_task_item(*, output_dir: Path, item: dict[str, Any], create_document: bool, document_text: str | None = None) -> None:
    json_path = item_json_path(output_dir=output_dir, key=str(item.get("key") or ""))
    markdown_path = item_markdown_path(output_dir=output_dir, key=str(item.get("key") or ""))
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(item, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if document_text is not None:
        markdown_path.write_text(document_text, encoding="utf-8")
    elif create_document or not markdown_path.exists():
        markdown_path.write_text(build_task_document(item), encoding="utf-8")


def build_task_document(item: dict[str, Any], *, sections: dict[str, str] | None = None) -> str:
    tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    rendered_sections = default_task_sections(item)
    if isinstance(sections, dict):
        for name, value in sections.items():
            rendered_sections[str(name)] = str(value).strip("\n")
    lines = [
        f"# {item.get('title') or '(untitled)'}",
        "",
        f"- key: {item.get('key') or ''}",
        f"- state: {item.get('state') or 'new'}",
        f"- kind: {item.get('kind') or 'unknown'}",
        f"- area: {item.get('area') or ''}",
        f"- project: {item.get('project') or ''}",
        f"- tags: {', '.join(str(tag) for tag in tags)}",
        f"- source_uuid: {item.get('source_uuid') or ''}",
    ]
    for name in ("Outcome", "Next Action", "Steps", "Notes", "Original Capture"):
        lines.extend(["", f"## {name}", ""])
        content = rendered_sections.get(name) or ""
        if content:
            lines.extend(content.splitlines())
    return "\n".join(lines).rstrip() + "\n"


def default_task_sections(item: dict[str, Any]) -> dict[str, str]:
    source = item.get("source_item") if isinstance(item.get("source_item"), dict) else {}
    return {
        "Outcome": "",
        "Next Action": "",
        "Steps": "",
        "Notes": str(source.get("notes") or "").rstrip(),
        "Original Capture": build_original_capture(source),
    }


def read_task_document_sections(*, output_dir: Path, item: dict[str, Any]) -> dict[str, str]:
    path = Path(str(item.get("document_path") or item_markdown_path(output_dir=output_dir, key=str(item.get("key") or ""))))
    if not path.exists():
        return default_task_sections(item)
    return parse_task_document_text(path.read_text(encoding="utf-8"), item=item)["sections"]


def parse_task_document_text(text: str, *, item: dict[str, Any]) -> dict[str, Any]:
    parsed: dict[str, Any] = {"title": "", "metadata": {}, "sections": default_task_sections(item)}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if not parsed["title"] and line.startswith("# "):
            parsed["title"] = line[2:].strip()
            continue
        if current is None and line.startswith("- ") and ":" in line:
            name, value = line[2:].split(":", 1)
            parsed["metadata"][name.strip()] = value.strip()
            continue
        if line.startswith("## "):
            if current is not None:
                parsed["sections"][current] = "\n".join(buffer).strip("\n")
            current = line[3:].strip()
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        parsed["sections"][current] = "\n".join(buffer).strip("\n")
    return parsed


def build_original_capture(source_item: dict[str, Any]) -> str:
    title = str(source_item.get("title") or "").rstrip()
    notes = str(source_item.get("notes") or "").rstrip()
    if title and notes:
        return f"{title}\n\n{notes}"
    return title or notes


def ensure_task_document_path(*, output_dir: Path, item: dict[str, Any]) -> Path:
    path = Path(str(item.get("document_path") or item_markdown_path(output_dir=output_dir, key=str(item.get("key") or ""))))
    if not path.exists():
        write_task_item(output_dir=output_dir, item=item, create_document=True)
    return path


def launch_vim_editor(path: Path) -> None:
    subprocess.run(["vim", str(path)], check=True)


def should_run_ai_polish(answer: str) -> bool:
    return clean_text(answer).lower() not in {"n", "no"}


def task_ai_polish_instruction() -> str:
    return TASK_AI_POLISH_PROMPT_PATH.read_text(encoding="utf-8").strip()


def build_task_ai_polish_prompt(*, instruction: str, document_text: str) -> str:
    return (
        "\n\n".join(
            [
                instruction.strip(),
                "Revise the shared task document below.",
                "Return the full revised task document only.",
                "Do not add commentary before or after the document.",
                "Preserve the `## Original Capture` section exactly.",
                "Current task document:",
                document_text.rstrip(),
            ]
        ).rstrip()
        + "\n"
    )


def default_ai_polish(item: dict[str, Any], document_text: str) -> str:
    prompt = build_task_ai_polish_prompt(instruction=task_ai_polish_instruction(), document_text=document_text)
    result = complete(prompt, execute=True)
    return str(result.get("response_text") or "")


def apply_ai_polish(
    *,
    output_dir: Path,
    item: dict[str, Any],
    path: Path,
    polish_func: Callable[[dict[str, Any], str], str],
) -> tuple[dict[str, Any], dict[str, str]]:
    edited_text = path.read_text(encoding="utf-8")
    edited = parse_task_document_text(edited_text, item=item)
    polished_response = polish_func(item, edited_text)
    polished_text = unwrap_markdown_fence(polished_response).strip()
    if not polished_text:
        raise ValueError("AI polish returned an empty document")
    polished = parse_task_document_text(polished_text, item=item)

    updated_item = dict(item)
    updated_item["title"] = polished.get("title") or updated_item.get("title") or updated_item.get("source_title") or "(untitled)"
    metadata = polished.get("metadata") if isinstance(polished.get("metadata"), dict) else {}
    kind = clean_text(metadata.get("kind"))
    if kind:
        updated_item["kind"] = kind
    if "area" in metadata:
        updated_item["area"] = clean_text(metadata.get("area"))
    if "project" in metadata:
        updated_item["project"] = clean_text(metadata.get("project"))
    updated_item["state"] = "proposed"
    updated_item["updated_at"] = now_utc()
    updated_item["polished_at"] = updated_item["updated_at"]

    sections = default_task_sections(updated_item)
    sections.update(edited.get("sections") if isinstance(edited.get("sections"), dict) else {})
    sections.update(polished.get("sections") if isinstance(polished.get("sections"), dict) else {})
    edited_sections = edited.get("sections") if isinstance(edited.get("sections"), dict) else {}
    sections["Original Capture"] = str(
        edited_sections.get("Original Capture")
        or sections.get("Original Capture")
        or build_original_capture(updated_item.get("source_item") if isinstance(updated_item.get("source_item"), dict) else {})
    )

    document_text = build_task_document(updated_item, sections=sections)
    write_task_item(output_dir=output_dir, item=updated_item, create_document=False, document_text=document_text)
    return updated_item, sections


def apply_document_edits_to_item(*, item: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    updated_item = dict(item)
    if parsed.get("title"):
        updated_item["title"] = parsed["title"]
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    kind = clean_text(metadata.get("kind"))
    if kind:
        updated_item["kind"] = kind
    if "area" in metadata:
        updated_item["area"] = clean_text(metadata.get("area"))
    if "project" in metadata:
        updated_item["project"] = clean_text(metadata.get("project"))
    return updated_item


def accepted_kind(item: dict[str, Any]) -> str:
    return "project" if clean_text(item.get("kind")).lower() == "project" else "task"


def accepted_project_title(*, item: dict[str, Any], kind: str) -> str:
    project = clean_text(item.get("project"))
    if kind == "project":
        return project or clean_text(item.get("title")) or "Untitled Project"
    return project or "Single Actions"


def accepted_next_action(*, item: dict[str, Any], sections: dict[str, str]) -> str:
    return (
        clean_text(sections.get("Next Action"))
        or clean_text(item.get("title"))
        or clean_text(sections.get("Outcome"))
        or clean_text(item.get("source_title"))
        or "(untitled)"
    )


def build_accept_notes(*, sections: dict[str, str]) -> str:
    blocks: list[str] = []
    for name in ("Outcome", "Steps", "Notes", "Original Capture"):
        content = str(sections.get(name) or "").strip()
        if not content:
            continue
        blocks.extend([name, content])
    return "\n\n".join(blocks)


def lookup_existing_project(area_title: str | None, project_title: str, command_text: str | None) -> dict[str, Any] | None:
    if not project_title:
        return None
    snapshot = fetch_snapshot(command_text=command_text)
    area = resolve_area(snapshot, area_title=area_title) if area_title else None
    return resolve_project(snapshot, project_title=project_title, area=area)


def accept_source_todo(
    *,
    item: dict[str, Any],
    next_action: str,
    notes: str | None,
    area_title: str | None,
    project: dict[str, Any] | None,
    project_title: str,
    command_text: str | None,
    create_todo_func: Callable[..., dict[str, Any]],
    update_todo_func: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    project_uuid = str(project.get("uuid") or "") if isinstance(project, dict) else ""
    source_uuid = clean_text(item.get("source_uuid"))
    if source_uuid:
        return update_todo_func(
            todo_uuid=source_uuid,
            title=next_action,
            notes=notes,
            move_area_title=area_title,
            move_project_uuid=project_uuid or None,
            move_project_title=None if project_uuid else project_title,
            dry_run=False,
            command_text=command_text,
        )
    return create_todo_func(
        title=next_action,
        notes=notes,
        area_title=area_title,
        project_uuid=project_uuid or None,
        project_title=None if project_uuid else project_title,
        dry_run=False,
        command_text=command_text,
    )


def unwrap_markdown_fence(text: str) -> str:
    stripped = str(text or "").strip()
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return stripped


def load_task_index(*, output_dir: Path) -> dict[str, Any]:
    path = task_index_path(output_dir=output_dir)
    if not path.exists():
        return {
            "schema_version": TASK_INDEX_SCHEMA_VERSION,
            "kind": "task-index",
            "next_key_number": 1,
            "keys": [],
            "source_index": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def write_task_index(*, output_dir: Path, index: dict[str, Any]) -> None:
    path = task_index_path(output_dir=output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_task_items(*, output_dir: Path, keys: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in keys:
        path = item_json_path(output_dir=output_dir, key=str(key))
        if path.exists():
            items.append(json.loads(path.read_text(encoding="utf-8")))
    return items


def load_selection_cache(*, output_dir: Path) -> dict[str, Any]:
    path = selection_cache_path(output_dir=output_dir)
    if not path.exists():
        return {"schema_version": TASK_SELECTION_SCHEMA_VERSION, "kind": "task-selection", "slots": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_selection_cache(*, output_dir: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    slots = {str(index): str(item.get("key") or "") for index, item in enumerate(items, start=1)}
    selection = {
        "schema_version": TASK_SELECTION_SCHEMA_VERSION,
        "kind": "task-selection",
        "generated_at": now_utc(),
        "slots": slots,
    }
    path = selection_cache_path(output_dir=output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return selection


def resolve_slot_for_key(*, output_dir: Path, key: str) -> int | None:
    slots = dict(load_selection_cache(output_dir=output_dir).get("slots") or {})
    for slot, value in slots.items():
        if str(value).upper() == key.upper():
            return int(slot)
    return None


def allocate_task_key(index: dict[str, Any]) -> str:
    number = int(index.get("next_key_number") or 1)
    index["next_key_number"] = number + 1
    return f"T-{number:03d}"


def is_reviewable_todo(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("kind") not in (None, "todo"):
        return False
    return str(item.get("status") or "open").lower() not in {"completed", "canceled", "cancelled"}


def source_identity(item: dict[str, Any]) -> str:
    uuid = item.get("uuid")
    if uuid not in (None, ""):
        return f"uuid:{uuid}"
    relationships = item.get("relationships") if isinstance(item.get("relationships"), dict) else {}
    parts = [
        single_line(item.get("title") or ""),
        str(relationships.get("project_uuid") or relationships.get("project_title") or ""),
        str(relationships.get("area_uuid") or relationships.get("area_title") or ""),
        str(relationships.get("heading_uuid") or relationships.get("heading_title") or ""),
    ]
    return "fingerprint:" + "|".join(parts)


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_review_kind(value: str) -> str:
    lowered = clean_text(value).lower()
    if lowered in {"task", "t", "single", "single-action", "single action"}:
        return "task"
    if lowered in {"project", "p", "outcome"}:
        return "project"
    return "unsure"


def normalize_yes_no_unsure(value: str) -> str:
    lowered = clean_text(value).lower()
    if lowered in {"yes", "y"}:
        return "yes"
    if lowered in {"no", "n"}:
        return "no"
    return "unsure"


def review_prompt(*, prompt: str, answer: str, normalized: str) -> dict[str, str]:
    return {"prompt": prompt, "answer": answer, "normalized": normalized}


def choose_review_outcome(*, item: dict[str, Any], sections: dict[str, str], answers: dict[str, str]) -> str:
    if answers.get("outcome"):
        return str(answers["outcome"])
    if answers.get("about"):
        return str(answers["about"])
    return str(sections.get("Outcome") or item.get("title") or "")


def choose_review_next_action(*, sections: dict[str, str], answers: dict[str, str]) -> str:
    return str(answers.get("clearer_next_action") or answers.get("next_action") or sections.get("Next Action") or "")


def choose_review_steps(*, sections: dict[str, str], answers: dict[str, str]) -> str:
    return str(answers.get("later_steps") or answers.get("hidden_structure") or sections.get("Steps") or "")


def append_review_notes(*, existing: str, answers: dict[str, str], reviewed_at: str) -> str:
    lines = [f"### Review {reviewed_at}", "", f"- kind: {answers.get('kind') or 'unsure'}"]
    if answers.get("one_action"):
        lines.append(f"- can this be done in one action?: {answers['one_action']}")
    if answers.get("constraints"):
        lines.append(f"- constraints: {answers['constraints']}")
    if answers.get("area"):
        lines.append(f"- area: {answers['area']}")
    block = "\n".join(lines)
    existing_clean = str(existing).strip("\n")
    if not existing_clean:
        return block
    return f"{existing_clean}\n\n{block}"


def render_review_summary(*, output_dir: Path, item: dict[str, Any], slot: int | None, sections: dict[str, str]) -> str:
    path = Path(str(item.get("document_path") or item_markdown_path(output_dir=output_dir, key=str(item.get("key") or ""))))
    lines = [
        "# Task Review",
        "",
        render_task_row(item, slot=slot),
        "",
        f"Path: {path}",
        f"State: {item.get('state') or 'new'}",
        f"Kind: {item.get('kind') or 'unknown'}",
    ]
    if item.get("area"):
        lines.append(f"Area: {item['area']}")
    if item.get("project"):
        lines.append(f"Project: {item['project']}")
    if sections.get("Outcome"):
        lines.extend(["", f"Outcome: {single_line(sections['Outcome'])}"])
    if sections.get("Next Action"):
        lines.append(f"Next Action: {single_line(sections['Next Action'])}")
    if sections.get("Steps"):
        lines.append(f"Steps: {single_line(sections['Steps'])}")
    lines.extend(["", "AI polish: not run in this slice."])
    return "\n".join(lines).rstrip() + "\n"


def task_key_number(key: str) -> int:
    try:
        return int(str(key).split("-", 1)[1])
    except (IndexError, ValueError):
        return 999999


def truncate_text(text: str, limit: int) -> str:
    clean = single_line(text)
    if limit < 2:
        return clean[:limit]
    if len(clean) <= limit:
        return clean
    return clean[: max(1, limit - 1)].rstrip() + "…"


def title_width(slot_text: str, key: str, home: str) -> int:
    reserved = len(slot_text) + len(key) + len(home) + len(" |  |  | ")
    return max(12, ROW_WIDTH - reserved)


def home_label(item: dict[str, Any]) -> str:
    area = str(item.get("area") or "").strip()
    project = str(item.get("project") or "").strip()
    return target_home_label(area=area, project=project)


def target_home_label(*, area: str, project: str) -> str:
    if area and project:
        return f"📎 {area} / {project}"
    if area:
        return f"📎 {area}"
    if project:
        return f"📎 {project}"
    return "📎 Unsorted"


def tasks_root(output_dir: Path) -> Path:
    return output_dir / "tasks"


def items_dir(root: Path) -> Path:
    return root / "items"


def task_index_path(*, output_dir: Path) -> Path:
    return tasks_root(output_dir) / "index.json"


def selection_cache_path(*, output_dir: Path) -> Path:
    return tasks_root(output_dir) / "selection.json"


def item_json_path(*, output_dir: Path, key: str) -> Path:
    return items_dir(tasks_root(output_dir)) / f"{key}.json"


def item_markdown_path(*, output_dir: Path, key: str) -> Path:
    return items_dir(tasks_root(output_dir)) / f"{key}.md"