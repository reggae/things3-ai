from __future__ import annotations

import argparse
import json
from pathlib import Path

from .control import create_project, create_todo, update_project, update_todo
from .intake import propose_intake_packet, review_next_packet, start_intake_session
from .inbox_review import review_inbox_answer_file, write_inbox_answer_review_artifacts
from .llm_bridge import (
    build_task_action_proposals,
    build_task_request_bundle,
    build_task_context_prompt,
    complete,
    fetch_task_context_payload,
    load_task_request_bundle,
    parse_task_decision,
    resolve_llm_config,
    resolve_model_name,
    write_llm_debug_log,
    write_task_proposal_artifacts,
    write_task_request_artifacts,
    write_task_context_artifacts,
)
from .mcp import StdioMcpClient
from .snapshot import (
    archive_snapshot,
    export_snapshot,
    fetch_inbox_question_set,
    plan_restore,
    write_inbox_question_set_artifacts,
)
from .tasks import accept_task, list_tasks, next_task, open_task, review_task, show_task


def add_common_update_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--title", help="Updated title")
    parser.add_argument("--notes", help="Updated notes")
    parser.add_argument("--when", help="Updated schedule value")
    parser.add_argument("--deadline", help="Updated deadline in YYYY-MM-DD format")
    parser.add_argument("--tag", dest="tags", action="append", default=[], help="Replace tags with the provided list")
    parser.add_argument("--completed", dest="completed", action="store_true", help="Mark as completed")
    parser.add_argument("--not-completed", dest="completed", action="store_false", help="Mark as not completed")
    parser.add_argument("--canceled", dest="canceled", action="store_true", help="Mark as canceled")
    parser.add_argument("--not-canceled", dest="canceled", action="store_false", help="Mark as not canceled")
    parser.set_defaults(completed=None, canceled=None)


def add_apply_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually perform the mutation. Without this flag the command is a dry-run preview.",
    )
    parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")


def add_context_selector_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--todo-uuid", help="Canonical todo UUID")
    parser.add_argument("--todo-title", help="Canonical todo title")
    parser.add_argument("--area-uuid", help="Canonical area UUID")
    parser.add_argument("--area-title", help="Canonical area title")
    parser.add_argument("--project-uuid", help="Canonical project UUID")
    parser.add_argument("--project-title", help="Canonical project title")
    parser.add_argument("--heading-uuid", help="Canonical heading UUID within the selected project")
    parser.add_argument("--heading-title", help="Canonical heading title within the selected project")


def add_move_selector_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--move-area-uuid", help="Destination area UUID for moving the todo")
    parser.add_argument("--move-area-title", help="Destination area title for moving the todo")
    parser.add_argument("--move-project-uuid", help="Destination project UUID for moving the todo")
    parser.add_argument("--move-project-title", help="Destination project title for moving the todo")
    parser.add_argument("--move-heading-uuid", help="Destination heading UUID for moving the todo")
    parser.add_argument("--move-heading-title", help="Destination heading title for moving the todo")


def read_text_arg(text: str | None, path: str | None) -> str:
    if text is not None:
        return text
    if path:
        return Path(path).read_text(encoding="utf-8")
    return ""


