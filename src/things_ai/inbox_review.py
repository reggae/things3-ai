from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .control import SelectionError, prepare_update_todo_request, resolve_area, resolve_project
from .llm_bridge import build_command_handoff
from .snapshot import INBOX_ANSWER_FIELDS, fetch_snapshot, now_utc, timestamp_slug

INBOX_ANSWER_REVIEW_SCHEMA_VERSION = "things-ai.inbox-answer-review.v1"
QUESTION_SET_SCHEMA_VERSION = "things-ai.inbox-questions.v1"
MARKDOWN_FIELD_NAMES = {
    "question_id",
    "status",
    "todo_uuid",
    "sources",
    "current_title",
    "current_status",
    "current_project",
    "current_area",
    "current_heading",
    "current_when",
    "current_deadline",
    "current_tags",
    "current_notes",
    *(f"answer_{field}" for field in INBOX_ANSWER_FIELDS),
}


def review_inbox_answer_file(
    markdown_path: Path,
    *,
    snapshot: dict[str, Any] | None = None,
    question_set: dict[str, Any] | None = None,
    command_text: str | None = None,
) -> dict[str, Any]:
    parsed = parse_inbox_answer_markdown(markdown_path.read_text(encoding="utf-8"))
    question_set = question_set or load_companion_question_set(markdown_path)
    snapshot = snapshot or fetch_snapshot(command_text=command_text)
    return build_inbox_answer_review(
        parsed,
        snapshot=snapshot,
        question_set=question_set,
        source_path=markdown_path,
    )


def parse_inbox_answer_markdown(text: str) -> dict[str, Any]:
    questions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_field: str | None = None
    current_lines: list[str] = []

    def flush_field() -> None:
        nonlocal current_field, current_lines
        if current is None or current_field is None:
            return
        fields = current.setdefault("fields", {})
        fields[current_field] = normalize_markdown_block(current_lines, quoted=current_field == "current_notes")
        current_field = None
        current_lines = []

    def finish_question() -> None:
        flush_field()
        if not isinstance(current, dict):
            return
        fields = dict(current.get("fields") or {})
        question = {
            "question_id": str(fields.get("question_id") or current.get("question_id") or ""),
            "title": str(current.get("title") or fields.get("current_title") or ""),
            "status": str(fields.get("status") or ""),
            "todo_uuid": str(fields.get("todo_uuid") or ""),
            "sources": split_csv(fields.get("sources")),
            "current": {
                "title": str(fields.get("current_title") or ""),
                "status": str(fields.get("current_status") or ""),
                "project": str(fields.get("current_project") or ""),
                "area": str(fields.get("current_area") or ""),
                "heading": str(fields.get("current_heading") or ""),
                "when": str(fields.get("current_when") or ""),
                "deadline": str(fields.get("current_deadline") or ""),
                "tags": split_csv(fields.get("current_tags")),
                "notes": str(fields.get("current_notes") or ""),
            },
            "answers": {field: str(fields.get(f"answer_{field}") or "") for field in INBOX_ANSWER_FIELDS},
        }
        questions.append(question)

    for raw_line in text.splitlines():
        heading = parse_question_heading(raw_line)
        if heading is not None:
            finish_question()
            current = {"question_id": heading["question_id"], "title": heading["title"], "fields": {}}
            current_field = None
            current_lines = []
            continue
        if current is None:
            continue
        parsed = parse_markdown_field(raw_line)
        if parsed is not None:
            flush_field()
            current_field, first_value = parsed
            current_lines = [first_value] if first_value else []
            continue
        if current_field is not None:
            current_lines.append(raw_line)

    finish_question()
    return {
        "schema_version": QUESTION_SET_SCHEMA_VERSION,
        "kind": "parsed-inbox-answer-markdown",
        "question_count": len(questions),
        "questions": questions,
    }


