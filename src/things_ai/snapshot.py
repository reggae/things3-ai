from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .mcp import StdioMcpClient

TEXT_FIELD_NAMES = {
    "title": "title",
    "uuid": "uuid",
    "type": "type",
    "status": "status",
    "list": "list",
    "when": "when",
    "start date": "start_date",
    "deadline": "deadline",
    "created": "created_at",
    "age": "age",
    "modified": "modified_at",
    "last modified": "last_modified",
    "completion date": "completed_at",
    "completed": "completed_at",
    "notes": "notes",
    "project": "project",
    "area": "area",
    "heading": "heading",
    "tags": "tags",
    "checklist": "checklist",
    "tasks": "tasks",
    "projects": "projects",
    "headings": "headings",
}

SNAPSHOT_TOOLS = {
    "todos": ("get_todos", {"include_items": True}),
    "projects": ("get_projects", {"include_items": True}),
    "areas": ("get_areas", {"include_items": True}),
    "tags": ("get_tags", {}),
}

INBOX_QUESTION_TOOLS = {
    "today": ("get_today", {}),
    "inbox": ("get_inbox", {}),
}

INBOX_ANSWER_FIELDS = (
    "summary",
    "next_action",
    "project",
    "area",
    "when",
    "deadline",
    "notes",
)

ARCHIVE_SCHEMA_VERSION = "things-ai.archive.v1"
RESTORE_PLAN_SCHEMA_VERSION = "things-ai.restore-plan.v1"


def fetch_snapshot(command_text: str | None = None) -> dict[str, Any]:
    with StdioMcpClient.from_environment(command_text=command_text) as client:
        tools = client.list_tools()
        raw_tool_results = {
            label: client.call_tool(tool_name, arguments)
            for label, (tool_name, arguments) in SNAPSHOT_TOOLS.items()
        }

    normalized = {
        label: normalize_collection(label[:-1], call["payload"])
        for label, call in raw_tool_results.items()
    }
    normalized = reconcile_normalized_snapshot(normalized)

    return {
        "schema_version": "0.1",
        "generated_at": now_utc(),
        "source": {
            "integration": "things-mcp",
            "transport": "stdio",
            "command": command_text or "uvx things-mcp",
        },
        "available_tools": [tool.get("name") for tool in tools if isinstance(tool, dict)],
        "summary": {label: len(items) for label, items in normalized.items()},
        "normalized": normalized,
        "raw_tool_results": raw_tool_results,
    }


def export_snapshot(
    output_dir: Path,
    prefix: str = "things-snapshot",
    command_text: str | None = None,
) -> dict[str, Path]:
    snapshot = fetch_snapshot(command_text=command_text)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = timestamp_slug()
    json_path = output_dir / f"{prefix}-{timestamp}.json"
    markdown_path = output_dir / f"{prefix}-{timestamp}.md"

    json_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_snapshot_markdown(snapshot), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def archive_snapshot(
    output_dir: Path,
    *,
    prefix: str = "things-archive",
    command_text: str | None = None,
    write_artifacts: bool = True,
    archive_reason: str = "manual-archive",
) -> dict[str, Any]:
    archive_bundle = build_archive_bundle(fetch_snapshot(command_text=command_text), archive_reason=archive_reason)
    result: dict[str, Any] = {"archive_bundle": archive_bundle}
    if write_artifacts:
        result["artifacts"] = write_archive_bundle_artifacts(archive_bundle, output_dir=output_dir, prefix=prefix)
    return result


def build_archive_bundle(snapshot: dict[str, Any], *, archive_reason: str = "manual-archive") -> dict[str, Any]:
    summary = summarize_snapshot(snapshot)
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "kind": "archive-bundle",
        "archive_id": timestamp_slug(),
        "generated_at": now_utc(),
        "archive_reason": archive_reason,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "source": deepcopy(snapshot.get("source", {})),
        "available_tools": list(snapshot.get("available_tools") or []),
        "summary": summary,
        "snapshot": deepcopy(snapshot),
    }


