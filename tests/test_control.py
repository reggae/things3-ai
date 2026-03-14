from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from things_ai.cli import main
from things_ai.control import (
    SelectionError,
    create_todo,
    prepare_create_todo_request,
    prepare_update_project_request,
    prepare_update_todo_request,
    update_todo,
)


def sample_snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-03-09T16:00:00Z",
        "normalized": {
            "areas": [
                {"kind": "area", "uuid": "area-1", "title": "Home"},
                {"kind": "area", "uuid": "area-2", "title": "Work"},
            ],
            "projects": [
                {
                    "kind": "project",
                    "uuid": "project-1",
                    "title": "Project Alpha",
                    "relationships": {"area_uuid": "area-1", "area_title": "Home"},
                    "children": {
                        "headings": [
                            {"kind": "heading", "uuid": "heading-1", "title": "Next"},
                            {"kind": "heading", "uuid": "heading-2", "title": "Later"},
                        ]
                    },
                },
                {
                    "kind": "project",
                    "uuid": "project-2",
                    "title": "Project Alpha",
                    "relationships": {"area_uuid": "area-2", "area_title": "Work"},
                },
            ],
            "todos": [
                {
                    "kind": "todo",
                    "uuid": "todo-1",
                    "title": "Follow up",
                    "relationships": {
                        "project_title": "Project Alpha",
                        "area_title": "Home",
                        "heading_title": "Next",
                    },
                },
                {
                    "kind": "todo",
                    "uuid": "todo-2",
                    "title": "Follow up",
                    "relationships": {
                        "project_title": "Project Alpha",
                        "area_title": "Work",
                    },
                },
                {
                    "kind": "todo",
                    "uuid": "todo-3",
                    "title": "Loose task",
                    "relationships": {"area_title": "Home"},
                },
            ],
            "tags": [],
        },
    }


