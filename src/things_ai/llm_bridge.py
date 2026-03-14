from __future__ import annotations

import json
import os
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .control import (
    SelectionError,
    describe_item,
    prepare_create_todo_request,
    prepare_update_project_request,
    prepare_update_todo_request,
    resolve_area,
    resolve_heading,
    resolve_project,
    resolve_todo,
)
from .snapshot import compact, fetch_snapshot, now_utc, select_child_path, timestamp_slug

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "data" / "llm"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_SLUG_MAX_LENGTH = 80
TASK_REQUEST_SCHEMA_VERSION = "things-ai.task-request.v1"
TASK_DECISION_RESPONSE_SCHEMA_VERSION = "things-ai.task-decision.v1"
TASK_PROPOSAL_SCHEMA_VERSION = "things-ai.task-proposal.v1"
REPO_LOCAL_CLI_COMMAND = ("python3", "-m", "things_ai")
REPO_LOCAL_CLI_ENV = {"PYTHONPATH": "src"}
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
MODEL_ALIASES = {
    "openai-cheap": "gpt-4.1-mini",
    "openai-strong": "gpt-4.1",
    "claude-strong": "claude-sonnet-4-5",
}


def load_dotenv_values(path: Path | None = None) -> dict[str, str]:
    env_path = path or DEFAULT_ENV_PATH
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def resolve_model_name(model: str | None, *, config: dict[str, Any] | None = None) -> str:
    requested = model or (config or {}).get("default_model") or DEFAULT_MODEL
    return MODEL_ALIASES.get(str(requested), str(requested))


def resolve_llm_config(env_path: Path | None = None) -> dict[str, Any]:
    path = env_path or DEFAULT_ENV_PATH
    dotenv_values = load_dotenv_values(path)
    default_model = env_value("LLM_DEFAULT_MODEL", dotenv_values) or DEFAULT_MODEL
    max_tokens_text = env_value("LLM_MAX_TOKENS", dotenv_values) or str(DEFAULT_MAX_TOKENS)
    try:
        max_tokens = int(max_tokens_text)
    except ValueError as exc:
        raise ValueError("LLM_MAX_TOKENS must be an integer") from exc
    artifact_root_value = (
        env_value("THINGS_AI_OUTPUT_DIR", dotenv_values)
        or env_value("SKILLS_OUTPUT_DIR", dotenv_values)
        or str(DEFAULT_ARTIFACT_ROOT)
    )
    artifact_root = resolve_repo_path(artifact_root_value)
    return {
        "env_path": str(path),
        "env_exists": path.exists(),
        "default_model": default_model,
        "resolved_default_model": resolve_model_name(default_model),
        "max_tokens": max_tokens,
        "artifact_root": str(artifact_root),
        "providers": {
            "anthropic_key_present": bool(env_value("ANTHROPIC_API_KEY", dotenv_values)),
            "openai_key_present": bool(env_value("OPENAI_API_KEY", dotenv_values)),
        },
        "model_aliases": dict(MODEL_ALIASES),
    }


def fetch_task_context_payload(
    *,
    todo_uuid: str | None = None,
    todo_title: str | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
    project_uuid: str | None = None,
    project_title: str | None = None,
    heading_uuid: str | None = None,
    heading_title: str | None = None,
    include_area: bool = False,
    command_text: str | None = None,
) -> dict[str, Any]:
    snapshot = fetch_snapshot(command_text=command_text)
    return build_task_context_payload(
        snapshot,
        todo_uuid=todo_uuid,
        todo_title=todo_title,
        area_uuid=area_uuid,
        area_title=area_title,
        project_uuid=project_uuid,
        project_title=project_title,
        heading_uuid=heading_uuid,
        heading_title=heading_title,
        include_area=include_area,
    )


def build_task_context_payload(
    snapshot: dict[str, Any],
    *,
    todo_uuid: str | None = None,
    todo_title: str | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
    project_uuid: str | None = None,
    project_title: str | None = None,
    heading_uuid: str | None = None,
    heading_title: str | None = None,
    include_area: bool = False,
) -> dict[str, Any]:
    area = resolve_area(snapshot, area_uuid=area_uuid, area_title=area_title)
    project = resolve_project(snapshot, project_uuid=project_uuid, project_title=project_title, area=area)
    heading = resolve_heading(project, heading_uuid=heading_uuid, heading_title=heading_title)
    todo = resolve_todo(
        snapshot,
        todo_uuid=todo_uuid,
        todo_title=todo_title,
        area=area,
        project=project,
        heading=heading,
    )
    if not isinstance(todo, dict):
        raise ValueError("todo selector is required")
    relationships = ensure_dict(todo.get("relationships"))
    relationship_area = relationship_selector(relationships, "area")
    area = area or resolve_area(snapshot, area_uuid=relationship_area.get("uuid"), area_title=relationship_area.get("title"))
    project = project or resolve_project(
        snapshot,
        project_uuid=relationships.get("project_uuid"),
        project_title=relationships.get("project_title"),
        area=area or relationship_area or None,
    )
    heading = heading or resolve_heading(
        project,
        heading_uuid=relationships.get("heading_uuid"),
        heading_title=relationships.get("heading_title"),
    )
    return compact(
        {
            "schema_version": "0.1",
            "generated_at": now_utc(),
            "snapshot_generated_at": snapshot.get("generated_at"),
            "selection": compact(
                {
                    "todo": describe_item(todo),
                    "project": describe_item(project),
                    "heading": describe_item(heading),
                    "area": describe_item(area) if include_area else None,
                }
            ),
            "task": summarize_item(todo, include_area=include_area),
            "project_context": build_project_context(project, selected_todo_uuid=todo.get("uuid"), include_area=include_area),
            "area": summarize_item(area, include_area=True) if include_area else None,
        }
    )


def build_task_context_prompt(payload: dict[str, Any], instruction: str) -> str:
    instruction = instruction.strip()
    if not instruction:
        raise ValueError("instruction prompt is required")
    context_json = json.dumps(payload, indent=2, sort_keys=True)
    return "\n\n".join([instruction, "Task context JSON:", context_json]) + "\n"