def write_archive_bundle_artifacts(
    archive_bundle: dict[str, Any], *, output_dir: Path, prefix: str = "things-archive"
) -> dict[str, Path]:
    generated_at = str(archive_bundle.get("generated_at") or "")
    target_dir = output_dir / "archives" / archive_partition_date(generated_at)
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}-{archive_bundle.get('archive_id') or timestamp_slug()}"
    json_path = target_dir / f"{stem}.json"
    markdown_path = target_dir / f"{stem}.md"
    json_path.write_text(json.dumps(archive_bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_archive_bundle_markdown(archive_bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def render_archive_bundle_markdown(archive_bundle: dict[str, Any]) -> str:
    summary = archive_bundle.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    snapshot = archive_bundle.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    capabilities = build_restore_capability_report(archive_bundle.get("available_tools", []))
    lines = [
        "# Things Archive",
        "",
        f"- Archive id: {archive_bundle.get('archive_id') or ''}",
        f"- Archive generated at: {archive_bundle.get('generated_at') or ''}",
        f"- Snapshot generated at: {archive_bundle.get('snapshot_generated_at') or snapshot.get('generated_at') or ''}",
        f"- Reason: {archive_bundle.get('archive_reason') or 'manual-archive'}",
        f"- Integration: {source.get('integration') or ''}",
        f"- Command: `{source.get('command') or ''}`",
        "",
        "## Counts",
        f"- todos: {summary.get('todos', 0)}",
        f"- projects: {summary.get('projects', 0)}",
        f"- areas: {summary.get('areas', 0)}",
        f"- tags: {summary.get('tags', 0)}",
        f"- headings: {summary.get('headings', 0)}",
        "",
        "## Restore reality",
    ]
    if capabilities["full_fidelity_restore_supported"]:
        lines.append("- Current tool surface appears sufficient for full-fidelity restore automation.")
    else:
        lines.append("- Full destructive restore is not currently supported from this CLI.")
        if not capabilities["can_delete_existing_items"]:
            lines.append("- Existing Things data cannot be wiped because no delete/archive mutation tools are exposed.")
        if not capabilities["can_create_areas"]:
            lines.append("- Areas cannot be recreated from scratch because no area-creation tool is exposed.")
        if not capabilities["can_create_headings"]:
            lines.append("- Headings cannot be recreated from scratch because no heading-creation tool is exposed.")
    return "\n".join(lines) + "\n"


def resolve_archive_reference(reference: str, *, output_dir: Path, prefix: str = "things-archive") -> Path:
    candidate = Path(reference).expanduser()
    if candidate.exists():
        return candidate

    archive_date = normalize_archive_reference_date(reference)
    if archive_date is None:
        raise FileNotFoundError(f"Archive reference not found: {reference}")

    target_dir = output_dir / "archives" / archive_date
    matches = sorted(target_dir.glob(f"{prefix}-*.json"))
    if not matches:
        raise FileNotFoundError(f"No archive bundles found for {archive_date} in {target_dir}")
    return matches[-1]


def load_archive_bundle(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("kind") == "archive-bundle":
        return payload
    if isinstance(payload, dict) and "normalized" in payload and "source" in payload:
        return build_archive_bundle(payload, archive_reason="snapshot-import")
    raise ValueError(f"Unsupported archive payload: {path}")


def plan_restore(
    archive_reference: str,
    *,
    output_dir: Path,
    prefix: str = "things-restore-plan",
    archive_prefix: str = "things-archive",
    backup_prefix: str = "things-pre-restore-backup",
    trash_area_uuid: str | None = None,
    trash_area_title: str | None = None,
    trash_project_uuid: str | None = None,
    trash_project_title: str | None = "Trash",
    apply: bool = False,
    command_text: str | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    if apply and not write_artifacts:
        raise ValueError("restore apply mode requires artifact writing so the safety backup and plan are preserved")

    archive_path = resolve_archive_reference(archive_reference, output_dir=output_dir, prefix=archive_prefix)
    archive_bundle = load_archive_bundle(archive_path)
    current_snapshot = fetch_snapshot(command_text=command_text)

    safety_backup_bundle = None
    if write_artifacts:
        safety_backup_bundle = build_archive_bundle(current_snapshot, archive_reason="pre-restore-safety-backup")

    restore_plan = build_restore_plan(
        archive_bundle,
        current_snapshot,
        archive_reference=archive_reference,
        archive_path=archive_path,
        safety_backup_bundle=safety_backup_bundle,
        apply=apply,
        trash_area_uuid=trash_area_uuid,
        trash_area_title=trash_area_title,
        trash_project_uuid=trash_project_uuid,
        trash_project_title=trash_project_title,
    )
    if apply:
        restore_plan["execution"] = execute_restore_actions(restore_plan, command_text=command_text)

    result: dict[str, Any] = {
        "resolved_archive_path": str(archive_path),
        "restore_plan": restore_plan,
    }
    if write_artifacts:
        safety_backup_artifacts = write_archive_bundle_artifacts(
            safety_backup_bundle,
            output_dir=output_dir,
            prefix=backup_prefix,
        )
        plan_artifacts = write_restore_plan_artifacts(restore_plan, output_dir=output_dir, prefix=prefix)
        result["artifacts"] = plan_artifacts
        result["safety_backup"] = {
            "archive_id": safety_backup_bundle.get("archive_id"),
            "generated_at": safety_backup_bundle.get("generated_at"),
            "artifacts": safety_backup_artifacts,
        }
    return result


def build_restore_plan(
    archive_bundle: dict[str, Any],
    current_snapshot: dict[str, Any],
    *,
    archive_reference: str | None = None,
    archive_path: Path | None = None,
    safety_backup_bundle: dict[str, Any] | None = None,
    apply: bool = False,
    trash_area_uuid: str | None = None,
    trash_area_title: str | None = None,
    trash_project_uuid: str | None = None,
    trash_project_title: str | None = "Trash",
) -> dict[str, Any]:
    archive_snapshot = archive_bundle.get("snapshot")
    if not isinstance(archive_snapshot, dict):
        archive_snapshot = {}
    archive_summary = summarize_snapshot(archive_snapshot)
    current_summary = summarize_snapshot(current_snapshot)
    capabilities = build_restore_capability_report(current_snapshot.get("available_tools", []))
    missing_area_titles = missing_archive_area_titles(archive_snapshot, current_snapshot)
    missing_heading_refs = missing_archive_heading_refs(archive_snapshot, current_snapshot)
    reconcile_plan = build_restore_reconcile_plan(
        archive_snapshot,
        current_snapshot,
        capabilities,
        trash_area_uuid=trash_area_uuid,
        trash_area_title=trash_area_title,
        trash_project_uuid=trash_project_uuid,
        trash_project_title=trash_project_title,
    )
    blocking_reasons = build_restore_blocking_reasons(
        capabilities,
        archive_summary=archive_summary,
        missing_area_titles=missing_area_titles,
        missing_heading_refs=missing_heading_refs,
        reconcile_plan=reconcile_plan,
    )

    restore_plan = {
        "schema_version": RESTORE_PLAN_SCHEMA_VERSION,
        "kind": "restore-plan",
        "generated_at": now_utc(),
        "execution_mode": "apply" if apply else "analysis-only",
        "requested_archive": compact(
            {
                "reference": archive_reference,
                "resolved_path": str(archive_path) if archive_path else None,
                "archive_id": archive_bundle.get("archive_id"),
                "archive_generated_at": archive_bundle.get("generated_at"),
                "snapshot_generated_at": archive_bundle.get("snapshot_generated_at"),
            }
        ),
        "archive_summary": archive_summary,
        "current_summary": current_summary,
        "capabilities": capabilities,
        "feasibility": {
            "destructive_restore_supported": capabilities["destructive_restore_supported"],
            "full_fidelity_restore_supported": capabilities["full_fidelity_restore_supported"],
            "reconcile_restore_supported": bool(reconcile_plan.get("summary", {}).get("ready_action_count", 0)),
            "analysis_only": not apply,
        },
        "structure_gaps": compact(
            {
                "missing_area_count": len(missing_area_titles),
                "missing_area_titles": missing_area_titles[:25],
                "missing_heading_count": len(missing_heading_refs),
                "missing_heading_samples": [format_heading_reference(ref) for ref in missing_heading_refs[:25]],
            }
        ),
        "reconcile": reconcile_plan,
        "blocking_reasons": blocking_reasons,
        "recommended_next_steps": build_restore_next_steps(capabilities, reconcile_plan=reconcile_plan, apply=apply),
    }
    if safety_backup_bundle is not None:
        restore_plan["safety_backup"] = compact(
            {
                "archive_id": safety_backup_bundle.get("archive_id"),
                "generated_at": safety_backup_bundle.get("generated_at"),
                "summary": safety_backup_bundle.get("summary"),
            }
        )
    return restore_plan


def write_restore_plan_artifacts(
    restore_plan: dict[str, Any], *, output_dir: Path, prefix: str = "things-restore-plan"
) -> dict[str, Path]:
    target_dir = output_dir / "restore-plans" / archive_partition_date(str(restore_plan.get("generated_at") or ""))
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}-{timestamp_slug()}"
    json_path = target_dir / f"{stem}.json"
    markdown_path = target_dir / f"{stem}.md"
    json_path.write_text(json.dumps(restore_plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_restore_plan_markdown(restore_plan), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def render_restore_plan_markdown(restore_plan: dict[str, Any]) -> str:
    requested_archive = restore_plan.get("requested_archive")
    if not isinstance(requested_archive, dict):
        requested_archive = {}
    archive_summary = restore_plan.get("archive_summary")
    if not isinstance(archive_summary, dict):
        archive_summary = {}
    current_summary = restore_plan.get("current_summary")
    if not isinstance(current_summary, dict):
        current_summary = {}
    structure_gaps = restore_plan.get("structure_gaps")
    if not isinstance(structure_gaps, dict):
        structure_gaps = {}
    reconcile = restore_plan.get("reconcile")
    if not isinstance(reconcile, dict):
        reconcile = {}
    reconcile_summary = reconcile.get("summary")
    if not isinstance(reconcile_summary, dict):
        reconcile_summary = {}
    execution = restore_plan.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    blocking_reasons = restore_plan.get("blocking_reasons")
    if not isinstance(blocking_reasons, list):
        blocking_reasons = []
    next_steps = restore_plan.get("recommended_next_steps")
    if not isinstance(next_steps, list):
        next_steps = []

    lines = [
        "# Things Restore Plan",
        "",
        f"- Generated at: {restore_plan.get('generated_at') or ''}",
        f"- Mode: {restore_plan.get('execution_mode') or 'analysis-only'}",
        f"- Archive reference: {requested_archive.get('reference') or requested_archive.get('resolved_path') or ''}",
        f"- Resolved archive path: {requested_archive.get('resolved_path') or ''}",
        "",
        "## Archive counts",
        f"- todos: {archive_summary.get('todos', 0)}",
        f"- projects: {archive_summary.get('projects', 0)}",
        f"- areas: {archive_summary.get('areas', 0)}",
        f"- headings: {archive_summary.get('headings', 0)}",
        "",
        "## Current counts",
        f"- todos: {current_summary.get('todos', 0)}",
        f"- projects: {current_summary.get('projects', 0)}",
        f"- areas: {current_summary.get('areas', 0)}",
        f"- headings: {current_summary.get('headings', 0)}",
        "",
        "## Reconcile summary",
        f"- missing archived projects: {reconcile_summary.get('missing_project_count', 0)}",
        f"- missing archived todos: {reconcile_summary.get('missing_todo_count', 0)}",
        f"- extra current todos: {reconcile_summary.get('extra_current_todo_count', 0)}",
        f"- ready actions: {reconcile_summary.get('ready_action_count', 0)}",
        f"- blocked actions: {reconcile_summary.get('blocked_action_count', 0)}",
        "",
        "## Blocking reasons",
    ]
    if blocking_reasons:
        lines.extend(f"- {reason}" for reason in blocking_reasons)
    else:
        lines.append("- No blocking reasons detected.")
    lines.extend(
        [
            "",
            "## Structure gaps",
            f"- Missing areas: {structure_gaps.get('missing_area_count', 0)}",
            f"- Missing headings: {structure_gaps.get('missing_heading_count', 0)}",
        ]
    )
    if execution:
        lines.extend(
            [
                "",
                "## Execution",
                f"- attempted actions: {execution.get('attempted_action_count', 0)}",
                f"- applied actions: {execution.get('applied_action_count', 0)}",
                f"- failed actions: {execution.get('failed_action_count', 0)}",
            ]
        )
    lines.extend(["", "## Recommended next steps"])
    lines.extend(f"- {step}" for step in next_steps)
    return "\n".join(lines) + "\n"


def fetch_inbox_question_set(command_text: str | None = None) -> dict[str, Any]:
    with StdioMcpClient.from_environment(command_text=command_text) as client:
        tools = client.list_tools()
        raw_tool_results = {
            label: client.call_tool(tool_name, arguments)
            for label, (tool_name, arguments) in INBOX_QUESTION_TOOLS.items()
        }

    return build_inbox_question_set(
        today_payload=raw_tool_results["today"]["payload"],
        inbox_payload=raw_tool_results["inbox"]["payload"],
        command_text=command_text,
        available_tools=[tool.get("name") for tool in tools if isinstance(tool, dict)],
    )


def build_inbox_question_set(
    *,
    today_payload: Any,
    inbox_payload: Any,
    command_text: str | None = None,
    available_tools: list[str] | None = None,
) -> dict[str, Any]:
    today_items = normalize_collection("todo", today_payload)
    inbox_items = normalize_collection("todo", inbox_payload)
    questions = build_inbox_questions(today_items=today_items, inbox_items=inbox_items)
    today_first_count = sum(1 for question in questions if "today" in question.get("sources", []))
    return {
        "schema_version": "things-ai.inbox-questions.v1",
        "kind": "inbox-question-set",
        "generated_at": now_utc(),
        "source": {
            "integration": "things-mcp",
            "transport": "stdio",
            "command": command_text or "uvx things-mcp",
        },
        "available_tools": available_tools or [],
        "counts": {
            "today": len(today_items),
            "inbox": len(inbox_items),
            "questions": len(questions),
            "today_first_questions": today_first_count,
        },
        "questions": questions,
    }


def write_inbox_question_set_artifacts(
    question_set: dict[str, Any], *, output_dir: Path, prefix: str = "things-inbox-questions"
) -> dict[str, Path]:
    target_dir = output_dir / "inbox-questions" / datetime.now(UTC).strftime("%Y-%m-%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}-{timestamp_slug()}"
    json_path = target_dir / f"{stem}.json"
    markdown_path = target_dir / f"{stem}.md"
    json_path.write_text(json.dumps(question_set, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_inbox_question_markdown(question_set), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def normalize_collection(kind: str, payload: Any) -> list[dict[str, Any]]:
    items = extract_items(payload)
    return [normalize_item(kind, item) for item in items]


def extract_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, str):
        return parse_text_records(payload)
    if not isinstance(payload, dict):
        return []

    text_result = payload.get("result")
    if isinstance(text_result, str):
        parsed = parse_text_records(text_result)
        if parsed:
            return parsed

    for key in ("items", "todos", "projects", "areas", "tags", "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    for value in payload.values():
        if isinstance(value, list):
            return value
    return []


def parse_text_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for chunk in text.split("\n\n---\n\n"):
        record = parse_text_record(chunk)
        if record:
            records.append(record)
    return records


def parse_text_record(text: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    current_label: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_label, current_lines
        if current_label is None:
            return

        key = TEXT_FIELD_NAMES[current_label]
        raw_value = "\n".join(current_lines).rstrip()
        assign_text_field(record, key, raw_value)
        current_label = None
        current_lines = []

    for raw_line in text.splitlines():
        parsed = parse_text_field(raw_line)
        if parsed is not None:
            flush()
            current_label, first_value = parsed
            current_lines = [first_value] if first_value else []
            continue

        if current_label is not None:
            current_lines.append(raw_line.rstrip())

    flush()
    return compact(record)


def parse_text_field(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None

    label, value = line.split(":", 1)
    normalized = label.strip().lower()
    if normalized not in TEXT_FIELD_NAMES:
        return None
    return normalized, value.lstrip()


def assign_text_field(record: dict[str, Any], key: str, value: str) -> None:
    if key == "tags":
        record[key] = parse_tags(value)
        return
    if key == "checklist":
        record["items"] = parse_block_items(value, checkbox_items=True)
        return
    if key in {"tasks", "projects", "headings"}:
        record[key] = parse_block_items(value)
        return
    if key in {"project", "area", "heading"}:
        if value:
            record[key] = {"title": value}
        return
    if value:
        record[key] = value


def parse_tags(value: str) -> list[str]:
    return [tag.strip() for tag in value.split(",") if tag.strip()]


def parse_block_items(value: str, *, checkbox_items: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        status: str | None = None
        title = stripped
        if stripped.startswith(("☐ ", "☑ ", "✓ ", "✔ ")):
            marker, title = stripped.split(" ", 1)
            if checkbox_items:
                status = "complete" if marker in {"☑", "✓", "✔"} else "incomplete"
        elif stripped.startswith(("- ", "* ")):
            title = stripped[2:].strip()

        item = compact({"title": title, "status": status})
        if item:
            items.append(item)
    return items


def normalize_item(
    kind: str,
    item: Any,
    *,
    parent_context: dict[str, Any] | None = None,
    inherited_relationships: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        return compact({"kind": kind, "value": item, "parent": parent_reference(parent_context)})

    tags = item.get("tags")
    children = item.get("items")
    relationships = build_relationships(item, tags, inherited_relationships=inherited_relationships)
    normalized = {
        "kind": kind,
        "uuid": first_non_empty(item, "uuid", "id"),
        "title": first_non_empty(item, "title", "name"),
        "type": first_non_empty(item, "type", "item_type"),
        "status": item.get("status"),
        "notes": item.get("notes"),
        "list": item.get("list"),
        "when": first_non_empty(item, "when", "start_date"),
        "deadline": first_non_empty(item, "deadline", "deadline_date"),
        "created_at": item.get("created_at"),
        "modified_at": item.get("modified_at"),
        "completed_at": first_non_empty(item, "completed_at", "completion_date"),
        "relationships": relationships,
        "parent": parent_reference(parent_context),
        "checklist_item_count": len(children) if isinstance(children, list) else 0,
    }
    current_context = {
        "kind": kind,
        "uuid": normalized["uuid"],
        "title": normalized["title"],
        "relationships": relationships,
    }
    nested_children = normalize_nested_collections(item, parent_context=current_context)
    normalized["children"] = nested_children
    normalized["child_counts"] = {label: len(values) for label, values in nested_children.items()}
    return compact(normalized)


def normalize_nested_collections(
    item: dict[str, Any], *, parent_context: dict[str, Any] | None = None
) -> dict[str, list[dict[str, Any]]]:
    return compact(
        {
            "checklist_items": normalize_item_list(
                "checklist_item", item.get("items"), parent_context=parent_context
            ),
            "todos": normalize_item_list("todo", item.get("tasks"), parent_context=parent_context),
            "projects": normalize_item_list(
                "project", item.get("projects"), parent_context=parent_context
            ),
            "headings": normalize_item_list(
                "heading", item.get("headings"), parent_context=parent_context
            ),
        }
    )


def normalize_item_list(
    kind: str, items: Any, *, parent_context: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    inherited_relationships = inherited_relationship_values(parent_context)
    return [
        normalize_item(
            kind,
            item,
            parent_context=parent_context,
            inherited_relationships=inherited_relationships,
        )
        for item in items
    ]


def render_snapshot_markdown(snapshot: dict[str, Any]) -> str:
    lines = [
        "# Things Snapshot",
        "",
        f"- Generated at: {snapshot['generated_at']}",
        f"- Integration: {snapshot['source']['integration']}",
        f"- Command: `{snapshot['source']['command']}`",
        "",
        "## Counts",
    ]
    summary = snapshot.get("summary", {})
    for label in ("todos", "projects", "areas", "tags"):
        lines.append(f"- {label}: {summary.get(label, 0)}")

    normalized = snapshot.get("normalized", {})
    for label in ("todos", "projects", "areas", "tags"):
        lines.extend(["", f"## {label.title()}"])
        for item in normalized.get(label, []):
            title = item.get("title") or item.get("uuid") or "(untitled)"
            status = item.get("status") or "unknown"
            lines.append(f"- [{status}] {single_line(title)} — {item.get('uuid', 'no-uuid')}")
    return "\n".join(lines) + "\n"


def build_inbox_questions(*, today_items: list[dict[str, Any]], inbox_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []

    for source, items in (("today", today_items), ("inbox", inbox_items)):
        for item in items:
            key = inbox_question_key(item)
            question = questions_by_key.get(key)
            if question is None:
                question = {
                    "question_id": "",
                    "status": "pending",
                    "sources": [],
                    "todo": item,
                    "answers": {field: "" for field in INBOX_ANSWER_FIELDS},
                }
                questions_by_key[key] = question
                order.append(key)
            if source not in question["sources"]:
                question["sources"].append(source)

    questions: list[dict[str, Any]] = []
    for index, key in enumerate(order, start=1):
        question = dict(questions_by_key[key])
        question["question_id"] = f"Q{index:03d}"
        questions.append(question)
    return questions


def inbox_question_key(item: dict[str, Any]) -> tuple[Any, ...]:
    uuid = item.get("uuid")
    if uuid not in (None, ""):
        return ("uuid", uuid)

    relationships = item.get("relationships")
    if not isinstance(relationships, dict):
        relationships = {}

    return (
        "title",
        single_line(item.get("title") or ""),
        relationships.get("project_uuid") or relationships.get("project_title"),
        relationships.get("area_uuid") or relationships.get("area_title"),
        relationships.get("heading_uuid") or relationships.get("heading_title"),
    )


def render_inbox_question_markdown(question_set: dict[str, Any]) -> str:
    counts = question_set.get("counts")
    if not isinstance(counts, dict):
        counts = {}

    lines = [
        "# Things Inbox Questions",
        "",
        f"- Generated at: {question_set.get('generated_at')}",
        f"- Today items fetched: {counts.get('today', 0)}",
        f"- Inbox items fetched: {counts.get('inbox', 0)}",
        f"- Questions: {counts.get('questions', 0)}",
        "",
        "## Instructions",
        "",
        "- Fill any `answer_*:` lines you want and leave the others blank.",
        "- Keep `question_id:` and `todo_uuid:` unchanged so later tooling can match answers safely.",
        "- Today-sourced items are listed first, followed by the remaining Inbox items.",
    ]

    questions = question_set.get("questions")
    if not isinstance(questions, list):
        questions = []

    for question in questions:
        if not isinstance(question, dict):
            continue
        todo = question.get("todo")
        if not isinstance(todo, dict):
            todo = {}
        relationships = todo.get("relationships")
        if not isinstance(relationships, dict):
            relationships = {}
        answers = question.get("answers")
        if not isinstance(answers, dict):
            answers = {}
        tags = relationships.get("tag_names")
        if not isinstance(tags, list):
            tags = []
        sources = question.get("sources")
        if not isinstance(sources, list):
            sources = []

        lines.extend(
            [
                "",
                f"## {question.get('question_id') or 'Q???'} — {single_line(todo.get('title') or todo.get('uuid') or '(untitled)')}",
                "",
                f"question_id: {question.get('question_id') or ''}",
                f"status: {question.get('status') or 'pending'}",
                f"todo_uuid: {todo.get('uuid') or ''}",
                f"sources: {', '.join(str(source) for source in sources)}",
                f"current_title: {single_line(todo.get('title') or '')}",
                f"current_status: {todo.get('status') or ''}",
                f"current_project: {relationships.get('project_title') or relationships.get('project_uuid') or ''}",
                f"current_area: {relationships.get('area_title') or relationships.get('area_uuid') or ''}",
                f"current_heading: {relationships.get('heading_title') or relationships.get('heading_uuid') or ''}",
                f"current_when: {todo.get('when') or ''}",
                f"current_deadline: {todo.get('deadline') or ''}",
                f"current_tags: {', '.join(str(tag) for tag in tags)}",
                "current_notes:",
            ]
        )

        notes = str(todo.get("notes") or "").splitlines()
        if notes:
            lines.extend(f"> {line}" for line in notes)
        else:
            lines.append(">")

        for field in INBOX_ANSWER_FIELDS:
            lines.append(f"answer_{field}: {answers.get(field, '')}")

    return "\n".join(lines).rstrip() + "\n"


def first_non_empty(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def related_uuid(value: Any, fallback: Any) -> Any:
    if isinstance(value, dict):
        return first_non_empty(value, "uuid", "id") or fallback
    return fallback


def related_title(value: Any, fallback: Any) -> Any:
    if isinstance(value, dict):
        return first_non_empty(value, "title", "name") or fallback
    if value not in (None, ""):
        return value
    return fallback


def extract_tag_values(tags: Any, key: str) -> list[Any]:
    if not isinstance(tags, list):
        return []
    values: list[Any] = []
    for tag in tags:
        if isinstance(tag, dict):
            value = tag.get(key) or tag.get("name" if key == "title" else key)
            if value not in (None, ""):
                values.append(value)
        elif key == "title":
            values.append(tag)
    return values


def build_relationships(
    item: dict[str, Any],
    tags: Any,
    *,
    inherited_relationships: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inherited = inherited_relationships or {}
    return compact(
        {
            "project_uuid": related_uuid(
                item.get("project"), first_non_empty(item, "project_uuid") or inherited.get("project_uuid")
            ),
            "project_title": related_title(
                item.get("project"), first_non_empty(item, "project_title") or inherited.get("project_title")
            ),
            "area_uuid": related_uuid(
                item.get("area"), first_non_empty(item, "area_uuid") or inherited.get("area_uuid")
            ),
            "area_title": related_title(
                item.get("area"), first_non_empty(item, "area_title") or inherited.get("area_title")
            ),
            "heading_uuid": related_uuid(
                item.get("heading"), first_non_empty(item, "heading_uuid") or inherited.get("heading_uuid")
            ),
            "heading_title": related_title(
                item.get("heading"), first_non_empty(item, "heading_title") or inherited.get("heading_title")
            ),
            "tag_uuids": extract_tag_values(tags, "uuid"),
            "tag_names": extract_tag_values(tags, "title"),
        }
    )


def parent_reference(parent_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(parent_context, dict):
        return {}
    return compact(
        {
            "kind": parent_context.get("kind"),
            "uuid": parent_context.get("uuid"),
            "title": parent_context.get("title"),
        }
    )


def inherited_relationship_values(parent_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(parent_context, dict):
        return {}

    relationships = dict(parent_context.get("relationships") or {})
    parent_kind = parent_context.get("kind")
    if parent_kind == "project":
        relationships.setdefault("project_uuid", parent_context.get("uuid"))
        relationships.setdefault("project_title", parent_context.get("title"))
    elif parent_kind == "area":
        relationships.setdefault("area_uuid", parent_context.get("uuid"))
        relationships.setdefault("area_title", parent_context.get("title"))
    elif parent_kind == "heading":
        relationships.setdefault("heading_uuid", parent_context.get("uuid"))
        relationships.setdefault("heading_title", parent_context.get("title"))
    return compact(relationships)


def find_project(
    snapshot: dict[str, Any], *, uuid: str | None = None, title: str | None = None
) -> dict[str, Any] | None:
    return find_collection_item(snapshot.get("normalized", {}).get("projects", []), uuid=uuid, title=title)


def find_area(
    snapshot: dict[str, Any], *, uuid: str | None = None, title: str | None = None
) -> dict[str, Any] | None:
    return find_collection_item(snapshot.get("normalized", {}).get("areas", []), uuid=uuid, title=title)


def find_collection_item(
    items: Any, *, uuid: str | None = None, title: str | None = None
) -> dict[str, Any] | None:
    if uuid in (None, "") and title in (None, ""):
        raise ValueError("uuid or title is required")
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        if uuid not in (None, "") and item.get("uuid") != uuid:
            continue
        if title not in (None, "") and item.get("title") != title:
            continue
        return item
    return None


def select_child_path(item: dict[str, Any], *labels: str) -> list[dict[str, Any]]:
    current_items = [item] if isinstance(item, dict) else []
    for label in labels:
        next_items: list[dict[str, Any]] = []
        for current in current_items:
            children = current.get("children")
            if not isinstance(children, dict):
                continue
            branch = children.get(label)
            if isinstance(branch, list):
                next_items.extend(child for child in branch if isinstance(child, dict))
        current_items = next_items
    return current_items


def reconcile_normalized_snapshot(normalized: dict[str, Any]) -> dict[str, Any]:
    projects = normalized.get("projects")
    areas = normalized.get("areas")
    if not isinstance(projects, list) or not isinstance(areas, list):
        return normalized

    project_index = build_project_match_index(projects)
    for area in areas:
        reconcile_area_projects(area, project_index)
    return normalized


def reconcile_area_projects(area: dict[str, Any], project_index: dict[tuple[Any, Any], list[dict[str, Any]]]) -> None:
    if not isinstance(area, dict):
        return

    children = area.get("children")
    if not isinstance(children, dict):
        return

    projects = children.get("projects")
    if not isinstance(projects, list):
        return

    for index, project in enumerate(projects):
        if not isinstance(project, dict):
            continue
        match = resolve_unique_project_match(project, project_index)
        if match is not None:
            projects[index] = merge_missing_values(project, match)


def build_project_match_index(projects: list[dict[str, Any]]) -> dict[tuple[Any, Any], list[dict[str, Any]]]:
    index: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for project in projects:
        if not isinstance(project, dict):
            continue
        for key in project_match_keys(project):
            index.setdefault(key, []).append(project)
    return index


def resolve_unique_project_match(
    project: dict[str, Any], project_index: dict[tuple[Any, Any], list[dict[str, Any]]]
) -> dict[str, Any] | None:
    for key in project_match_keys(project):
        matches = project_index.get(key, [])
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


def project_match_keys(project: dict[str, Any]) -> list[tuple[Any, Any]]:
    title = project.get("title")
    if title in (None, ""):
        return []

    relationships = project.get("relationships")
    if not isinstance(relationships, dict):
        relationships = {}

    keys: list[tuple[Any, Any]] = []
    for scope in (relationships.get("area_uuid"), relationships.get("area_title"), None):
        key = (title, scope)
        if key not in keys:
            keys.append(key)
    return keys


def merge_missing_values(target: Any, source: Any) -> Any:
    if isinstance(target, dict) and isinstance(source, dict):
        merged = deepcopy(target)
        for key, value in source.items():
            if key not in merged or merged[key] in (None, "", {}, []):
                merged[key] = deepcopy(value)
            else:
                merged[key] = merge_missing_values(merged[key], value)
        return compact(merged)
    if isinstance(target, list):
        return deepcopy(target) if target else deepcopy(source)
    return target if target not in (None, "") else deepcopy(source)


def summarize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    normalized = snapshot.get("normalized")
    if not isinstance(normalized, dict):
        normalized = {}
    return {
        "todos": len(normalized.get("todos") or []),
        "projects": len(normalized.get("projects") or []),
        "areas": len(normalized.get("areas") or []),
        "tags": len(normalized.get("tags") or []),
        "headings": count_heading_items(normalized.get("projects") or []),
    }


def count_heading_items(projects: list[dict[str, Any]]) -> int:
    count = 0
    for project in projects:
        if not isinstance(project, dict):
            continue
        headings = select_child_path(project, "headings")
        count += len(headings)
    return count


def build_restore_capability_report(available_tools: Any) -> dict[str, Any]:
    tools = sorted(str(tool) for tool in available_tools if isinstance(tool, str))
    report = {
        "available_tools": tools,
        "can_create_todos": "add_todo" in tools,
        "can_create_projects": "add_project" in tools,
        "can_update_todos": "update_todo" in tools,
        "can_update_projects": "update_project" in tools,
        "can_create_areas": any(tool in tools for tool in ("add_area", "create_area")),
        "can_create_headings": any(tool in tools for tool in ("add_heading", "create_heading")),
        "can_delete_existing_items": any(
            tool in tools
            for tool in (
                "delete_item",
                "delete_todo",
                "delete_project",
                "remove_item",
                "archive_item",
                "trash_item",
            )
        ),
    }
    report["destructive_restore_supported"] = bool(report["can_delete_existing_items"])
    report["full_fidelity_restore_supported"] = bool(
        report["destructive_restore_supported"]
        and report["can_create_todos"]
        and report["can_create_projects"]
        and report["can_create_areas"]
        and report["can_create_headings"]
    )
    return report


def missing_archive_area_titles(archive_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> list[str]:
    archive_titles = set(snapshot_area_titles(archive_snapshot))
    current_titles = set(snapshot_area_titles(current_snapshot))
    return sorted(archive_titles - current_titles)


def snapshot_area_titles(snapshot: dict[str, Any]) -> list[str]:
    normalized = snapshot.get("normalized")
    if not isinstance(normalized, dict):
        return []
    areas = normalized.get("areas")
    if not isinstance(areas, list):
        return []
    titles = {str(area.get("title")) for area in areas if isinstance(area, dict) and area.get("title") not in (None, "")}
    return sorted(titles)


def missing_archive_heading_refs(
    archive_snapshot: dict[str, Any], current_snapshot: dict[str, Any]
) -> list[tuple[str | None, str | None, str]]:
    archive_refs = set(snapshot_heading_refs(archive_snapshot))
    current_refs = set(snapshot_heading_refs(current_snapshot))
    return sorted(archive_refs - current_refs)


def snapshot_heading_refs(snapshot: dict[str, Any]) -> list[tuple[str | None, str | None, str]]:
    normalized = snapshot.get("normalized")
    if not isinstance(normalized, dict):
        return []
    projects = normalized.get("projects")
    if not isinstance(projects, list):
        return []

    refs: set[tuple[str | None, str | None, str]] = set()
    for project in projects:
        if not isinstance(project, dict):
            continue
        relationships = project.get("relationships")
        if not isinstance(relationships, dict):
            relationships = {}
        area_scope = relationships.get("area_title") or relationships.get("area_uuid")
        project_label = project.get("title") or project.get("uuid")
        for heading in select_child_path(project, "headings"):
            heading_label = heading.get("title") or heading.get("uuid")
            if heading_label in (None, ""):
                continue
            refs.add((area_scope, project_label, str(heading_label)))
    return sorted(refs)


def format_heading_reference(reference: tuple[str | None, str | None, str]) -> str:
    area_scope, project_label, heading_label = reference
    parts = [part for part in (area_scope, project_label, heading_label) if part not in (None, "")]
    return " / ".join(str(part) for part in parts)


def build_restore_blocking_reasons(
    capabilities: dict[str, Any],
    *,
    archive_summary: dict[str, Any],
    missing_area_titles: list[str],
    missing_heading_refs: list[tuple[str | None, str | None, str]],
) -> list[str]:
    reasons: list[str] = []
    if not capabilities.get("can_delete_existing_items"):
        reasons.append(
            "Current Things MCP tools do not expose delete/archive/trash mutations, so the CLI cannot wipe existing Things data before rebuild."
        )
    if archive_summary.get("areas", 0) and not capabilities.get("can_create_areas"):
        reasons.append(
            "Current Things MCP tools do not expose area creation, so archive areas cannot be recreated from scratch."
        )
    if archive_summary.get("headings", 0) and not capabilities.get("can_create_headings"):
        reasons.append(
            "Current Things MCP tools do not expose heading creation, so archive headings cannot be recreated from scratch."
        )
    if missing_area_titles and not capabilities.get("can_create_areas"):
        reasons.append(
            f"The archive references {len(missing_area_titles)} area title(s) that are not present in the current snapshot."
        )
    if missing_heading_refs and not capabilities.get("can_create_headings"):
        reasons.append(
            f"The archive references {len(missing_heading_refs)} heading location(s) that are not present in the current snapshot."
        )
    return reasons


def build_restore_next_steps(capabilities: dict[str, Any]) -> list[str]:
    steps = [
        "Treat this command as a restore preflight: review the generated plan JSON/Markdown before making any manual changes.",
        "Keep the pre-restore safety backup so you can compare current state against the requested archive.",
    ]
    if not capabilities.get("can_delete_existing_items"):
        steps.append("Do not attempt a destructive reset from this CLI yet; the current MCP tool surface cannot remove existing items.")
    if not capabilities.get("can_create_areas") or not capabilities.get("can_create_headings"):
        steps.append("If full restore becomes necessary, the MCP layer will need create-area/create-heading support first.")
    steps.append("Use the archive as a durable read-only backup and manual reconstruction reference until restore capabilities improve.")
    return steps


def normalize_archive_reference_date(reference: str) -> str | None:
    value = reference.strip()
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


def archive_partition_date(value: str) -> str:
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return datetime.now(UTC).strftime("%Y-%m-%d")


def compact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: compact(item) for key, item in value.items()}
        return {key: item for key, item in result.items() if item not in (None, {}, [])}
    if isinstance(value, list):
        return [compact(item) for item in value if item not in (None, {}, [])]
    return value


def single_line(text: str) -> str:
    return " ".join(str(text).split())


def now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")