class ControlHelpersTests(unittest.TestCase):
    def test_prepare_create_todo_request_targets_project_and_heading(self) -> None:
        request = prepare_create_todo_request(
            sample_snapshot(),
            title="Plan next step",
            project_title="Project Alpha",
            area_title="Home",
            heading_title="Next",
            tags=["home"],
            checklist_items=["Draft"],
        )

        self.assertEqual(request["tool"], "add_todo")
        self.assertEqual(request["arguments"]["list_id"], "project-1")
        self.assertEqual(request["arguments"]["heading_id"], "heading-1")
        self.assertEqual(request["arguments"]["tags"], ["home"])
        self.assertEqual(request["arguments"]["checklist_items"], ["Draft"])
        self.assertEqual(request["target"]["project"]["uuid"], "project-1")

    def test_prepare_create_todo_request_targets_area_without_project(self) -> None:
        request = prepare_create_todo_request(sample_snapshot(), title="Loose task", area_uuid="area-1")

        self.assertEqual(request["arguments"]["list_id"], "area-1")
        self.assertNotIn("heading_id", request["arguments"])
        self.assertEqual(request["target"]["area"]["title"], "Home")

    def test_prepare_create_todo_request_rejects_ambiguous_project(self) -> None:
        with self.assertRaisesRegex(SelectionError, "Multiple projects matched selector"):
            prepare_create_todo_request(sample_snapshot(), title="Loose task", project_title="Project Alpha")

    def test_prepare_create_todo_request_requires_project_for_heading(self) -> None:
        with self.assertRaisesRegex(SelectionError, "project selector is required"):
            prepare_create_todo_request(sample_snapshot(), title="Loose task", heading_title="Next")

    @patch("things_ai.control.StdioMcpClient.from_environment")
    @patch("things_ai.control.fetch_snapshot", return_value=sample_snapshot())
    def test_create_todo_defaults_to_dry_run(self, mock_fetch: object, mock_client: object) -> None:
        result = create_todo(title="Plan next step", project_uuid="project-1")

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["request"]["arguments"]["list_id"], "project-1")
        mock_fetch.assert_called_once()
        mock_client.assert_not_called()

    def test_prepare_update_todo_request_targets_scoped_todo(self) -> None:
        request = prepare_update_todo_request(
            sample_snapshot(),
            todo_title="Follow up",
            area_title="Home",
            project_title="Project Alpha",
            heading_title="Next",
            completed=True,
        )

        self.assertEqual(request["tool"], "update_todo")
        self.assertEqual(request["arguments"]["id"], "todo-1")
        self.assertEqual(request["arguments"]["completed"], True)
        self.assertEqual(request["target"]["todo"]["uuid"], "todo-1")
        self.assertEqual(request["target"]["project"]["uuid"], "project-1")

    def test_prepare_update_todo_request_includes_move_destination(self) -> None:
        request = prepare_update_todo_request(
            sample_snapshot(),
            todo_uuid="todo-1",
            notes="Move this",
            move_area_title="Work",
            move_project_title="Project Alpha",
        )

        self.assertEqual(request["arguments"]["id"], "todo-1")
        self.assertEqual(request["arguments"]["notes"], "Move this")
        self.assertEqual(request["arguments"]["list_id"], "project-2")
        self.assertEqual(request["target"]["move_area"]["uuid"], "area-2")
        self.assertEqual(request["target"]["move_project"]["uuid"], "project-2")
        self.assertEqual(request["target"]["list"]["uuid"], "project-2")

    def test_prepare_update_todo_request_rejects_ambiguous_todo(self) -> None:
        with self.assertRaisesRegex(SelectionError, "Multiple todos matched selector"):
            prepare_update_todo_request(sample_snapshot(), todo_title="Follow up", notes="Updated")

    def test_prepare_update_todo_request_requires_changes(self) -> None:
        with self.assertRaisesRegex(ValueError, "At least one todo field must be provided"):
            prepare_update_todo_request(sample_snapshot(), todo_uuid="todo-1")

    def test_prepare_update_project_request_targets_project(self) -> None:
        request = prepare_update_project_request(
            sample_snapshot(),
            project_title="Project Alpha",
            area_title="Home",
            deadline="2026-03-10",
        )

        self.assertEqual(request["tool"], "update_project")
        self.assertEqual(request["arguments"]["id"], "project-1")
        self.assertEqual(request["arguments"]["deadline"], "2026-03-10")
        self.assertEqual(request["target"]["project"]["uuid"], "project-1")

    @patch("things_ai.control.StdioMcpClient.from_environment")
    @patch("things_ai.control.fetch_snapshot", return_value=sample_snapshot())
    def test_update_todo_defaults_to_dry_run(self, mock_fetch: object, mock_client: object) -> None:
        result = update_todo(todo_uuid="todo-1", notes="Updated")

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["request"]["arguments"]["id"], "todo-1")
        mock_fetch.assert_called_once()
        mock_client.assert_not_called()