def build_task_decision_response_contract() -> dict[str, Any]:
    return {
        "schema_version": TASK_DECISION_RESPONSE_SCHEMA_VERSION,
        "kind": "task-decision",
        "instructions": [
            "Return a JSON object only.",
            "Treat all suggested changes as advisory; do not assume they have been applied.",
            "Ground every recommendation in the provided task context.",
        ],
        "required": ["summary", "recommended_action", "reasoning", "suggested_changes", "questions", "risks"],
        "properties": {
            "summary": "Brief assessment of the task and its current context.",
            "recommended_action": ["do_now", "defer", "schedule", "delegate", "clarify", "break_down", "drop", "leave_as_is"],
            "reasoning": "Short explanation for the recommendation.",
            "suggested_changes": [
                {
                    "kind": ["update_task", "update_project", "create_task", "add_note", "none"],
                    "target_kind": ["todo", "project", "new_todo", "none"],
                    "target_uuid": "Optional canonical UUID when referring to an existing item.",
                    "target_title": "Optional canonical title when referring to an existing item.",
                    "area_uuid": "Optional canonical area UUID for scoping or placement.",
                    "area_title": "Optional canonical area title for scoping or placement.",
                    "project_uuid": "Optional canonical project UUID for scoping or placement.",
                    "project_title": "Optional canonical project title for scoping or placement.",
                    "heading_uuid": "Optional canonical heading UUID for scoping or placement.",
                    "heading_title": "Optional canonical heading title for scoping or placement.",
                    "title": "Optional title for a new or updated task/project.",
                    "notes": "Optional note text to add or replace.",
                    "when": "Optional Things schedule value.",
                    "deadline": "Optional deadline in YYYY-MM-DD format.",
                    "tags": ["Optional replacement tag list."],
                    "checklist_items": ["Optional checklist items for a suggested new task."],
                    "completed": "Optional boolean suggesting completion status for an existing task/project.",
                    "canceled": "Optional boolean suggesting cancellation status for an existing task/project.",
                    "reason": "Why this suggested change would help.",
                }
            ],
            "questions": ["Outstanding clarifying questions, if any."],
            "risks": ["Potential downside or uncertainty to flag."],
        },
    }


def build_task_decision_prompt(payload: dict[str, Any], instruction: str) -> str:
    instruction = instruction.strip()
    if not instruction:
        raise ValueError("instruction prompt is required")
    response_contract = build_task_decision_response_contract()
    return "\n\n".join(
        [
            instruction,
            "Return JSON only that matches this response contract:",
            json.dumps(response_contract, indent=2, sort_keys=True),
            "Task context JSON:",
            json.dumps(payload, indent=2, sort_keys=True),
        ]
    ) + "\n"