def build_intake_propose_cli_summary(result: dict[str, object]) -> dict[str, object]:
    session = result.get("session") if isinstance(result.get("session"), dict) else {}
    packet = result.get("packet") if isinstance(result.get("packet"), dict) else {}
    review = packet.get("review") if isinstance(packet.get("review"), dict) else {}
    normalized = review.get("normalized") if isinstance(review.get("normalized"), dict) else {}
    proposal = packet.get("proposal") if isinstance(packet.get("proposal"), dict) else {}
    llm = result.get("llm") if isinstance(result.get("llm"), dict) else {}
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}

    summary: dict[str, object] = {
        "status": result.get("status"),
        "session": {
            "session_id": session.get("session_id"),
            "status": session.get("status"),
            "next_packet_id": session.get("next_packet_id"),
        },
        "packet": {
            "packet_id": packet.get("packet_id"),
            "status": packet.get("status"),
            "classification": normalized.get("classification"),
            "proposal_status": proposal.get("status"),
        },
        "llm": {
            "dry_run": llm.get("dry_run"),
            "provider": llm.get("provider"),
            "requested_model": llm.get("requested_model"),
            "resolved_model": llm.get("resolved_model"),
            "prompt_chars": llm.get("prompt_chars"),
        },
    }
    if artifacts:
        summary["artifacts"] = artifacts
    if result.get("error"):
        summary["error"] = result.get("error")
    if result.get("status") == "proposed":
        summary["proposal"] = {
            "interpretation_kind": proposal.get("interpretation_kind"),
            "confidence": proposal.get("confidence"),
            "recommended_home_kind": proposal.get("recommended_home_kind"),
            "recommended_home_title": proposal.get("recommended_home_title"),
            "proposed_project": proposal.get("proposed_project"),
            "proposed_next_action": proposal.get("proposed_next_action"),
            "retire_recommendation": proposal.get("retire_recommendation"),
        }
    elif proposal.get("parse_error"):
        summary["proposal"] = {"parse_error": proposal.get("parse_error")}
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Things 3 helpers via things-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools_parser = subparsers.add_parser("tools", help="List available MCP tools")
    tools_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    export_parser = subparsers.add_parser("export", help="Export a Things snapshot")
    export_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where timestamped JSON/Markdown snapshots should be written",
    )
    export_parser.add_argument(
        "--prefix",
        default="things-snapshot",
        help="Filename prefix for generated snapshot files",
    )
    export_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    archive_parser = subparsers.add_parser(
        "archive",
        help="Create a durable JSON/Markdown archive bundle of the current Things state",
    )
    archive_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where archive artifacts should be written",
    )
    archive_parser.add_argument(
        "--prefix",
        default="things-archive",
        help="Filename prefix for generated archive files",
    )
    archive_parser.add_argument(
        "--reason",
        default="manual-archive",
        help="Short reason recorded in the archive metadata",
    )
    archive_parser.add_argument("--no-write", action="store_true", help="Do not write archive artifacts to disk")
    archive_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    restore_parser = subparsers.add_parser(
        "restore",
        help="Build an analysis-only restore plan from an archive and create a pre-restore safety backup",
    )
    restore_parser.add_argument("--archive", required=True, help="Archive path or archive date like YYYYMMDD")
    restore_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where restore-plan and safety-backup artifacts should be written",
    )
    restore_parser.add_argument(
        "--prefix",
        default="things-restore-plan",
        help="Filename prefix for generated restore-plan files",
    )
    restore_parser.add_argument(
        "--archive-prefix",
        default="things-archive",
        help="Filename prefix used when resolving archive date references",
    )
    restore_parser.add_argument(
        "--backup-prefix",
        default="things-pre-restore-backup",
        help="Filename prefix for the automatic pre-restore safety backup",
    )
    restore_parser.add_argument(
        "--trash-area-uuid",
        help="Canonical area UUID for the restore Trash destination",
    )
    restore_parser.add_argument(
        "--trash-area-title",
        help="Canonical area title for the restore Trash destination",
    )
    restore_parser.add_argument(
        "--trash-project-uuid",
        help="Canonical project UUID for the restore Trash destination",
    )
    restore_parser.add_argument(
        "--trash-project-title",
        default="Trash",
        help="Canonical project title for the restore Trash destination (default: Trash)",
    )
    restore_parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute ready reconcile actions. Without this flag restore stays analysis-only.",
    )
    restore_parser.add_argument("--no-write", action="store_true", help="Do not write restore-plan or safety-backup artifacts")
    restore_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    inbox_parser = subparsers.add_parser(
        "inbox-questions",
        help="Build a Today-first Inbox question set as JSON/Markdown artifacts",
    )
    inbox_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where inbox question artifacts should be written",
    )
    inbox_parser.add_argument(
        "--prefix",
        default="things-inbox-questions",
        help="Filename prefix for generated inbox question files",
    )
    inbox_parser.add_argument("--no-write", action="store_true", help="Do not write inbox question artifacts to disk")
    inbox_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    inbox_answers_parser = subparsers.add_parser(
        "inbox-answers",
        help="Read an answered inbox Markdown file and build explicit reviewed actions for completed answers",
    )
    inbox_answers_parser.add_argument("--input-file", required=True, help="Path to the answered inbox Markdown file")
    inbox_answers_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where inbox answer review artifacts should be written",
    )
    inbox_answers_parser.add_argument(
        "--prefix",
        default="things-inbox-answer-review",
        help="Filename prefix for generated inbox answer review files",
    )
    inbox_answers_parser.add_argument("--no-write", action="store_true", help="Do not write inbox answer review artifacts to disk")
    inbox_answers_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    intake_parser = subparsers.add_parser(
        "intake",
        help="Start or continue the terminal-first intake reconstruction workflow",
    )
    intake_subparsers = intake_parser.add_subparsers(dest="intake_command", required=True)

    intake_start_parser = intake_subparsers.add_parser(
        "start",
        help="Generate singleton intake packets from incomplete Today items and write session artifacts",
    )
    intake_start_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where intake session artifacts should be written",
    )
    intake_start_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    intake_next_parser = intake_subparsers.add_parser(
        "next",
        help="Review the next unresolved intake packet and write updated session artifacts",
    )
    intake_next_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where intake session artifacts are stored",
    )
    intake_next_parser.add_argument(
        "--session",
        help="Session directory, session.json path, or session id. Defaults to the latest intake session.",
    )
    intake_next_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    intake_propose_parser = intake_subparsers.add_parser(
        "propose",
        help="Build an intake LLM request, optionally parse a decision, and write preview-only proposal artifacts",
    )
    intake_propose_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where intake session artifacts are stored",
    )
    intake_propose_parser.add_argument(
        "--session",
        help="Session directory, session.json path, or session id. Defaults to the latest intake session.",
    )
    intake_propose_parser.add_argument("--packet", required=True, help="Packet id to propose from, such as packet-001")
    intake_propose_parser.add_argument("--decision", help="Inline JSON intake-decision text")
    intake_propose_parser.add_argument("--decision-file", help="Path to a JSON intake-decision file")
    intake_propose_parser.add_argument("--env-path", help="Override the repo-local .env path")
    intake_propose_parser.add_argument("--model", help="Model name or alias to use")
    intake_propose_parser.add_argument("--max-tokens", type=int, help="Override the max token limit")
    intake_propose_parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the real provider call. Without this flag the command returns a request preview unless a decision is supplied.",
    )
    intake_propose_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    task_parser = subparsers.add_parser(
        "task",
        help="Sync and inspect durable local task items backed by Things Today + Inbox",
    )
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)

    task_list_parser = task_subparsers.add_parser(
        "list",
        help="Sync Today + Inbox into the local task store and print compact reviewable rows",
    )
    task_list_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where the local task store should be written",
    )
    task_list_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    task_next_parser = task_subparsers.add_parser(
        "next",
        help="Sync Today + Inbox, pick the next reviewable task, and show it immediately",
    )
    task_next_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where the local task store should be written",
    )
    task_next_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    task_show_parser = task_subparsers.add_parser(
        "show",
        help="Show a durable local task by stable key or cached numbered slot",
    )
    task_show_parser.add_argument("selector", help="Stable key like T-001 or cached slot number like 1")
    task_show_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where the local task store is written",
    )

    task_review_parser = task_subparsers.add_parser(
        "review",
        help="Run the structured task-vs-project interview for a durable local task item",
    )
    task_review_parser.add_argument("selector", help="Stable key like T-001 or cached slot number like 1")
    task_review_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where the local task store is written",
    )

    task_open_parser = task_subparsers.add_parser(
        "open",
        help="Open a durable local task document in Vim, then optionally run AI polish",
    )
    task_open_parser.add_argument("selector", help="Stable key like T-001 or cached slot number like 1")
    task_open_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where the local task store is written",
    )

    task_accept_parser = task_subparsers.add_parser(
        "accept",
        help="Apply a proposed durable task item back into Things and mark it active locally",
    )
    task_accept_parser.add_argument("selector", help="Stable key like T-001 or cached slot number like 1")
    task_accept_parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory where the local task store is written",
    )
    task_accept_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    create_parser = subparsers.add_parser(
        "create-task",
        help="Prepare or create a task in Things using canonical area/project/heading selectors",
    )
    create_parser.add_argument("--title", required=True, help="Title of the task")
    create_parser.add_argument("--notes", help="Optional notes for the task")
    create_parser.add_argument("--when", help="Schedule value for the task")
    create_parser.add_argument("--deadline", help="Deadline in YYYY-MM-DD format")
    create_parser.add_argument("--tag", dest="tags", action="append", default=[], help="Tag to apply")
    create_parser.add_argument(
        "--checklist-item",
        dest="checklist_items",
        action="append",
        default=[],
        help="Checklist item to add",
    )
    create_parser.add_argument("--area-uuid", help="Canonical area UUID")
    create_parser.add_argument("--area-title", help="Canonical area title")
    create_parser.add_argument("--project-uuid", help="Canonical project UUID")
    create_parser.add_argument("--project-title", help="Canonical project title")
    create_parser.add_argument("--heading-uuid", help="Canonical heading UUID within the selected project")
    create_parser.add_argument("--heading-title", help="Canonical heading title within the selected project")
    add_apply_argument(create_parser)

    create_project_parser = subparsers.add_parser(
        "create-project",
        help="Prepare or create a project in Things using canonical area selectors",
    )
    create_project_parser.add_argument("--title", required=True, help="Title of the project")
    create_project_parser.add_argument("--notes", help="Optional notes for the project")
    create_project_parser.add_argument("--when", help="Schedule value for the project")
    create_project_parser.add_argument("--deadline", help="Deadline in YYYY-MM-DD format")
    create_project_parser.add_argument("--tag", dest="tags", action="append", default=[], help="Tag to apply")
    create_project_parser.add_argument(
        "--todo",
        dest="todos",
        action="append",
        default=[],
        help="Initial project todo to add",
    )
    create_project_parser.add_argument("--area-uuid", help="Canonical area UUID")
    create_project_parser.add_argument("--area-title", help="Canonical area title")
    add_apply_argument(create_project_parser)

    update_task_parser = subparsers.add_parser(
        "update-task",
        help="Prepare or update an existing task in Things using canonical selectors",
    )
    update_task_parser.add_argument("--todo-uuid", help="Canonical todo UUID")
    update_task_parser.add_argument("--todo-title", help="Canonical todo title")
    update_task_parser.add_argument("--area-uuid", help="Canonical area UUID")
    update_task_parser.add_argument("--area-title", help="Canonical area title")
    update_task_parser.add_argument("--project-uuid", help="Canonical project UUID")
    update_task_parser.add_argument("--project-title", help="Canonical project title")
    update_task_parser.add_argument("--heading-uuid", help="Canonical heading UUID within the selected project")
    update_task_parser.add_argument("--heading-title", help="Canonical heading title within the selected project")
    add_move_selector_arguments(update_task_parser)
    add_common_update_fields(update_task_parser)
    add_apply_argument(update_task_parser)

    update_project_parser = subparsers.add_parser(
        "update-project",
        help="Prepare or update an existing project in Things using canonical selectors",
    )
    update_project_parser.add_argument("--project-uuid", help="Canonical project UUID")
    update_project_parser.add_argument("--project-title", help="Canonical project title")
    update_project_parser.add_argument("--area-uuid", help="Canonical area UUID")
    update_project_parser.add_argument("--area-title", help="Canonical area title")
    add_common_update_fields(update_project_parser)
    add_apply_argument(update_project_parser)

    context_parser = subparsers.add_parser(
        "task-context",
        help="Build a task-context payload and optional local debug artifacts for the Phase 2 LLM bridge",
    )
    add_context_selector_arguments(context_parser)
    context_parser.add_argument("--include-area", action="store_true", help="Include area context in the payload")
    context_parser.add_argument("--env-path", help="Override the repo-local .env path")
    context_parser.add_argument("--output-dir", help="Override the data root for task-context and log artifacts")
    context_parser.add_argument("--no-write", action="store_true", help="Do not write payload artifacts to disk")
    context_parser.add_argument("--model", help="Model name or alias to record in the debug log")
    context_parser.add_argument("--system", help="Optional system prompt text to log")
    context_parser.add_argument("--system-file", help="Read system prompt text from a file")
    context_parser.add_argument("--prompt", help="Optional user prompt text to log")
    context_parser.add_argument("--prompt-file", help="Read user prompt text from a file")
    context_parser.add_argument("--response", help="Optional model response text to log")
    context_parser.add_argument("--response-file", help="Read model response text from a file")
    context_parser.add_argument("--action", dest="actions", action="append", default=[], help="Action note to append to the debug log")
    context_parser.add_argument("--log-path", help="Explicit markdown log path")
    context_parser.add_argument("--no-log", action="store_true", help="Do not write a debug log")
    context_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    llm_parser = subparsers.add_parser(
        "task-llm",
        help="Build task context, prepare an LLM request preview, and optionally execute a real provider call",
    )
    add_context_selector_arguments(llm_parser)
    llm_parser.add_argument("--include-area", action="store_true", help="Include area context in the payload")
    llm_parser.add_argument("--env-path", help="Override the repo-local .env path")
    llm_parser.add_argument("--output-dir", help="Override the data root for task-context and log artifacts")
    llm_parser.add_argument("--no-write", action="store_true", help="Do not write task-context artifacts to disk")
    llm_parser.add_argument("--model", help="Model name or alias to use")
    llm_parser.add_argument("--max-tokens", type=int, help="Override the max token limit")
    llm_parser.add_argument("--system", help="Optional system prompt text")
    llm_parser.add_argument("--system-file", help="Read system prompt text from a file")
    llm_parser.add_argument("--prompt", help="Instruction prompt text for the task context")
    llm_parser.add_argument("--prompt-file", help="Read instruction prompt text from a file")
    llm_parser.add_argument("--action", dest="actions", action="append", default=[], help="Action note to append to the debug log")
    llm_parser.add_argument("--log-path", help="Explicit markdown log path")
    llm_parser.add_argument("--no-log", action="store_true", help="Do not write a debug log")
    llm_parser.add_argument("--execute", action="store_true", help="Perform the real provider call. Without this flag the command returns a preview only.")
    llm_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    proposals_parser = subparsers.add_parser(
        "task-proposals",
        help="Interpret a shared task request bundle and task decision into suggestion-only action proposals",
    )
    proposals_parser.add_argument("--request-file", required=True, help="Path to a task-request JSON artifact")
    proposals_parser.add_argument("--decision", help="Inline JSON task-decision text")
    proposals_parser.add_argument("--decision-file", help="Path to a task-decision JSON file")
    proposals_parser.add_argument("--env-path", help="Override the repo-local .env path")
    proposals_parser.add_argument("--output-dir", help="Override the data root for task-proposal artifacts")
    proposals_parser.add_argument("--no-write", action="store_true", help="Do not write task-proposal artifacts to disk")
    proposals_parser.add_argument("--mcp-command", help="Override THINGS_MCP_COMMAND")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "tools":
        with StdioMcpClient.from_environment(command_text=args.mcp_command) as client:
            tools = client.list_tools()
        print(json.dumps(tools, indent=2, sort_keys=True))
        return 0

    if args.command == "export":
        paths = export_snapshot(
            output_dir=Path(args.output_dir),
            prefix=args.prefix,
            command_text=args.mcp_command,
        )
        print(json.dumps({key: str(value) for key, value in paths.items()}, indent=2))
        return 0

    if args.command == "archive":
        archive_result = archive_snapshot(
            output_dir=Path(args.output_dir),
            prefix=args.prefix,
            command_text=args.mcp_command,
            write_artifacts=not args.no_write,
            archive_reason=args.reason,
        )
        archive_bundle = archive_result["archive_bundle"]
        result: dict[str, object] = {
            "archive": {
                "schema_version": archive_bundle.get("schema_version"),
                "archive_id": archive_bundle.get("archive_id"),
                "generated_at": archive_bundle.get("generated_at"),
                "archive_reason": archive_bundle.get("archive_reason"),
                "summary": archive_bundle.get("summary", {}),
            }
        }
        artifacts = archive_result.get("artifacts")
        if isinstance(artifacts, dict):
            result["artifacts"] = {key: str(value) for key, value in artifacts.items()}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "restore":
        restore_result = plan_restore(
            args.archive,
            output_dir=Path(args.output_dir),
            prefix=args.prefix,
            archive_prefix=args.archive_prefix,
            backup_prefix=args.backup_prefix,
            trash_area_uuid=args.trash_area_uuid,
            trash_area_title=args.trash_area_title,
            trash_project_uuid=args.trash_project_uuid,
            trash_project_title=args.trash_project_title,
            apply=args.apply,
            command_text=args.mcp_command,
            write_artifacts=not args.no_write,
        )
        result: dict[str, object] = {
            "resolved_archive_path": restore_result["resolved_archive_path"],
            "restore_plan": restore_result["restore_plan"],
        }
        artifacts = restore_result.get("artifacts")
        if isinstance(artifacts, dict):
            result["artifacts"] = {key: str(value) for key, value in artifacts.items()}
        safety_backup = restore_result.get("safety_backup")
        if isinstance(safety_backup, dict):
            result["safety_backup"] = {
                **{key: value for key, value in safety_backup.items() if key != "artifacts"},
                "artifacts": {
                    key: str(value) for key, value in (safety_backup.get("artifacts") or {}).items()
                },
            }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "inbox-questions":
        question_set = fetch_inbox_question_set(command_text=args.mcp_command)
        result: dict[str, object] = {"question_set": question_set}
        if not args.no_write:
            artifacts = write_inbox_question_set_artifacts(
                question_set,
                output_dir=Path(args.output_dir),
                prefix=args.prefix,
            )
            result["artifacts"] = {key: str(value) for key, value in artifacts.items()}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "inbox-answers":
        review_bundle = review_inbox_answer_file(Path(args.input_file), command_text=args.mcp_command)
        result = {"review_bundle": review_bundle}
        if not args.no_write:
            artifacts = write_inbox_answer_review_artifacts(
                review_bundle,
                output_dir=Path(args.output_dir),
                prefix=args.prefix,
            )
            result["artifacts"] = {key: str(value) for key, value in artifacts.items()}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "intake":
        if args.intake_command == "start":
            result = start_intake_session(
                output_dir=Path(args.output_dir),
                command_text=args.mcp_command,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.intake_command == "next":
            result = review_next_packet(
                output_dir=Path(args.output_dir),
                session_ref=args.session,
                command_text=args.mcp_command,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.intake_command == "propose":
            if args.decision and args.decision_file:
                parser.error("intake propose accepts at most one of --decision or --decision-file")
            result = propose_intake_packet(
                output_dir=Path(args.output_dir),
                packet_ref=args.packet,
                session_ref=args.session,
                model=args.model,
                max_tokens=args.max_tokens,
                env_path=Path(args.env_path) if args.env_path else None,
                execute=args.execute,
                decision_text=read_text_arg(args.decision, args.decision_file) if (args.decision or args.decision_file) else None,
                command_text=args.mcp_command,
            )
            print(json.dumps(build_intake_propose_cli_summary(result), indent=2, sort_keys=True))
            return 0

    if args.command == "task":
        if args.task_command == "list":
            result = list_tasks(output_dir=Path(args.output_dir), command_text=args.mcp_command)
            print(result["rendered"], end="")
            return 0
        if args.task_command == "next":
            result = next_task(output_dir=Path(args.output_dir), command_text=args.mcp_command)
            print(result["rendered"], end="")
            return 0
        if args.task_command == "show":
            result = show_task(output_dir=Path(args.output_dir), selector=args.selector)
            print(result["rendered"], end="")
            return 0
        if args.task_command == "review":
            result = review_task(output_dir=Path(args.output_dir), selector=args.selector)
            print(result["rendered"], end="")
            return 0
        if args.task_command == "open":
            result = open_task(output_dir=Path(args.output_dir), selector=args.selector)
            print(result["rendered"], end="")
            return 0
        if args.task_command == "accept":
            result = accept_task(output_dir=Path(args.output_dir), selector=args.selector, command_text=args.mcp_command)
            print(result["rendered"], end="")
            return 0

    if args.command == "create-task":
        result = create_todo(
            title=args.title,
            notes=args.notes,
            when=args.when,
            deadline=args.deadline,
            tags=args.tags or None,
            checklist_items=args.checklist_items or None,
            area_uuid=args.area_uuid,
            area_title=args.area_title,
            project_uuid=args.project_uuid,
            project_title=args.project_title,
            heading_uuid=args.heading_uuid,
            heading_title=args.heading_title,
            dry_run=not args.apply,
            command_text=args.mcp_command,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "create-project":
        result = create_project(
            title=args.title,
            notes=args.notes,
            when=args.when,
            deadline=args.deadline,
            tags=args.tags or None,
            todos=args.todos or None,
            area_uuid=args.area_uuid,
            area_title=args.area_title,
            dry_run=not args.apply,
            command_text=args.mcp_command,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "update-task":
        result = update_todo(
            todo_uuid=args.todo_uuid,
            todo_title=args.todo_title,
            title=args.title,
            notes=args.notes,
            when=args.when,
            deadline=args.deadline,
            tags=args.tags or None,
            completed=args.completed,
            canceled=args.canceled,
            area_uuid=args.area_uuid,
            area_title=args.area_title,
            project_uuid=args.project_uuid,
            project_title=args.project_title,
            heading_uuid=args.heading_uuid,
            heading_title=args.heading_title,
            move_area_uuid=args.move_area_uuid,
            move_area_title=args.move_area_title,
            move_project_uuid=args.move_project_uuid,
            move_project_title=args.move_project_title,
            move_heading_uuid=args.move_heading_uuid,
            move_heading_title=args.move_heading_title,
            dry_run=not args.apply,
            command_text=args.mcp_command,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "update-project":
        result = update_project(
            project_uuid=args.project_uuid,
            project_title=args.project_title,
            title=args.title,
            notes=args.notes,
            when=args.when,
            deadline=args.deadline,
            tags=args.tags or None,
            completed=args.completed,
            canceled=args.canceled,
            area_uuid=args.area_uuid,
            area_title=args.area_title,
            dry_run=not args.apply,
            command_text=args.mcp_command,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "task-context":
        config = resolve_llm_config(env_path=Path(args.env_path) if args.env_path else None)
        payload = fetch_task_context_payload(
            todo_uuid=args.todo_uuid,
            todo_title=args.todo_title,
            area_uuid=args.area_uuid,
            area_title=args.area_title,
            project_uuid=args.project_uuid,
            project_title=args.project_title,
            heading_uuid=args.heading_uuid,
            heading_title=args.heading_title,
            include_area=args.include_area,
            command_text=args.mcp_command,
        )
        result: dict[str, object] = {"config": config, "payload": payload}
        output_root = Path(args.output_dir) if args.output_dir else None
        artifacts = None
        if not args.no_write:
            artifacts = write_task_context_artifacts(payload, output_root=output_root, config=config)
            result["artifacts"] = {key: str(value) for key, value in artifacts.items()}
        system = read_text_arg(args.system, args.system_file)
        prompt = read_text_arg(args.prompt, args.prompt_file)
        response = read_text_arg(args.response, args.response_file)
        if not args.no_log and any([system, prompt, response, args.actions]):
            log_path = write_llm_debug_log(
                requested_model=args.model or config["default_model"],
                resolved_model=resolve_model_name(args.model, config=config),
                system=system,
                prompt=prompt,
                response=response,
                actions=args.actions,
                payload=payload,
                payload_path=artifacts["json"] if artifacts else None,
                output_root=output_root,
                log_path=Path(args.log_path) if args.log_path else None,
                config=config,
            )
            result["log_path"] = str(log_path)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "task-llm":
        env_path = Path(args.env_path) if args.env_path else None
        config = resolve_llm_config(env_path=env_path)
        instruction = read_text_arg(args.prompt, args.prompt_file)
        if not instruction:
            parser.error("task-llm requires --prompt or --prompt-file")
        payload = fetch_task_context_payload(
            todo_uuid=args.todo_uuid,
            todo_title=args.todo_title,
            area_uuid=args.area_uuid,
            area_title=args.area_title,
            project_uuid=args.project_uuid,
            project_title=args.project_title,
            heading_uuid=args.heading_uuid,
            heading_title=args.heading_title,
            include_area=args.include_area,
            command_text=args.mcp_command,
        )
        output_root = Path(args.output_dir) if args.output_dir else None
        artifacts = None
        if not args.no_write:
            artifacts = write_task_context_artifacts(payload, output_root=output_root, config=config)
        system = read_text_arg(args.system, args.system_file)
        request_bundle = build_task_request_bundle(
            payload,
            instruction,
            system=system,
            model=args.model,
            max_tokens=args.max_tokens,
            config=config,
        )
        request_artifacts = None
        if not args.no_write:
            request_artifacts = write_task_request_artifacts(request_bundle, output_root=output_root, config=config)
        prompt = str(request_bundle["request"]["prompt"])
        llm_result = complete(
            prompt,
            model=args.model,
            system=system,
            max_tokens=args.max_tokens,
            env_path=env_path,
            execute=args.execute,
        )
        result = {"config": config, "payload": payload, "request_bundle": request_bundle, "llm": llm_result}
        if artifacts is not None:
            result["artifacts"] = {key: str(value) for key, value in artifacts.items()}
        if request_artifacts is not None:
            result["request_artifacts"] = {key: str(value) for key, value in request_artifacts.items()}
        if not args.no_log:
            actions = list(args.actions)
            if not args.execute:
                actions.append("preview only; no external LLM call executed")
            actions.append("shared task request bundle available for external LLM or Augment consumption")
            log_path = write_llm_debug_log(
                requested_model=args.model or config["default_model"],
                resolved_model=llm_result["resolved_model"],
                system=system,
                prompt=prompt,
                response=str(llm_result.get("response_text", "")),
                actions=actions,
                payload=payload,
                payload_path=artifacts["json"] if artifacts else None,
                request_path=request_artifacts["markdown"] if request_artifacts else None,
                output_root=output_root,
                log_path=Path(args.log_path) if args.log_path else None,
                config=config,
            )
            result["log_path"] = str(log_path)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "task-proposals":
        if bool(args.decision) == bool(args.decision_file):
            parser.error("task-proposals requires exactly one of --decision or --decision-file")
        env_path = Path(args.env_path) if args.env_path else None
        config = resolve_llm_config(env_path=env_path)
        request_bundle = load_task_request_bundle(Path(args.request_file))
        decision = parse_task_decision(read_text_arg(args.decision, args.decision_file))
        proposal_bundle = build_task_action_proposals(
            request_bundle,
            decision,
            command_text=args.mcp_command,
        )
        result: dict[str, object] = {
            "config": config,
            "request_bundle": request_bundle,
            "decision": decision,
            "proposal_bundle": proposal_bundle,
        }
        output_root = Path(args.output_dir) if args.output_dir else None
        if not args.no_write:
            artifacts = write_task_proposal_artifacts(proposal_bundle, output_root=output_root, config=config)
            result["artifacts"] = {key: str(value) for key, value in artifacts.items()}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2