class CliCreateTaskTests(unittest.TestCase):
    @patch(
        "things_ai.cli.archive_snapshot",
        return_value={
            "archive_bundle": {
                "schema_version": "things-ai.archive.v1",
                "archive_id": "20260310T120000Z",
                "generated_at": "2026-03-10T12:00:00Z",
                "archive_reason": "manual-archive",
                "summary": {"todos": 1, "projects": 1, "areas": 1, "tags": 0, "headings": 1},
            },
            "artifacts": {
                "json": Path("data/archives/2026-03-10/example.json"),
                "markdown": Path("data/archives/2026-03-10/example.md"),
            },
        },
    )
    def test_main_archive_writes_artifacts_by_default(self, mock_archive: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["archive"])

        self.assertEqual(exit_code, 0)
        mock_archive.assert_called_once_with(
            output_dir=Path("data"),
            prefix="things-archive",
            command_text=None,
            write_artifacts=True,
            archive_reason="manual-archive",
        )
        self.assertIn('"archive_id": "20260310T120000Z"', stdout.getvalue())
        self.assertIn('"json": "data/archives/2026-03-10/example.json"', stdout.getvalue())

    @patch(
        "things_ai.cli.plan_restore",
        return_value={
            "resolved_archive_path": "data/archives/2026-03-10/example.json",
            "restore_plan": {
                "schema_version": "things-ai.restore-plan.v1",
                "execution_mode": "analysis-only",
                "blocking_reasons": ["missing delete tools"],
            },
        },
    )
    def test_main_restore_no_write_skips_artifacts(self, mock_restore: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["restore", "--archive", "20260310", "--no-write"])

        self.assertEqual(exit_code, 0)
        mock_restore.assert_called_once_with(
            "20260310",
            output_dir=Path("data"),
            prefix="things-restore-plan",
            archive_prefix="things-archive",
            backup_prefix="things-pre-restore-backup",
            trash_area_uuid=None,
            trash_area_title=None,
            trash_project_uuid=None,
            trash_project_title="Trash",
            apply=False,
            command_text=None,
            write_artifacts=False,
        )
        self.assertIn('"resolved_archive_path": "data/archives/2026-03-10/example.json"', stdout.getvalue())
        self.assertIn('"schema_version": "things-ai.restore-plan.v1"', stdout.getvalue())

    @patch(
        "things_ai.cli.start_intake_session",
        return_value={"session": {"session_id": "intake-20260311T120000Z", "status": "active"}},
    )
    def test_main_intake_start_defaults_output_dir(self, mock_start: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["intake", "start"])

        self.assertEqual(exit_code, 0)
        mock_start.assert_called_once_with(output_dir=Path("data"), command_text=None)
        self.assertIn('"session_id": "intake-20260311T120000Z"', stdout.getvalue())

    @patch(
        "things_ai.cli.review_next_packet",
        return_value={"status": "reviewed", "packet": {"packet_id": "packet-001"}},
    )
    def test_main_intake_next_forwards_session_reference(self, mock_next: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["intake", "next", "--output-dir", "scratch", "--session", "session-123"])

        self.assertEqual(exit_code, 0)
        mock_next.assert_called_once_with(
            output_dir=Path("scratch"),
            session_ref="session-123",
            command_text=None,
        )
        self.assertIn('"packet_id": "packet-001"', stdout.getvalue())

    @patch(
        "things_ai.cli.propose_intake_packet",
        return_value={
            "status": "proposed",
            "session": {"session_id": "session-123", "status": "complete", "next_packet_id": None},
            "packet": {
                "packet_id": "packet-001",
                "status": "proposed",
                "review": {"normalized": {"classification": "project"}},
                "proposal": {
                    "status": "generated",
                    "interpretation_kind": "project_with_next_action",
                    "confidence": "medium",
                    "recommended_home_kind": "new_project",
                    "recommended_home_title": "Plan trip",
                    "proposed_project": "Plan trip",
                    "proposed_next_action": "Call airline about baggage policy",
                    "retire_recommendation": "keep_source",
                },
            },
            "llm": {
                "dry_run": True,
                "provider": "anthropic",
                "requested_model": "openai-cheap",
                "resolved_model": "claude-sonnet-4-5",
                "prompt_chars": 999,
                "request_preview": {"body": {"messages": [{"content": "very large prompt body"}]}}},
            "request_bundle": {"request": {"prompt": "very large prompt body"}},
            "artifacts": {
                "request_json": "scratch/request.json",
                "request_markdown": "scratch/request.md",
                "proposal_json": "scratch/proposal.json",
            },
        },
    )
    def test_main_intake_propose_forwards_packet_and_decision(self, mock_propose: object) -> None:
        stdout = io.StringIO()
        decision = '{"summary":"ok"}'
        with patch("sys.stdout", stdout):
            exit_code = main(
                [
                    "intake",
                    "propose",
                    "--output-dir",
                    "scratch",
                    "--session",
                    "session-123",
                    "--packet",
                    "packet-001",
                    "--decision",
                    decision,
                    "--env-path",
                    ".env.test",
                    "--model",
                    "openai-cheap",
                    "--max-tokens",
                    "1234",
                ]
            )

        self.assertEqual(exit_code, 0)
        mock_propose.assert_called_once_with(
            output_dir=Path("scratch"),
            packet_ref="packet-001",
            session_ref="session-123",
            model="openai-cheap",
            max_tokens=1234,
            env_path=Path(".env.test"),
            execute=False,
            decision_text=decision,
            command_text=None,
        )
        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["status"], "proposed")
        self.assertEqual(rendered["packet"]["packet_id"], "packet-001")
        self.assertEqual(rendered["packet"]["proposal_status"], "generated")
        self.assertEqual(rendered["proposal"]["interpretation_kind"], "project_with_next_action")
        self.assertEqual(rendered["proposal"]["recommended_home_kind"], "new_project")
        self.assertEqual(rendered["proposal"]["proposed_project"], "Plan trip")
        self.assertNotIn("request_bundle", rendered)
        self.assertNotIn("request_preview", rendered.get("llm", {}))
        self.assertNotIn("very large prompt body", stdout.getvalue())

    @patch(
        "things_ai.cli.propose_intake_packet",
        return_value={
            "status": "response_invalid",
            "error": "intake decision is not valid JSON",
            "session": {"session_id": "session-123", "status": "complete", "next_packet_id": None},
            "packet": {
                "packet_id": "packet-001",
                "status": "reviewed",
                "review": {"normalized": {"classification": "project"}},
                "proposal": {
                    "status": "response_invalid",
                    "parse_error": "intake decision is not valid JSON",
                },
            },
            "llm": {
                "dry_run": False,
                "provider": "anthropic",
                "requested_model": "claude-strong",
                "resolved_model": "claude-sonnet-4-5",
                "prompt_chars": 999,
                "request_preview": {"body": {"messages": [{"content": "very large prompt body"}]}}},
            "request_bundle": {"request": {"prompt": "very large prompt body"}},
            "artifacts": {
                "request_json": "scratch/request.json",
                "response_json": "scratch/response.json",
            },
        },
    )
    def test_main_intake_propose_surfaces_compact_invalid_response_summary(self, mock_propose: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["intake", "propose", "--packet", "packet-001", "--execute"])

        self.assertEqual(exit_code, 0)
        rendered = json.loads(stdout.getvalue())
        self.assertEqual(rendered["status"], "response_invalid")
        self.assertEqual(rendered["packet"]["proposal_status"], "response_invalid")
        self.assertEqual(rendered["proposal"]["parse_error"], "intake decision is not valid JSON")
        self.assertEqual(rendered["error"], "intake decision is not valid JSON")
        self.assertNotIn("request_bundle", rendered)
        self.assertNotIn("request_preview", rendered.get("llm", {}))
        self.assertNotIn("very large prompt body", stdout.getvalue())

    @patch("things_ai.cli.create_todo", return_value={"dry_run": True, "request": {"tool": "add_todo"}})
    def test_main_create_task_uses_dry_run_by_default(self, mock_create: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["create-task", "--title", "Preview", "--project-uuid", "project-1"])

        self.assertEqual(exit_code, 0)
        mock_create.assert_called_once_with(
            title="Preview",
            notes=None,
            when=None,
            deadline=None,
            tags=None,
            checklist_items=None,
            area_uuid=None,
            area_title=None,
            project_uuid="project-1",
            project_title=None,
            heading_uuid=None,
            heading_title=None,
            dry_run=True,
            command_text=None,
        )
        self.assertIn('"dry_run": true', stdout.getvalue())

    @patch("things_ai.cli.create_todo", return_value={"dry_run": False, "request": {"tool": "add_todo"}})
    def test_main_create_task_apply_disables_dry_run(self, mock_create: object) -> None:
        with patch("sys.stdout", io.StringIO()):
            exit_code = main(["create-task", "--title", "Apply", "--project-uuid", "project-1", "--apply"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_create.call_args.kwargs["dry_run"], False)

    @patch("things_ai.cli.create_project", return_value={"dry_run": True, "request": {"tool": "add_project"}})
    def test_main_create_project_uses_dry_run_by_default(self, mock_create: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["create-project", "--title", "Project Preview", "--area-title", "📎 Work"])

        self.assertEqual(exit_code, 0)
        mock_create.assert_called_once_with(
            title="Project Preview",
            notes=None,
            when=None,
            deadline=None,
            tags=None,
            todos=None,
            area_uuid=None,
            area_title="📎 Work",
            dry_run=True,
            command_text=None,
        )
        self.assertIn('"dry_run": true', stdout.getvalue())

    @patch("things_ai.cli.create_project", return_value={"dry_run": False, "request": {"tool": "add_project"}})
    def test_main_create_project_apply_disables_dry_run(self, mock_create: object) -> None:
        with patch("sys.stdout", io.StringIO()):
            exit_code = main(["create-project", "--title", "Project Apply", "--area-title", "📎 Work", "--apply"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_create.call_args.kwargs["dry_run"], False)

    @patch("things_ai.cli.update_todo", return_value={"dry_run": True, "request": {"tool": "update_todo"}})
    def test_main_update_task_uses_dry_run_by_default(self, mock_update: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["update-task", "--todo-uuid", "todo-1", "--notes", "Updated"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_update.call_args.kwargs["todo_uuid"], "todo-1")
        self.assertEqual(mock_update.call_args.kwargs["notes"], "Updated")
        self.assertEqual(mock_update.call_args.kwargs["dry_run"], True)
        self.assertIn('"dry_run": true', stdout.getvalue())

    @patch("things_ai.cli.update_todo", return_value={"dry_run": True, "request": {"tool": "update_todo"}})
    def test_main_update_task_forwards_move_selectors(self, mock_update: object) -> None:
        with patch("sys.stdout", io.StringIO()):
            exit_code = main(
                [
                    "update-task",
                    "--todo-uuid",
                    "todo-1",
                    "--notes",
                    "Updated",
                    "--move-area-title",
                    "Work",
                    "--move-project-title",
                    "Project Alpha",
                    "--move-heading-title",
                    "Later",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_update.call_args.kwargs["move_area_title"], "Work")
        self.assertEqual(mock_update.call_args.kwargs["move_project_title"], "Project Alpha")
        self.assertEqual(mock_update.call_args.kwargs["move_heading_title"], "Later")

    @patch("things_ai.cli.update_project", return_value={"dry_run": False, "request": {"tool": "update_project"}})
    def test_main_update_project_apply_disables_dry_run(self, mock_update: object) -> None:
        with patch("sys.stdout", io.StringIO()):
            exit_code = main(
                [
                    "update-project",
                    "--project-uuid",
                    "project-1",
                    "--deadline",
                    "2026-03-10",
                    "--apply",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_update.call_args.kwargs["dry_run"], False)

    @patch(
        "things_ai.cli.write_inbox_question_set_artifacts",
        return_value={
            "json": Path("data/inbox-questions/2026-03-09/example.json"),
            "markdown": Path("data/inbox-questions/2026-03-09/example.md"),
        },
    )
    @patch(
        "things_ai.cli.fetch_inbox_question_set",
        return_value={"schema_version": "things-ai.inbox-questions.v1", "questions": []},
    )
    def test_main_inbox_questions_writes_artifacts_by_default(self, mock_fetch: object, mock_write: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["inbox-questions"])

        self.assertEqual(exit_code, 0)
        mock_fetch.assert_called_once_with(command_text=None)
        mock_write.assert_called_once_with(
            {"schema_version": "things-ai.inbox-questions.v1", "questions": []},
            output_dir=Path("data"),
            prefix="things-inbox-questions",
        )
        self.assertIn('"schema_version": "things-ai.inbox-questions.v1"', stdout.getvalue())

    @patch("things_ai.cli.write_inbox_question_set_artifacts")
    @patch(
        "things_ai.cli.fetch_inbox_question_set",
        return_value={"schema_version": "things-ai.inbox-questions.v1", "questions": []},
    )
    def test_main_inbox_questions_no_write_skips_artifacts(self, mock_fetch: object, mock_write: object) -> None:
        with patch("sys.stdout", io.StringIO()):
            exit_code = main(["inbox-questions", "--no-write"])

        self.assertEqual(exit_code, 0)
        mock_fetch.assert_called_once_with(command_text=None)
        mock_write.assert_not_called()

    @patch("things_ai.cli.write_inbox_answer_review_artifacts")
    @patch(
        "things_ai.cli.review_inbox_answer_file",
        return_value={"schema_version": "things-ai.inbox-answer-review.v1", "counts": {"answered": 1}, "questions": []},
    )
    def test_main_inbox_answers_no_write_skips_artifacts(self, mock_review: object, mock_write: object) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["inbox-answers", "--input-file", "answers.md", "--no-write"])

        self.assertEqual(exit_code, 0)
        mock_review.assert_called_once_with(Path("answers.md"), command_text=None)
        mock_write.assert_not_called()
        self.assertIn('"schema_version": "things-ai.inbox-answer-review.v1"', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()