def build_task_request_bundle(
    payload: dict[str, Any],
    instruction: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or resolve_llm_config()
    requested_model = model or config["default_model"]
    resolved_model = resolve_model_name(requested_model, config=config)
    token_limit = max_tokens if max_tokens is not None else int(config["max_tokens"])
    system = str(system or "")
    prompt = build_task_decision_prompt(payload, instruction)
    return compact(
        {
            "schema_version": TASK_REQUEST_SCHEMA_VERSION,
            "request_kind": "task-decision",
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "selection": ensure_dict(payload.get("selection")),
            "task": ensure_dict(payload.get("task")),
            "consumer_modes": {
                "external_llm": True,
                "augment": True,
            },
            "augment_usage": {
                "summary": "Augment can read this request bundle directly and act under repository safety rules.",
                "notes": [
                    "Suggested changes are advisory until explicitly applied.",
                    "Things mutations remain subject to the existing dry-run or explicit execution controls.",
                ],
            },
            "request": {
                "instruction": instruction.strip(),
                "system": system,
                "prompt": prompt,
                "requested_model": requested_model,
                "resolved_model": resolved_model,
                "max_tokens": token_limit,
                "response_contract": build_task_decision_response_contract(),
            },
            "payload": payload,
        }
    )


def load_task_request_bundle(path: Path) -> dict[str, Any]:
    request_bundle = load_json_object(path, label="task request bundle")
    validate_task_request_bundle(request_bundle)
    return request_bundle


def parse_task_decision(text: str) -> dict[str, Any]:
    decision = parse_json_object(text, label="task decision")
    validate_task_decision(decision)
    return decision


def build_task_action_proposals(
    request_bundle: dict[str, Any],
    decision: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None = None,
    command_text: str | None = None,
) -> dict[str, Any]:
    validate_task_request_bundle(request_bundle)
    validate_task_decision(decision)
    snapshot = snapshot or fetch_snapshot(command_text=command_text)
    proposals = [
        build_single_task_action_proposal(snapshot, request_bundle=request_bundle, suggestion=suggestion, index=index)
        for index, suggestion in enumerate(decision["suggested_changes"], start=1)
    ]
    counts = {
        "ready": sum(1 for item in proposals if item.get("status") == "ready"),
        "error": sum(1 for item in proposals if item.get("status") == "error"),
        "skipped": sum(1 for item in proposals if item.get("status") == "skipped"),
    }
    return compact(
        {
            "schema_version": TASK_PROPOSAL_SCHEMA_VERSION,
            "proposal_kind": "task-action-proposals",
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "request_bundle": compact(
                {
                    "schema_version": request_bundle.get("schema_version"),
                    "request_kind": request_bundle.get("request_kind"),
                    "generated_at": request_bundle.get("generated_at"),
                    "selection": ensure_dict(request_bundle.get("selection")),
                    "task": ensure_dict(request_bundle.get("task")),
                }
            ),
            "decision": compact(
                {
                    "schema_version": decision.get("schema_version"),
                    "kind": decision.get("kind"),
                    "summary": decision.get("summary"),
                    "recommended_action": decision.get("recommended_action"),
                    "reasoning": decision.get("reasoning"),
                    "questions": decision.get("questions"),
                    "risks": decision.get("risks"),
                }
            ),
            "counts": counts,
            "proposals": proposals,
        }
    )


def build_single_task_action_proposal(
    snapshot: dict[str, Any],
    *,
    request_bundle: dict[str, Any],
    suggestion: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    kind = str(suggestion.get("kind") or "none")
    proposal = compact(
        {
            "index": index,
            "kind": kind,
            "target_kind": suggestion.get("target_kind"),
            "reason": suggestion.get("reason"),
            "suggestion": suggestion,
        }
    )
    if kind == "none":
        proposal["status"] = "skipped"
        proposal["message"] = "Decision explicitly requested no action proposal."
        return proposal
    try:
        prepared_request, notes = interpret_suggested_change(snapshot, request_bundle=request_bundle, suggestion=suggestion)
    except (SelectionError, ValueError) as exc:
        proposal["status"] = "error"
        proposal["error"] = str(exc)
        return proposal
    proposal["status"] = "ready"
    proposal["prepared_request"] = prepared_request
    proposal["command_handoff"] = build_command_handoff(prepared_request)
    if notes:
        proposal["notes"] = notes
    return proposal


def build_command_handoff(prepared_request: dict[str, Any]) -> dict[str, Any]:
    dry_run_argv = list(REPO_LOCAL_CLI_COMMAND) + build_prepared_request_cli_args(prepared_request)
    apply_argv = dry_run_argv + ["--apply"]
    return {
        "review_required": True,
        "run_from_repo_root": True,
        "env": dict(REPO_LOCAL_CLI_ENV),
        "notes": [
            "Run the dry-run command first.",
            "Only use the --apply command after human review.",
        ],
        "dry_run": {
            "argv": dry_run_argv,
            "shell": render_shell_command(dry_run_argv),
        },
        "apply": {
            "argv": apply_argv,
            "shell": render_shell_command(apply_argv),
        },
    }


def build_prepared_request_cli_args(prepared_request: dict[str, Any]) -> list[str]:
    tool = str(prepared_request.get("tool") or "")
    arguments = ensure_dict(prepared_request.get("arguments"))
    target = ensure_dict(prepared_request.get("target"))
    if tool == "add_todo":
        title = string_value(arguments.get("title"))
        if not title:
            raise ValueError("add_todo prepared_request is missing title")
        tokens = ["create-task", "--title", title]
        append_optional_cli_value(tokens, "--notes", arguments.get("notes"))
        append_optional_cli_value(tokens, "--when", arguments.get("when"))
        append_optional_cli_value(tokens, "--deadline", arguments.get("deadline"))
        append_repeated_cli_values(tokens, "--tag", arguments.get("tags"))
        append_repeated_cli_values(tokens, "--checklist-item", arguments.get("checklist_items"))
        append_target_context_selectors(tokens, target)
        return tokens
    if tool == "update_todo":
        tokens = ["update-task"]
        todo = ensure_dict(target.get("todo"))
        todo_uuid = first_non_empty(todo.get("uuid"), arguments.get("id"))
        todo_title = first_non_empty(todo.get("title"))
        append_selector_tokens(tokens, "--todo-uuid", "--todo-title", uuid=todo_uuid, title=todo_title)
        if len(tokens) == 1:
            raise ValueError("update_todo prepared_request is missing a todo selector")
        append_optional_cli_value(tokens, "--title", arguments.get("title"))
        append_optional_cli_value(tokens, "--notes", arguments.get("notes"))
        append_optional_cli_value(tokens, "--when", arguments.get("when"))
        append_optional_cli_value(tokens, "--deadline", arguments.get("deadline"))
        append_repeated_cli_values(tokens, "--tag", arguments.get("tags"))
        append_bool_cli_flag(tokens, true_flag="--completed", false_flag="--not-completed", value=arguments.get("completed"))
        append_bool_cli_flag(tokens, true_flag="--canceled", false_flag="--not-canceled", value=arguments.get("canceled"))
        append_selector_from_item(tokens, "--area-uuid", "--area-title", ensure_dict(target.get("area")))
        append_selector_from_item(tokens, "--project-uuid", "--project-title", ensure_dict(target.get("project")))
        append_selector_from_item(tokens, "--heading-uuid", "--heading-title", ensure_dict(target.get("heading")))
        append_selector_from_item(tokens, "--move-area-uuid", "--move-area-title", resolve_move_area(target))
        append_selector_from_item(tokens, "--move-project-uuid", "--move-project-title", resolve_move_project(target))
        append_selector_from_item(tokens, "--move-heading-uuid", "--move-heading-title", resolve_move_heading(target))
        return tokens
    if tool == "update_project":
        tokens = ["update-project"]
        project = ensure_dict(target.get("project"))
        project_uuid = first_non_empty(project.get("uuid"), arguments.get("id"))
        project_title = first_non_empty(project.get("title"))
        append_selector_tokens(tokens, "--project-uuid", "--project-title", uuid=project_uuid, title=project_title)
        if len(tokens) == 1:
            raise ValueError("update_project prepared_request is missing a project selector")
        append_optional_cli_value(tokens, "--title", arguments.get("title"))
        append_optional_cli_value(tokens, "--notes", arguments.get("notes"))
        append_optional_cli_value(tokens, "--when", arguments.get("when"))
        append_optional_cli_value(tokens, "--deadline", arguments.get("deadline"))
        append_repeated_cli_values(tokens, "--tag", arguments.get("tags"))
        append_bool_cli_flag(tokens, true_flag="--completed", false_flag="--not-completed", value=arguments.get("completed"))
        append_bool_cli_flag(tokens, true_flag="--canceled", false_flag="--not-canceled", value=arguments.get("canceled"))
        append_selector_from_item(tokens, "--area-uuid", "--area-title", ensure_dict(target.get("area")))
        return tokens
    raise ValueError(f"Unsupported prepared_request tool for command handoff: {tool}")


def append_target_context_selectors(tokens: list[str], target: dict[str, Any]) -> None:
    area = ensure_dict(target.get("area"))
    project = ensure_dict(target.get("project"))
    heading = ensure_dict(target.get("heading"))
    list_target = ensure_dict(target.get("list"))
    if not area and list_target.get("kind") == "area":
        area = list_target
    if not project and list_target.get("kind") == "project":
        project = list_target
    append_selector_from_item(tokens, "--area-uuid", "--area-title", area)
    append_selector_from_item(tokens, "--project-uuid", "--project-title", project)
    append_selector_from_item(tokens, "--heading-uuid", "--heading-title", heading)


def resolve_move_area(target: dict[str, Any]) -> dict[str, Any]:
    move_area = ensure_dict(target.get("move_area"))
    list_target = ensure_dict(target.get("list"))
    if move_area:
        return move_area
    if list_target.get("kind") == "area":
        return list_target
    return {}


def resolve_move_project(target: dict[str, Any]) -> dict[str, Any]:
    move_project = ensure_dict(target.get("move_project"))
    list_target = ensure_dict(target.get("list"))
    if move_project:
        return move_project
    if list_target.get("kind") == "project":
        return list_target
    return {}


def resolve_move_heading(target: dict[str, Any]) -> dict[str, Any]:
    return ensure_dict(target.get("move_heading"))


def append_selector_from_item(tokens: list[str], uuid_flag: str, title_flag: str, item: dict[str, Any]) -> None:
    append_selector_tokens(
        tokens,
        uuid_flag,
        title_flag,
        uuid=first_non_empty(item.get("uuid")),
        title=first_non_empty(item.get("title")),
    )


def append_selector_tokens(tokens: list[str], uuid_flag: str, title_flag: str, *, uuid: str | None, title: str | None) -> None:
    if uuid:
        tokens.extend([uuid_flag, uuid])
        return
    if title:
        tokens.extend([title_flag, title])


def append_optional_cli_value(tokens: list[str], flag: str, value: Any) -> None:
    text = string_value(value)
    if text:
        tokens.extend([flag, text])


def append_repeated_cli_values(tokens: list[str], flag: str, value: Any) -> None:
    items = string_list_value(value, field_name=flag) or []
    for item in items:
        tokens.extend([flag, item])


def append_bool_cli_flag(tokens: list[str], *, true_flag: str, false_flag: str, value: Any) -> None:
    bool_result = bool_value(value, field_name=true_flag)
    if bool_result is True:
        tokens.append(true_flag)
    elif bool_result is False:
        tokens.append(false_flag)


def render_shell_command(argv: list[str]) -> str:
    env_prefix = " ".join(f"{key}={shlex.quote(str(value))}" for key, value in REPO_LOCAL_CLI_ENV.items())
    return f"{env_prefix} {shlex.join(argv)}"


def interpret_suggested_change(
    snapshot: dict[str, Any], *, request_bundle: dict[str, Any], suggestion: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    kind = str(suggestion.get("kind") or "none")
    defaults = default_request_selectors(request_bundle)
    notes: list[str] = []
    if kind == "create_task":
        title = string_value(suggestion.get("title"))
        if not title:
            raise ValueError("create_task suggestion requires title")
        area_uuid, area_title = choose_selector(
            suggestion,
            uuid_key="area_uuid",
            title_key="area_title",
            default_uuid=defaults.get("area_uuid"),
            default_title=defaults.get("area_title"),
        )
        project_uuid, project_title = choose_selector(
            suggestion,
            uuid_key="project_uuid",
            title_key="project_title",
            default_uuid=defaults.get("project_uuid"),
            default_title=defaults.get("project_title"),
        )
        heading_uuid, heading_title = choose_selector(
            suggestion,
            uuid_key="heading_uuid",
            title_key="heading_title",
            default_uuid=defaults.get("heading_uuid"),
            default_title=defaults.get("heading_title"),
        )
        return (
            prepare_create_todo_request(
                snapshot,
                title=title,
                notes=string_value(suggestion.get("notes")),
                when=string_value(suggestion.get("when")),
                deadline=string_value(suggestion.get("deadline")),
                tags=string_list_value(suggestion.get("tags"), field_name="tags"),
                checklist_items=string_list_value(suggestion.get("checklist_items"), field_name="checklist_items"),
                area_uuid=area_uuid,
                area_title=area_title,
                project_uuid=project_uuid,
                project_title=project_title,
                heading_uuid=heading_uuid,
                heading_title=heading_title,
            ),
            notes,
        )
    if kind == "update_task":
        todo_uuid, todo_title = choose_target_selector(
            suggestion,
            default_uuid=defaults.get("todo_uuid"),
            default_title=defaults.get("todo_title"),
        )
        area_uuid, area_title = choose_selector(
            suggestion,
            uuid_key="area_uuid",
            title_key="area_title",
            default_uuid=defaults.get("area_uuid"),
            default_title=defaults.get("area_title"),
        )
        project_uuid, project_title = choose_selector(
            suggestion,
            uuid_key="project_uuid",
            title_key="project_title",
            default_uuid=defaults.get("project_uuid"),
            default_title=defaults.get("project_title"),
        )
        heading_uuid, heading_title = choose_selector(
            suggestion,
            uuid_key="heading_uuid",
            title_key="heading_title",
            default_uuid=defaults.get("heading_uuid"),
            default_title=defaults.get("heading_title"),
        )
        return (
            prepare_update_todo_request(
                snapshot,
                todo_uuid=todo_uuid,
                todo_title=todo_title,
                title=string_value(suggestion.get("title")),
                notes=string_value(suggestion.get("notes")),
                when=string_value(suggestion.get("when")),
                deadline=string_value(suggestion.get("deadline")),
                tags=string_list_value(suggestion.get("tags"), field_name="tags"),
                completed=bool_value(suggestion.get("completed"), field_name="completed"),
                canceled=bool_value(suggestion.get("canceled"), field_name="canceled"),
                area_uuid=area_uuid,
                area_title=area_title,
                project_uuid=project_uuid,
                project_title=project_title,
                heading_uuid=heading_uuid,
                heading_title=heading_title,
            ),
            notes,
        )
    if kind == "update_project":
        project_uuid, project_title = choose_target_selector(
            suggestion,
            default_uuid=defaults.get("project_uuid"),
            default_title=defaults.get("project_title"),
        )
        area_uuid, area_title = choose_selector(
            suggestion,
            uuid_key="area_uuid",
            title_key="area_title",
            default_uuid=defaults.get("area_uuid"),
            default_title=defaults.get("area_title"),
        )
        return (
            prepare_update_project_request(
                snapshot,
                project_uuid=project_uuid,
                project_title=project_title,
                title=string_value(suggestion.get("title")),
                notes=string_value(suggestion.get("notes")),
                when=string_value(suggestion.get("when")),
                deadline=string_value(suggestion.get("deadline")),
                tags=string_list_value(suggestion.get("tags"), field_name="tags"),
                completed=bool_value(suggestion.get("completed"), field_name="completed"),
                canceled=bool_value(suggestion.get("canceled"), field_name="canceled"),
                area_uuid=area_uuid,
                area_title=area_title,
            ),
            notes,
        )
    if kind == "add_note":
        note_text = string_value(suggestion.get("notes"))
        if not note_text:
            raise ValueError("add_note suggestion requires notes")
        target_kind = str(suggestion.get("target_kind") or "todo")
        if target_kind == "project":
            project_uuid, project_title = choose_target_selector(
                suggestion,
                default_uuid=defaults.get("project_uuid"),
                default_title=defaults.get("project_title"),
            )
            area_uuid, area_title = choose_selector(
                suggestion,
                uuid_key="area_uuid",
                title_key="area_title",
                default_uuid=defaults.get("area_uuid"),
                default_title=defaults.get("area_title"),
            )
            notes.append("add_note previews as a notes replacement via update_project")
            return (
                prepare_update_project_request(
                    snapshot,
                    project_uuid=project_uuid,
                    project_title=project_title,
                    notes=note_text,
                    area_uuid=area_uuid,
                    area_title=area_title,
                ),
                notes,
            )
        todo_uuid, todo_title = choose_target_selector(
            suggestion,
            default_uuid=defaults.get("todo_uuid"),
            default_title=defaults.get("todo_title"),
        )
        area_uuid, area_title = choose_selector(
            suggestion,
            uuid_key="area_uuid",
            title_key="area_title",
            default_uuid=defaults.get("area_uuid"),
            default_title=defaults.get("area_title"),
        )
        project_uuid, project_title = choose_selector(
            suggestion,
            uuid_key="project_uuid",
            title_key="project_title",
            default_uuid=defaults.get("project_uuid"),
            default_title=defaults.get("project_title"),
        )
        heading_uuid, heading_title = choose_selector(
            suggestion,
            uuid_key="heading_uuid",
            title_key="heading_title",
            default_uuid=defaults.get("heading_uuid"),
            default_title=defaults.get("heading_title"),
        )
        notes.append("add_note previews as a notes replacement via update_todo")
        return (
            prepare_update_todo_request(
                snapshot,
                todo_uuid=todo_uuid,
                todo_title=todo_title,
                notes=note_text,
                area_uuid=area_uuid,
                area_title=area_title,
                project_uuid=project_uuid,
                project_title=project_title,
                heading_uuid=heading_uuid,
                heading_title=heading_title,
            ),
            notes,
        )
    raise ValueError(f"Unsupported suggestion kind: {kind}")


def complete(
    prompt: str,
    *,
    model: str | None = None,
    system: str | None = None,
    max_tokens: int | None = None,
    env_path: Path | None = None,
    execute: bool = False,
    transport: Any = None,
) -> dict[str, Any]:
    config = resolve_llm_config(env_path=env_path)
    requested_model = model or config["default_model"]
    resolved_model = resolve_model_name(requested_model, config=config)
    provider = infer_provider(resolved_model)
    prompt = str(prompt)
    system = str(system or "")
    token_limit = max_tokens if max_tokens is not None else int(config["max_tokens"])
    env_values = load_dotenv_values(env_path or DEFAULT_ENV_PATH)
    api_key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    api_key = env_value(api_key_name, env_values)
    endpoint, body = build_provider_request_body(
        provider,
        model=resolved_model,
        prompt=prompt,
        system=system,
        max_tokens=token_limit,
    )
    request_preview = {
        "endpoint": endpoint,
        "body": body,
        "api_key_env": api_key_name,
        "api_key_present": bool(api_key),
    }
    result = {
        "dry_run": not execute,
        "provider": provider,
        "requested_model": requested_model,
        "resolved_model": resolved_model,
        "max_tokens": token_limit,
        "prompt_chars": len(prompt),
        "system_chars": len(system),
        "request_preview": request_preview,
    }
    if not execute:
        return result
    if not api_key:
        raise ValueError(f"{api_key_name} is required to execute a real LLM call")
    headers = build_provider_headers(provider, api_key)
    transport = transport or default_http_transport
    raw_response = transport(endpoint, headers, body)
    return {
        **result,
        "dry_run": False,
        "response_text": extract_provider_response_text(provider, raw_response),
        "raw_response": raw_response,
    }


def write_task_context_artifacts(
    payload: dict[str, Any], *, output_root: Path | None = None, config: dict[str, Any] | None = None
) -> dict[str, Path]:
    root = resolve_output_root(output_root=output_root, config=config)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = slugify(
        payload.get("task", {}).get("title") or payload.get("selection", {}).get("todo", {}).get("uuid") or "task"
    )
    stem = f"{timestamp_slug()}_{slug}"
    target_dir = root / "task-context" / day
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / f"{stem}.json"
    markdown_path = target_dir / f"{stem}.md"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_task_context_markdown(payload), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def write_task_request_artifacts(
    request_bundle: dict[str, Any], *, output_root: Path | None = None, config: dict[str, Any] | None = None
) -> dict[str, Path]:
    root = resolve_output_root(output_root=output_root, config=config)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = slugify(
        request_bundle.get("task", {}).get("title")
        or request_bundle.get("selection", {}).get("todo", {}).get("uuid")
        or "task-request"
    )
    stem = f"{timestamp_slug()}_{slug}"
    target_dir = root / "task-requests" / day
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / f"{stem}.json"
    markdown_path = target_dir / f"{stem}.md"
    json_path.write_text(json.dumps(request_bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_task_request_markdown(request_bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def write_task_proposal_artifacts(
    proposal_bundle: dict[str, Any], *, output_root: Path | None = None, config: dict[str, Any] | None = None
) -> dict[str, Path]:
    root = resolve_output_root(output_root=output_root, config=config)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    request_bundle = ensure_dict(proposal_bundle.get("request_bundle"))
    task = ensure_dict(request_bundle.get("task"))
    selection = ensure_dict(request_bundle.get("selection"))
    slug = slugify(task.get("title") or ensure_dict(selection.get("todo")).get("uuid") or "task-proposal")
    stem = f"{timestamp_slug()}_{slug}"
    target_dir = root / "task-proposals" / day
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / f"{stem}.json"
    markdown_path = target_dir / f"{stem}.md"
    json_path.write_text(json.dumps(proposal_bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_task_proposal_markdown(proposal_bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def write_llm_debug_log(
    *,
    requested_model: str | None = None,
    resolved_model: str | None = None,
    system: str = "",
    prompt: str = "",
    response: str = "",
    actions: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    payload_path: Path | None = None,
    request_path: Path | None = None,
    output_root: Path | None = None,
    log_path: Path | None = None,
    config: dict[str, Any] | None = None,
) -> Path:
    config = config or resolve_llm_config()
    requested_model = requested_model or config["default_model"]
    resolved_model = resolved_model or resolve_model_name(requested_model, config=config)
    actions = actions or []
    path = log_path or default_log_path(resolved_model, output_root=output_root, config=config)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Things AI LLM Debug Log",
        "",
        f"- Timestamp: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%SZ')}",
        f"- Requested model: {requested_model}",
        f"- Resolved model: {resolved_model}",
        f"- System chars: {len(system)}",
        f"- Prompt chars: {len(prompt)}",
        f"- Response chars: {len(response)}",
        f"- Action count: {len(actions)}",
    ]
    if payload_path is not None:
        lines.append(f"- Payload artifact: {payload_path}")
    if request_path is not None:
        lines.append(f"- Request artifact: {request_path}")
    if payload:
        selection = ensure_dict(payload.get("selection"))
        task = ensure_dict(selection.get("todo"))
        project = ensure_dict(selection.get("project"))
        if task:
            lines.append(f"- Task: {task.get('title') or task.get('uuid')}")
        if project:
            lines.append(f"- Project: {project.get('title') or project.get('uuid')}")
    lines.extend(
        [
            "",
            "## System Prompt",
            "",
            f"````text\n{system}\n````",
            "",
            "## User Prompt",
            "",
            f"````text\n{prompt}\n````",
            "",
            "## Response",
            "",
            f"````text\n{response}\n````",
            "",
            "## Actions",
            "",
        ]
    )
    lines.extend(f"- {action}" for action in actions)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def build_project_context(
    project: dict[str, Any] | None, *, selected_todo_uuid: str | None, include_area: bool
) -> dict[str, Any] | None:
    if not isinstance(project, dict):
        return None
    headings = select_child_path(project, "headings")
    return compact(
        {
            "project": summarize_item(project, include_area=include_area),
            "project_todos": summarize_todo_list(ensure_list(ensure_dict(project.get("children")).get("todos")), selected_todo_uuid),
            "headings": [
                compact(
                    {
                        "heading": summarize_item(heading, include_area=include_area),
                        "todos": summarize_todo_list(
                            ensure_list(ensure_dict(heading.get("children")).get("todos")),
                            selected_todo_uuid,
                        ),
                    }
                )
                for heading in headings
            ],
        }
    )


def summarize_todo_list(items: list[dict[str, Any]], selected_todo_uuid: str | None) -> list[dict[str, Any]]:
    return [
        compact(
            {
                **summarize_item(item, include_area=False),
                "selected": bool(selected_todo_uuid and item.get("uuid") == selected_todo_uuid),
            }
        )
        for item in items
        if isinstance(item, dict)
    ]


def summarize_item(item: dict[str, Any] | None, *, include_area: bool) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    relationships = ensure_dict(item.get("relationships"))
    if not include_area:
        relationships = {key: value for key, value in relationships.items() if not key.startswith("area_")}
    return compact(
        {
            "kind": item.get("kind"),
            "uuid": item.get("uuid"),
            "title": item.get("title"),
            "status": item.get("status"),
            "notes": item.get("notes"),
            "when": item.get("when"),
            "deadline": item.get("deadline"),
            "checklist_item_count": item.get("checklist_item_count"),
            "child_counts": item.get("child_counts"),
            "relationships": relationships,
        }
    )


def render_task_context_markdown(payload: dict[str, Any]) -> str:
    task = ensure_dict(payload.get("task"))
    project_context = ensure_dict(payload.get("project_context"))
    project = ensure_dict(project_context.get("project"))
    lines = [
        "# Things Task Context",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Snapshot generated at: {payload.get('snapshot_generated_at')}",
        f"- Task: {task.get('title') or task.get('uuid')}",
    ]
    if project:
        lines.append(f"- Project: {project.get('title') or project.get('uuid')}")
    lines.extend(["", "## Task", "", json.dumps(task, indent=2, sort_keys=True)])
    if project_context:
        lines.extend(["", "## Project Context", "", json.dumps(project_context, indent=2, sort_keys=True)])
    if payload.get("area"):
        lines.extend(["", "## Area", "", json.dumps(payload["area"], indent=2, sort_keys=True)])
    return "\n".join(lines).rstrip() + "\n"


def render_task_request_markdown(request_bundle: dict[str, Any]) -> str:
    request = ensure_dict(request_bundle.get("request"))
    task = ensure_dict(request_bundle.get("task"))
    lines = [
        "# Things AI Task Request",
        "",
        f"- Generated at: {request_bundle.get('generated_at')}",
        f"- Task: {task.get('title') or task.get('uuid')}",
        f"- Requested model: {request.get('requested_model')}",
        f"- Resolved model: {request.get('resolved_model')}",
        f"- Max tokens: {request.get('max_tokens')}",
        "",
        "## Consumer Options",
        "",
        "- External LLM: use the system prompt, rendered prompt, and response contract below.",
        "- Augment: ask Augment to read this file and act on the request under the repo's existing safety rules.",
        "",
        "## System Prompt",
        "",
        request.get("system") or "",
        "",
        "## Instruction",
        "",
        request.get("instruction") or "",
        "",
        "## Rendered Prompt",
        "",
        request.get("prompt") or "",
        "",
        "## Response Contract",
        "",
        json.dumps(request.get("response_contract", {}), indent=2, sort_keys=True),
        "",
        "## Task Context Payload",
        "",
        json.dumps(request_bundle.get("payload", {}), indent=2, sort_keys=True),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_task_proposal_markdown(proposal_bundle: dict[str, Any]) -> str:
    request_bundle = ensure_dict(proposal_bundle.get("request_bundle"))
    decision = ensure_dict(proposal_bundle.get("decision"))
    counts = ensure_dict(proposal_bundle.get("counts"))
    task = ensure_dict(request_bundle.get("task"))
    lines = [
        "# Things AI Task Proposals",
        "",
        f"- Generated at: {proposal_bundle.get('generated_at')}",
        f"- Task: {task.get('title') or task.get('uuid')}",
        f"- Recommended action: {decision.get('recommended_action')}",
        f"- Ready proposals: {counts.get('ready', 0)}",
        f"- Proposal errors: {counts.get('error', 0)}",
        f"- Skipped proposals: {counts.get('skipped', 0)}",
        "",
        "## Decision Summary",
        "",
        str(decision.get("summary") or ""),
        "",
        "## Reasoning",
        "",
        str(decision.get("reasoning") or ""),
    ]
    questions = decision.get("questions")
    if isinstance(questions, list):
        lines.extend(["", "## Questions", ""])
        lines.extend(f"- {item}" for item in questions)
    risks = decision.get("risks")
    if isinstance(risks, list):
        lines.extend(["", "## Risks", ""])
        lines.extend(f"- {item}" for item in risks)
    for proposal in proposal_bundle.get("proposals", []):
        if not isinstance(proposal, dict):
            continue
        lines.extend(
            [
                "",
                f"## Proposal {proposal.get('index')} — {proposal.get('kind')}",
                "",
                f"- Status: {proposal.get('status')}",
                f"- Target kind: {proposal.get('target_kind')}",
                f"- Reason: {proposal.get('reason')}",
            ]
        )
        if proposal.get("notes"):
            lines.extend(["- Notes:"])
            lines.extend(f"  - {item}" for item in proposal.get("notes", []) if item not in (None, ""))
        if proposal.get("error"):
            lines.extend(["", "### Error", "", str(proposal.get("error"))])
            continue
        if proposal.get("prepared_request"):
            lines.extend(
                [
                    "",
                    "### Prepared Request",
                    "",
                    json.dumps(proposal.get("prepared_request"), indent=2, sort_keys=True),
                ]
            )
        handoff = ensure_dict(proposal.get("command_handoff"))
        if handoff:
            dry_run = ensure_dict(handoff.get("dry_run"))
            apply = ensure_dict(handoff.get("apply"))
            lines.extend(
                [
                    "",
                    "### Review Handoff",
                    "",
                    f"- Run from repo root: {handoff.get('run_from_repo_root')}",
                    f"- Review required: {handoff.get('review_required')}",
                ]
            )
            handoff_notes = handoff.get("notes")
            if isinstance(handoff_notes, list):
                lines.extend(f"- Note: {item}" for item in handoff_notes if item not in (None, ""))
            if dry_run.get("shell"):
                lines.extend(["", "#### Dry Run Command", "", str(dry_run.get("shell"))])
            if apply.get("shell"):
                lines.extend(["", "#### Apply Command", "", str(apply.get("shell"))])
    return "\n".join(lines).rstrip() + "\n"


def default_log_path(resolved_model: str, *, output_root: Path | None, config: dict[str, Any] | None) -> Path:
    root = resolve_output_root(output_root=output_root, config=config)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d/%H%M%S")
    return root / "logs" / "llm" / f"{stamp}_{slugify(resolved_model)}.md"


def resolve_output_root(*, output_root: Path | None, config: dict[str, Any] | None) -> Path:
    if output_root is not None:
        return output_root
    config = config or resolve_llm_config()
    return Path(config["artifact_root"])


def resolve_repo_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def env_value(name: str, dotenv_values: dict[str, str]) -> str | None:
    return os.getenv(name) or dotenv_values.get(name)


def infer_provider(model_name: str) -> str:
    if model_name.startswith("claude"):
        return "anthropic"
    if model_name.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    raise ValueError(f"Cannot infer provider from model name: {model_name}")


def build_provider_request_body(
    provider: str, *, model: str, prompt: str, system: str, max_tokens: int
) -> tuple[str, dict[str, Any]]:
    if provider == "anthropic":
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        return ANTHROPIC_API_URL, body
    if provider == "openai":
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return OPENAI_API_URL, {"model": model, "messages": messages, "max_tokens": max_tokens}
    raise ValueError(f"Unsupported provider: {provider}")


def build_provider_headers(provider: str, api_key: str) -> dict[str, str]:
    if provider == "anthropic":
        return {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
        }
    if provider == "openai":
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        }
    raise ValueError(f"Unsupported provider: {provider}")


def default_http_transport(endpoint: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_provider_response_text(provider: str, response: dict[str, Any]) -> str:
    if provider == "anthropic":
        return extract_anthropic_response_text(response)
    if provider == "openai":
        return extract_openai_response_text(response)
    raise ValueError(f"Unsupported provider: {provider}")


def extract_anthropic_response_text(response: dict[str, Any]) -> str:
    blocks = response.get("content", [])
    if not isinstance(blocks, list):
        return ""
    texts = [str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("text")]
    return "\n".join(texts).strip()


def extract_openai_response_text(response: dict[str, Any]) -> str:
    choices = response.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("text")]
        return "\n".join(texts).strip()
    return ""


def relationship_selector(relationships: dict[str, Any], prefix: str) -> dict[str, Any]:
    return compact({"uuid": relationships.get(f"{prefix}_uuid"), "title": relationships.get(f"{prefix}_title")})


def validate_task_request_bundle(request_bundle: dict[str, Any]) -> None:
    if not isinstance(request_bundle, dict):
        raise ValueError("task request bundle must be a JSON object")
    if request_bundle.get("schema_version") != TASK_REQUEST_SCHEMA_VERSION:
        raise ValueError(f"Unsupported task request bundle schema_version: {request_bundle.get('schema_version')}")
    if request_bundle.get("request_kind") != "task-decision":
        raise ValueError(f"Unsupported task request bundle request_kind: {request_bundle.get('request_kind')}")
    if not isinstance(request_bundle.get("request"), dict):
        raise ValueError("task request bundle is missing request metadata")


def validate_task_decision(decision: dict[str, Any]) -> None:
    if not isinstance(decision, dict):
        raise ValueError("task decision must be a JSON object")
    schema_version = decision.get("schema_version")
    if schema_version not in (None, "", TASK_DECISION_RESPONSE_SCHEMA_VERSION):
        raise ValueError(f"Unsupported task decision schema_version: {schema_version}")
    kind = decision.get("kind")
    if kind not in (None, "", "task-decision"):
        raise ValueError(f"Unsupported task decision kind: {kind}")
    for field_name in ["summary", "recommended_action", "reasoning", "suggested_changes", "questions", "risks"]:
        if field_name not in decision:
            raise ValueError(f"task decision is missing required field: {field_name}")
    if not isinstance(decision.get("suggested_changes"), list):
        raise ValueError("task decision suggested_changes must be a list")
    if not isinstance(decision.get("questions"), list):
        raise ValueError("task decision questions must be a list")
    if not isinstance(decision.get("risks"), list):
        raise ValueError("task decision risks must be a list")
    for index, suggestion in enumerate(decision.get("suggested_changes", []), start=1):
        if not isinstance(suggestion, dict):
            raise ValueError(f"task decision suggested_changes[{index}] must be an object")


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} file is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def extract_embedded_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    best_value: dict[str, Any] | None = None
    best_length = -1
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, parsed_length = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and parsed_length > best_length:
            best_value = value
            best_length = parsed_length
    return best_value


def parse_json_object(text: str, *, label: str) -> dict[str, Any]:
    cleaned = text.strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        value = extract_embedded_json_object(cleaned)
        if value is None:
            raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def default_request_selectors(request_bundle: dict[str, Any]) -> dict[str, str | None]:
    selection = ensure_dict(request_bundle.get("selection"))
    task = ensure_dict(request_bundle.get("task"))
    relationships = ensure_dict(task.get("relationships"))
    return {
        "todo_uuid": first_non_empty(ensure_dict(selection.get("todo")).get("uuid"), task.get("uuid")),
        "todo_title": first_non_empty(ensure_dict(selection.get("todo")).get("title"), task.get("title")),
        "area_uuid": first_non_empty(ensure_dict(selection.get("area")).get("uuid"), relationships.get("area_uuid")),
        "area_title": first_non_empty(ensure_dict(selection.get("area")).get("title"), relationships.get("area_title")),
        "project_uuid": first_non_empty(
            ensure_dict(selection.get("project")).get("uuid"), relationships.get("project_uuid")
        ),
        "project_title": first_non_empty(
            ensure_dict(selection.get("project")).get("title"), relationships.get("project_title")
        ),
        "heading_uuid": first_non_empty(
            ensure_dict(selection.get("heading")).get("uuid"), relationships.get("heading_uuid")
        ),
        "heading_title": first_non_empty(
            ensure_dict(selection.get("heading")).get("title"), relationships.get("heading_title")
        ),
    }


def choose_target_selector(
    suggestion: dict[str, Any], *, default_uuid: str | None, default_title: str | None
) -> tuple[str | None, str | None]:
    return choose_selector(
        suggestion,
        uuid_key="target_uuid",
        title_key="target_title",
        default_uuid=default_uuid,
        default_title=default_title,
    )


def choose_selector(
    suggestion: dict[str, Any],
    *,
    uuid_key: str,
    title_key: str,
    default_uuid: str | None,
    default_title: str | None,
) -> tuple[str | None, str | None]:
    explicit_uuid = string_value(suggestion.get(uuid_key))
    explicit_title = string_value(suggestion.get(title_key))
    if explicit_uuid is None and explicit_title is None:
        return default_uuid, default_title
    return explicit_uuid, explicit_title


def string_value(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def string_list_value(value: Any, *, field_name: str) -> list[str] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    items = [str(item) for item in value if item not in (None, "")]
    return items or None


def bool_value(value: Any, *, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return None


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def ensure_list(value: Any) -> list[dict[str, Any]]:
    return value if isinstance(value, list) else []


def slugify(value: str) -> str:
    cleaned = [ch.lower() if ch.isalnum() else "-" for ch in value]
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    if len(slug) > DEFAULT_SLUG_MAX_LENGTH:
        slug = slug[:DEFAULT_SLUG_MAX_LENGTH].rstrip("-")
    return slug or "artifact"