def build_inbox_answer_review(
    parsed_document: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    question_set: dict[str, Any] | None = None,
    source_path: Path | None = None,
) -> dict[str, Any]:
    question_set_index = index_question_set(question_set)
    snapshot_todos = index_snapshot_todos(snapshot)
    reviewed_questions = [
        review_single_answer(
            question,
            snapshot=snapshot,
            snapshot_todos=snapshot_todos,
            source_question=question_set_index.get(question.get("question_id") or ""),
        )
        for question in parsed_document.get("questions", [])
        if isinstance(question, dict)
    ]
    counts = count_review_statuses(reviewed_questions)
    result: dict[str, Any] = {
        "schema_version": INBOX_ANSWER_REVIEW_SCHEMA_VERSION,
        "kind": "inbox-answer-review",
        "generated_at": now_utc(),
        "source": {
            "markdown_path": str(source_path) if source_path else None,
            "question_set_path": str(companion_json_path(source_path)) if source_path and companion_json_path(source_path).exists() else None,
            "snapshot_generated_at": snapshot.get("generated_at"),
        },
        "counts": counts,
        "questions": reviewed_questions,
    }
    result["source"] = compact_dict(result["source"])
    return result


def review_single_answer(
    question: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    snapshot_todos: dict[str, dict[str, Any]],
    source_question: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = merge_question_context(question, source_question)
    answers = {field: str((merged.get("answers") or {}).get(field) or "").strip() for field in INBOX_ANSWER_FIELDS}
    answered_fields = [field for field, value in answers.items() if value]
    result = {
        "question_id": merged.get("question_id") or "",
        "todo_uuid": merged.get("todo_uuid") or "",
        "title": merged.get("title") or "",
        "sources": list(merged.get("sources") or []),
        "answers": answers,
        "status": "unanswered",
    }
    if not answered_fields:
        return result

    todo_uuid = str(merged.get("todo_uuid") or "")
    current_todo = snapshot_todos.get(todo_uuid)
    if not todo_uuid:
        result["status"] = "error"
        result["error"] = "question is missing todo_uuid"
        return result
    if current_todo is None:
        result["status"] = "error"
        result["error"] = f"todo {todo_uuid} was not found in the current snapshot"
        return result

    next_action = classify_next_action(answers.get("next_action") or "")
    result["intent"] = {
        "next_action_kind": next_action["kind"],
        "normalized_when": normalize_when_value(answers.get("when") or "")[0],
        "normalized_deadline": normalize_deadline_value(answers.get("deadline") or "")[0],
    }

    if next_action["kind"] == "delete":
        result["status"] = "manual_review"
        result["notes"] = delete_followup_notes(answered_fields)
        result["manual_handoff"] = build_delete_handoff(current_todo)
        return result

    if next_action["kind"] == "complete":
        return finalize_prepared_request(
            result,
            answered_fields=answered_fields,
            request_builder=lambda: prepare_update_todo_request(snapshot, todo_uuid=todo_uuid, completed=True),
            notes=completion_followup_notes(answered_fields),
        )

    notes_text = build_clarification_notes(
        current_notes=str(current_todo.get("notes") or ""),
        summary=answers.get("summary") or "",
        next_action=next_action.get("text") or "",
        extra_notes=answers.get("notes") or "",
        question_id=str(result["question_id"]),
    )
    normalized_when, when_issue = normalize_when_value(answers.get("when") or "")
    normalized_deadline, deadline_issue = normalize_deadline_value(answers.get("deadline") or "")
    request_kwargs = compact_dict(
        {
            "todo_uuid": todo_uuid,
            "notes": notes_text,
            "when": normalized_when,
            "deadline": normalized_deadline,
        }
    )
    move_kwargs, move_manual_fields = resolve_move_destination_answers(
        snapshot,
        area_title=answers.get("area") or "",
        project_title=answers.get("project") or "",
    )
    request_kwargs.update(move_kwargs)
    manual_fields = [item for item in (when_issue, deadline_issue, *move_manual_fields) if item is not None]
    if len(request_kwargs) == 1:
        result["status"] = "manual_review"
        result["manual_fields"] = manual_fields
        result["notes"] = [item["reason"] for item in manual_fields]
        return result

    finalized = finalize_prepared_request(
        result,
        answered_fields=answered_fields,
        request_builder=lambda: prepare_update_todo_request(snapshot, **request_kwargs),
        notes=[item["reason"] for item in manual_fields],
        manual_fields=manual_fields,
    )
    return finalized


def finalize_prepared_request(
    result: dict[str, Any],
    *,
    answered_fields: list[str],
    request_builder: Any,
    notes: list[str] | None = None,
    manual_fields: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        prepared_request = request_builder()
        command_handoff = build_command_handoff(prepared_request)
    except (SelectionError, ValueError) as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    result["prepared_request"] = prepared_request
    result["command_handoff"] = command_handoff
    if manual_fields:
        result["status"] = "partial"
        result["manual_fields"] = manual_fields
    else:
        result["status"] = "ready"
    if notes:
        result["notes"] = [item for item in notes if item]
    result["answered_fields"] = answered_fields
    return result


def render_inbox_answer_review_markdown(review_bundle: dict[str, Any]) -> str:
    counts = dict(review_bundle.get("counts") or {})
    lines = [
        "# Things Inbox Answer Review",
        "",
        f"- Generated at: {review_bundle.get('generated_at')}",
        f"- Questions: {counts.get('questions', 0)}",
        f"- Answered: {counts.get('answered', 0)}",
        f"- Unanswered: {counts.get('unanswered', 0)}",
        f"- Ready: {counts.get('ready', 0)}",
        f"- Partial: {counts.get('partial', 0)}",
        f"- Manual review: {counts.get('manual_review', 0)}",
        f"- Errors: {counts.get('error', 0)}",
    ]
    for question in review_bundle.get("questions", []):
        if not isinstance(question, dict) or question.get("status") == "unanswered":
            continue
        lines.extend(
            [
                "",
                f"## {question.get('question_id') or 'Q???'} — {question.get('title') or question.get('todo_uuid')}",
                "",
                f"- Status: {question.get('status')}",
                f"- Todo UUID: {question.get('todo_uuid')}",
            ]
        )
        if question.get("notes"):
            lines.extend(["- Notes:"])
            lines.extend(f"  - {item}" for item in question.get("notes", []) if item)
        if question.get("manual_fields"):
            lines.extend(["- Manual fields:"])
            lines.extend(
                f"  - {item.get('field')}: {item.get('value')} — {item.get('reason')}"
                for item in question.get("manual_fields", [])
                if isinstance(item, dict)
            )
        if question.get("error"):
            lines.extend(["", "### Error", "", str(question.get("error"))])
            continue
        if question.get("prepared_request"):
            lines.extend(["", "### Prepared Request", "", json.dumps(question.get("prepared_request"), indent=2, sort_keys=True)])
        if question.get("command_handoff"):
            handoff = dict(question.get("command_handoff") or {})
            lines.extend(
                [
                    "",
                    "### Review Handoff",
                    "",
                    "#### Dry Run Command",
                    "",
                    str(((handoff.get("dry_run") or {}).get("shell") or "")),
                    "",
                    "#### Apply Command",
                    "",
                    str(((handoff.get("apply") or {}).get("shell") or "")),
                ]
            )
        if question.get("manual_handoff"):
            lines.extend(["", "### Manual Handoff", "", json.dumps(question.get("manual_handoff"), indent=2, sort_keys=True)])
    return "\n".join(lines).rstrip() + "\n"


def write_inbox_answer_review_artifacts(
    review_bundle: dict[str, Any], *, output_dir: Path, prefix: str = "things-inbox-answer-review"
) -> dict[str, Path]:
    target_dir = output_dir / "inbox-answer-review" / datetime.now(UTC).strftime("%Y-%m-%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{prefix}-{timestamp_slug()}"
    json_path = target_dir / f"{stem}.json"
    markdown_path = target_dir / f"{stem}.md"
    json_path.write_text(json.dumps(review_bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_inbox_answer_review_markdown(review_bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def build_clarification_notes(
    *,
    current_notes: str,
    summary: str,
    next_action: str,
    extra_notes: str,
    question_id: str,
) -> str | None:
    sections: list[str] = []
    if summary:
        sections.extend(["Summary:", summary])
    if next_action:
        sections.extend(["Next action:", next_action])
    if extra_notes:
        sections.extend(["Additional notes:", extra_notes])
    if not sections:
        return None
    prefix = [current_notes.strip()] if current_notes.strip() else []
    block = [f"Inbox clarification ({question_id})", "", *sections]
    return "\n\n".join(prefix + ["\n".join(block)]).strip()


def resolve_move_destination_answers(
    snapshot: dict[str, Any], *, area_title: str, project_title: str
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    request_kwargs: dict[str, Any] = {}
    manual_fields: list[dict[str, str]] = []
    move_area = None
    normalized_area = normalize_inline_text(area_title)
    normalized_project = normalize_inline_text(project_title)

    if normalized_area:
        try:
            move_area = resolve_area(snapshot, area_title=normalized_area)
        except SelectionError as exc:
            manual_fields.append(unresolved_selector_field("area", normalized_area, str(exc)))
        else:
            request_kwargs.update(selector_request_kwargs("move_area", move_area))

    if normalized_project:
        try:
            move_project = resolve_project(snapshot, project_title=normalized_project, area=move_area)
        except SelectionError as exc:
            manual_fields.append(unresolved_selector_field("project", normalized_project, str(exc)))
        else:
            request_kwargs.update(selector_request_kwargs("move_project", move_project))

    return request_kwargs, manual_fields


def selector_request_kwargs(prefix: str, item: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    if item.get("uuid") not in (None, ""):
        return {f"{prefix}_uuid": item["uuid"]}
    if item.get("title") not in (None, ""):
        return {f"{prefix}_title": item["title"]}
    return {}


def classify_next_action(value: str) -> dict[str, str]:
    text = normalize_inline_text(value)
    if not text:
        return {"kind": "none", "text": ""}
    lowered = text.lower()
    collapsed = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    if lowered.startswith("delete") or lowered.startswith("remove"):
        return {"kind": "delete", "text": text}
    if collapsed in {"complete", "completed", "done"}:
        return {"kind": "complete", "text": text}
    return {"kind": "text", "text": text}


def normalize_when_value(value: str) -> tuple[str | None, dict[str, str] | None]:
    text = normalize_inline_text(value)
    if not text:
        return None, None
    lowered = text.lower()
    direct = {"today", "tomorrow", "evening", "anytime", "someday"}
    if lowered in direct:
        return lowered, None
    if lowered == "anytime is fine":
        return "anytime", None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:@[0-9]{2}:[0-9]{2})?", text):
        return text, None
    if re.fullmatch(r"[0-9]{8}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}", None
    return None, unsupported_field("when", text, "supported values are today/tomorrow/evening/anytime/someday or YYYY-MM-DD")


def normalize_deadline_value(value: str) -> tuple[str | None, dict[str, str] | None]:
    text = normalize_inline_text(value)
    if not text:
        return None, None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
        return text, None
    if re.fullmatch(r"[0-9]{8}", text):
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}", None
    return None, unsupported_field("deadline", text, "supported values are YYYY-MM-DD")


def unsupported_field(field: str, value: str, expectation: str) -> dict[str, str]:
    return {"field": field, "value": value, "reason": f"Could not auto-apply {field!r} value {value!r}; {expectation}."}


def unresolved_selector_field(field: str, value: str, error: str) -> dict[str, str]:
    return {"field": field, "value": value, "reason": f"Could not auto-apply {field!r} value {value!r}; {error}."}


def delete_followup_notes(answered_fields: list[str]) -> list[str]:
    notes = ["DELETE intent detected, but no validated MCP delete tool is available yet."]
    extra_fields = [field for field in answered_fields if field != "next_action"]
    if extra_fields:
        notes.append(f"Ignored additional answered fields because delete intent takes precedence: {', '.join(extra_fields)}.")
    return notes


def completion_followup_notes(answered_fields: list[str]) -> list[str]:
    extra_fields = [field for field in answered_fields if field != "next_action"]
    if not extra_fields:
        return []
    return [f"Ignored additional answered fields because complete intent takes precedence: {', '.join(extra_fields)}."]


def build_delete_handoff(todo: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_required": True,
        "action": "delete_todo",
        "notes": [
            "Delete this todo manually in Things.",
            "No validated MCP delete tool is currently available in the live surface.",
        ],
        "target": compact_dict({"kind": "todo", "uuid": todo.get("uuid"), "title": todo.get("title")}),
    }


def merge_question_context(question: dict[str, Any], source_question: dict[str, Any] | None) -> dict[str, Any]:
    source_question = source_question or {}
    source_todo = dict(source_question.get("todo") or {})
    merged = {
        "question_id": question.get("question_id") or source_question.get("question_id") or "",
        "todo_uuid": question.get("todo_uuid") or source_todo.get("uuid") or "",
        "title": question.get("title") or source_todo.get("title") or "",
        "sources": question.get("sources") or source_question.get("sources") or [],
        "answers": question.get("answers") or source_question.get("answers") or {},
        "current": question.get("current") or {},
    }
    if source_todo.get("uuid") not in (None, "") and merged["todo_uuid"] not in ("", source_todo.get("uuid")):
        merged["source_mismatch"] = True
    return merged


def count_review_statuses(questions: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"questions": len(questions), "answered": 0, "unanswered": 0, "ready": 0, "partial": 0, "manual_review": 0, "error": 0}
    for question in questions:
        status = str(question.get("status") or "unanswered")
        if status == "unanswered":
            counts["unanswered"] += 1
            continue
        counts["answered"] += 1
        if status in counts:
            counts[status] += 1
    return counts


def index_question_set(question_set: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(question_set, dict):
        return {}
    questions = question_set.get("questions")
    if not isinstance(questions, list):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for question in questions:
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or "")
        if question_id:
            index[question_id] = question
    return index


def index_snapshot_todos(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    todos = ((snapshot.get("normalized") or {}).get("todos") or []) if isinstance(snapshot, dict) else []
    return {str(todo.get("uuid")): todo for todo in todos if isinstance(todo, dict) and todo.get("uuid") not in (None, "")}


def load_companion_question_set(markdown_path: Path) -> dict[str, Any] | None:
    json_path = companion_json_path(markdown_path)
    if not json_path.exists():
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != QUESTION_SET_SCHEMA_VERSION:
        return None
    return payload


def companion_json_path(markdown_path: Path | None) -> Path:
    if markdown_path is None:
        return Path(".")
    return markdown_path.with_suffix(".json")


def parse_question_heading(line: str) -> dict[str, str] | None:
    match = re.match(r"^##\s+(Q[0-9]+)\s+—\s*(.*)$", line.strip())
    if not match:
        return None
    return {"question_id": match.group(1), "title": match.group(2).strip()}


def parse_markdown_field(line: str) -> tuple[str, str] | None:
    if ":" not in line:
        return None
    label, value = line.split(":", 1)
    normalized = label.strip()
    if normalized not in MARKDOWN_FIELD_NAMES:
        return None
    return normalized, value.lstrip()


def normalize_markdown_block(lines: list[str], *, quoted: bool = False) -> str:
    normalized = [strip_quote_prefix(line) if quoted else line.rstrip() for line in lines]
    while normalized and not normalized[0].strip():
        normalized.pop(0)
    while normalized and not normalized[-1].strip():
        normalized.pop()
    return "\n".join(normalized).strip()


def strip_quote_prefix(line: str) -> str:
    stripped = line.rstrip()
    if stripped.startswith(">"):
        return stripped[1:].lstrip()
    return stripped


def normalize_inline_text(value: Any) -> str:
    return str(value or "").strip()


def split_csv(value: Any) -> list[str]:
    text = normalize_inline_text(value)
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}