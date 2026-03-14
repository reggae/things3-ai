from __future__ import annotations

import io
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from things_ai.cli import main
from things_ai.llm_bridge import (
    build_command_handoff,
    build_task_action_proposals,
    build_task_decision_response_contract,
    build_task_decision_prompt,
    build_task_request_bundle,
    build_task_context_payload,
    build_task_context_prompt,
    complete,
    load_dotenv_values,
    parse_json_object,
    render_task_proposal_markdown,
    write_task_proposal_artifacts,
    write_task_request_artifacts,
    write_llm_debug_log,
    write_task_context_artifacts,
)


def sample_snapshot() -> dict[str, object]:
    selected = {
        "kind": "todo",
        "uuid": "todo-2",
        "title": "Selected task",
        "status": "incomplete",
        "notes": "Do the thing",
        "relationships": {
            "project_title": "Project Alpha",
            "area_title": "Home",
            "heading_title": "Next",
        },
    }
    return {
        "generated_at": "2026-03-09T17:00:00Z",
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
                        "todos": [{"kind": "todo", "uuid": "todo-1", "title": "Loose project task"}],
                        "headings": [
                            {
                                "kind": "heading",
                                "uuid": "heading-1",
                                "title": "Next",
                                "children": {"todos": [selected]},
                            }
                        ],
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
                selected,
                {
                    "kind": "todo",
                    "uuid": "todo-3",
                    "title": "Selected task",
                    "relationships": {"project_title": "Project Alpha", "area_title": "Work"},
                },
            ],
            "tags": [],
        },
    }


def sample_decision(*suggested_changes: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "things-ai.task-decision.v1",
        "kind": "task-decision",
        "summary": "Make the task easier to act on.",
        "recommended_action": "prepare suggestions",
        "reasoning": "Use existing safe request builders to preview likely next actions.",
        "suggested_changes": list(suggested_changes),
        "questions": [],
        "risks": [],
    }


class DotenvTests(unittest.TestCase):
    def test_load_dotenv_values_reads_basic_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            path.write_text(
                "# comment\nexport LLM_DEFAULT_MODEL=claude-strong\nLLM_MAX_TOKENS=4096\nOPENAI_API_KEY='abc'\n"
            )
            values = load_dotenv_values(path)
        self.assertEqual(values["LLM_DEFAULT_MODEL"], "claude-strong")
        self.assertEqual(values["LLM_MAX_TOKENS"], "4096")
        self.assertEqual(values["OPENAI_API_KEY"], "abc")


class JsonParsingTests(unittest.TestCase):
    def test_parse_json_object_accepts_fenced_json(self) -> None:
        value = parse_json_object("```json\n{\"summary\": \"ok\"}\n```", label="decision")
        self.assertEqual(value["summary"], "ok")

    def test_parse_json_object_accepts_surrounding_text(self) -> None:
        value = parse_json_object("Here is the JSON you asked for:\n{\"summary\": \"ok\"}\nThanks!", label="decision")
        self.assertEqual(value["summary"], "ok")


class TaskContextPayloadTests(unittest.TestCase):
    def test_build_task_context_payload_derives_project_and_excludes_area_by_default(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        self.assertEqual(payload["selection"]["project"]["uuid"], "project-1")
        self.assertNotIn("area", payload)
        self.assertNotIn("area_title", payload["task"]["relationships"])
        self.assertEqual(payload["project_context"]["headings"][0]["heading"]["uuid"], "heading-1")

    def test_build_task_context_payload_can_include_area(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2", include_area=True)
        self.assertEqual(payload["area"]["uuid"], "area-1")
        self.assertEqual(payload["task"]["relationships"]["area_title"], "Home")

    def test_build_task_context_prompt_wraps_instruction_and_json(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        prompt = build_task_context_prompt(payload, "Decide what to do next.")
        self.assertIn("Decide what to do next.", prompt)
        self.assertIn('"uuid": "todo-2"', prompt)

    def test_build_task_decision_prompt_includes_response_contract(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        prompt = build_task_decision_prompt(payload, "Decide what to do next.")
        self.assertIn("Return JSON only", prompt)
        self.assertIn(TASK_DECISION_RESPONSE_SCHEMA_VERSION := "things-ai.task-decision.v1", prompt)
        self.assertIn('"target_title"', prompt)

    def test_build_task_request_bundle_supports_external_llm_and_augment(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        bundle = build_task_request_bundle(payload, "Decide what to do next.", system="You are helpful.")
        self.assertEqual(bundle["schema_version"], "things-ai.task-request.v1")
        self.assertTrue(bundle["consumer_modes"]["external_llm"])
        self.assertTrue(bundle["consumer_modes"]["augment"])
        self.assertEqual(bundle["request"]["response_contract"]["schema_version"], "things-ai.task-decision.v1")


class ArtifactWriterTests(unittest.TestCase):
    def test_write_task_context_artifacts_writes_json_and_markdown(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_task_context_artifacts(payload, output_root=Path(tmpdir))
            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["markdown"].exists())
            self.assertIn("Selected task", paths["markdown"].read_text(encoding="utf-8"))

    def test_write_task_context_artifacts_truncates_long_filename_slug(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        payload["task"]["title"] = "https://example.com/" + ("very-long-segment/" * 20)
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_task_context_artifacts(payload, output_root=Path(tmpdir))
            self.assertTrue(paths["json"].exists())
            self.assertLessEqual(len(paths["json"].name), 120)

    def test_write_llm_debug_log_writes_markdown(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_task_context_artifacts(payload, output_root=Path(tmpdir))
            log_path = write_llm_debug_log(
                requested_model="claude-strong",
                response="Model reply",
                prompt="Prompt text",
                actions=["create follow-up task"],
                payload=payload,
                payload_path=paths["json"],
                output_root=Path(tmpdir),
            )
            content = log_path.read_text(encoding="utf-8")
        self.assertIn("## Response", content)
        self.assertIn("create follow-up task", content)

    def test_write_task_request_artifacts_writes_json_and_markdown(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        bundle = build_task_request_bundle(payload, "Decide what to do next.")
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_task_request_artifacts(bundle, output_root=Path(tmpdir))
            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["markdown"].exists())
            self.assertIn("Augment", paths["markdown"].read_text(encoding="utf-8"))

    def test_write_task_proposal_artifacts_writes_json_and_markdown(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        bundle = build_task_request_bundle(payload, "Decide what to do next.")
        proposal_bundle = build_task_action_proposals(
            bundle,
            sample_decision(
                {
                    "kind": "update_task",
                    "target_kind": "todo",
                    "notes": "Clarify the next step.",
                    "reason": "A sharper note reduces ambiguity.",
                }
            ),
            snapshot=sample_snapshot(),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_task_proposal_artifacts(proposal_bundle, output_root=Path(tmpdir))
            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["markdown"].exists())
            content = paths["markdown"].read_text(encoding="utf-8")
        self.assertIn("Things AI Task Proposals", content)
        self.assertIn("update_todo", content)


class TaskProposalTests(unittest.TestCase):
    def test_build_task_action_proposals_previews_create_task_with_request_defaults(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        bundle = build_task_request_bundle(payload, "Decide what to do next.")
        proposal_bundle = build_task_action_proposals(
            bundle,
            sample_decision(
                {
                    "kind": "create_task",
                    "target_kind": "new_todo",
                    "title": "Draft the first tiny next step",
                    "notes": "Keep it under ten minutes.",
                    "reason": "A concrete starter task can break inertia.",
                }
            ),
            snapshot=sample_snapshot(),
        )
        proposal = proposal_bundle["proposals"][0]
        self.assertEqual(proposal_bundle["schema_version"], "things-ai.task-proposal.v1")
        self.assertEqual(proposal_bundle["counts"]["ready"], 1)
        self.assertEqual(proposal["status"], "ready")
        self.assertEqual(proposal["prepared_request"]["tool"], "add_todo")
        self.assertEqual(proposal["prepared_request"]["arguments"]["list_id"], "project-1")
        self.assertEqual(proposal["prepared_request"]["arguments"]["heading_id"], "heading-1")
        handoff = proposal["command_handoff"]
        self.assertEqual(handoff["env"], {"PYTHONPATH": "src"})
        self.assertEqual(
            handoff["dry_run"]["argv"],
            [
                "python3",
                "-m",
                "things_ai",
                "create-task",
                "--title",
                "Draft the first tiny next step",
                "--notes",
                "Keep it under ten minutes.",
                "--project-uuid",
                "project-1",
                "--heading-uuid",
                "heading-1",
            ],
        )
        self.assertNotIn("--apply", handoff["dry_run"]["argv"])
        self.assertEqual(handoff["apply"]["argv"][-1], "--apply")
        self.assertIn("Only use the --apply command after human review.", handoff["notes"])

    def test_build_task_action_proposals_previews_update_task_using_selected_task_defaults(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        bundle = build_task_request_bundle(payload, "Decide what to do next.")
        proposal_bundle = build_task_action_proposals(
            bundle,
            sample_decision(
                {
                    "kind": "update_task",
                    "target_kind": "todo",
                    "notes": "Clarify Greg's next step.",
                    "reason": "A sharper note reduces ambiguity.",
                }
            ),
            snapshot=sample_snapshot(),
        )
        proposal = proposal_bundle["proposals"][0]
        self.assertEqual(proposal["status"], "ready")
        self.assertEqual(proposal["prepared_request"]["tool"], "update_todo")
        self.assertEqual(proposal["prepared_request"]["arguments"]["id"], "todo-2")
        self.assertEqual(proposal["prepared_request"]["arguments"]["notes"], "Clarify Greg's next step.")
        handoff = proposal["command_handoff"]
        self.assertIn("--todo-uuid", handoff["dry_run"]["argv"])
        self.assertIn("todo-2", handoff["dry_run"]["argv"])
        self.assertNotIn("--apply", handoff["dry_run"]["shell"])
        self.assertIn("--apply", handoff["apply"]["shell"])
        shell_parts = shlex.split(handoff["dry_run"]["shell"])
        self.assertEqual(shell_parts[0], "PYTHONPATH=src")
        self.assertEqual(shell_parts[1:], handoff["dry_run"]["argv"])

    def test_build_task_action_proposals_previews_add_note_for_project_target(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        bundle = build_task_request_bundle(payload, "Decide what to do next.")
        proposal_bundle = build_task_action_proposals(
            bundle,
            sample_decision(
                {
                    "kind": "add_note",
                    "target_kind": "project",
                    "notes": "Remember the broader outcome before starting.",
                    "reason": "A project note keeps the rationale visible.",
                }
            ),
            snapshot=sample_snapshot(),
        )
        proposal = proposal_bundle["proposals"][0]
        self.assertEqual(proposal["status"], "ready")
        self.assertEqual(proposal["prepared_request"]["tool"], "update_project")
        self.assertEqual(proposal["prepared_request"]["arguments"]["id"], "project-1")
        self.assertEqual(
            proposal["command_handoff"]["dry_run"]["argv"][:6],
            ["python3", "-m", "things_ai", "update-project", "--project-uuid", "project-1"],
        )
        self.assertIn("notes replacement", proposal["notes"][0])
        markdown = render_task_proposal_markdown(proposal_bundle)
        self.assertIn("update_project", markdown)
        self.assertIn("### Review Handoff", markdown)
        self.assertIn("#### Dry Run Command", markdown)
        self.assertIn("#### Apply Command", markdown)

    def test_build_command_handoff_serializes_move_selectors_for_update_todo(self) -> None:
        prepared_request = {
            "tool": "update_todo",
            "arguments": {
                "id": "todo-2",
                "notes": "Move it",
                "list_id": "project-2",
                "heading_id": "heading-2",
            },
            "target": {
                "todo": {"kind": "todo", "uuid": "todo-2", "title": "Selected task"},
                "area": {"kind": "area", "uuid": "area-1", "title": "Home"},
                "project": {"kind": "project", "uuid": "project-1", "title": "Project Alpha"},
                "heading": {"kind": "heading", "uuid": "heading-1", "title": "Next"},
                "move_area": {"kind": "area", "uuid": "area-2", "title": "Work"},
                "move_project": {"kind": "project", "uuid": "project-2", "title": "Project Alpha"},
                "move_heading": {"kind": "heading", "uuid": "heading-2", "title": "Later"},
                "list": {"kind": "project", "uuid": "project-2", "title": "Project Alpha"},
            },
        }

        handoff = build_command_handoff(prepared_request)

        self.assertIn("--todo-uuid", handoff["dry_run"]["argv"])
        self.assertIn("--project-uuid", handoff["dry_run"]["argv"])
        self.assertIn("project-1", handoff["dry_run"]["argv"])
        self.assertIn("--move-area-uuid", handoff["dry_run"]["argv"])
        self.assertIn("area-2", handoff["dry_run"]["argv"])
        self.assertIn("--move-project-uuid", handoff["dry_run"]["argv"])
        self.assertIn("project-2", handoff["dry_run"]["argv"])
        self.assertIn("--move-heading-uuid", handoff["dry_run"]["argv"])
        self.assertIn("heading-2", handoff["dry_run"]["argv"])

    def test_build_task_action_proposals_surfaces_selection_errors_as_proposal_errors(self) -> None:
        payload = build_task_context_payload(sample_snapshot(), todo_uuid="todo-2")
        bundle = build_task_request_bundle(payload, "Decide what to do next.")
        bundle["selection"] = {}
        bundle["task"] = {}
        proposal_bundle = build_task_action_proposals(
            bundle,
            sample_decision(
                {
                    "kind": "update_project",
                    "target_kind": "project",
                    "target_title": "Project Alpha",
                    "notes": "Tighten the project note.",
                    "reason": "This should stay ambiguous without request defaults.",
                }
            ),
            snapshot=sample_snapshot(),
        )
        proposal = proposal_bundle["proposals"][0]
        self.assertEqual(proposal_bundle["counts"]["error"], 1)
        self.assertEqual(proposal["status"], "error")
        self.assertIn("Multiple projects matched selector", proposal["error"])
        self.assertNotIn("command_handoff", proposal)


class TaskContextCliTests(unittest.TestCase):
    @patch("things_ai.cli.write_llm_debug_log", return_value=Path("data/llm/logs/example.md"))
    @patch(
        "things_ai.cli.write_task_context_artifacts",
        return_value={"json": Path("data/llm/task-context/example.json"), "markdown": Path("data/llm/task-context/example.md")},
    )
    @patch("things_ai.cli.fetch_task_context_payload", return_value={"task": {"uuid": "todo-2"}})
    @patch("things_ai.cli.resolve_llm_config", return_value={"default_model": "claude-strong", "artifact_root": "data/llm"})
    def test_main_task_context_writes_payload_and_log(
        self,
        mock_config: object,
        mock_payload: object,
        mock_artifacts: object,
        mock_log: object,
    ) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["task-context", "--todo-uuid", "todo-2", "--prompt", "Hello", "--action", "review"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(mock_payload.call_args.kwargs["todo_uuid"], "todo-2")
        self.assertTrue(mock_artifacts.called)
        self.assertTrue(mock_log.called)
        self.assertIn('"log_path": "data/llm/logs/example.md"', stdout.getvalue())

    @patch("things_ai.cli.write_task_context_artifacts")
    @patch("things_ai.cli.fetch_task_context_payload", return_value={"task": {"uuid": "todo-2"}})
    @patch("things_ai.cli.resolve_llm_config", return_value={"default_model": "claude-strong", "artifact_root": "data/llm"})
    def test_main_task_context_no_write_skips_artifacts(
        self,
        mock_config: object,
        mock_payload: object,
        mock_artifacts: object,
    ) -> None:
        with patch("sys.stdout", io.StringIO()):
            exit_code = main(["task-context", "--todo-uuid", "todo-2", "--no-write", "--no-log"])
        self.assertEqual(exit_code, 0)
        mock_artifacts.assert_not_called()


class CompletionTests(unittest.TestCase):
    def test_complete_dry_run_returns_preview_without_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("LLM_DEFAULT_MODEL=claude-strong\nANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
            result = complete("Hello", system="You are helpful.", env_path=env_path)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["request_preview"]["endpoint"], "https://api.anthropic.com/v1/messages")
        self.assertTrue(result["request_preview"]["api_key_present"])

    def test_complete_execute_uses_transport_and_extracts_anthropic_text(self) -> None:
        def transport(endpoint: str, headers: dict[str, str], body: dict[str, object]) -> dict[str, object]:
            self.assertEqual(endpoint, "https://api.anthropic.com/v1/messages")
            self.assertIn("x-api-key", headers)
            self.assertEqual(body["model"], "claude-sonnet-4-5")
            return {"content": [{"type": "text", "text": "Anthropic reply"}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
            result = complete("Hello", env_path=env_path, execute=True, transport=transport)
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["response_text"], "Anthropic reply")

    def test_complete_execute_uses_transport_and_extracts_openai_text(self) -> None:
        def transport(endpoint: str, headers: dict[str, str], body: dict[str, object]) -> dict[str, object]:
            self.assertEqual(endpoint, "https://api.openai.com/v1/chat/completions")
            self.assertIn("authorization", headers)
            self.assertEqual(body["model"], "gpt-4.1-mini")
            return {"choices": [{"message": {"content": "OpenAI reply"}}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")
            result = complete(
                "Hello",
                model="openai-cheap",
                env_path=env_path,
                execute=True,
                transport=transport,
            )
        self.assertFalse(result["dry_run"])
        self.assertEqual(result["response_text"], "OpenAI reply")


class TaskLlmCliTests(unittest.TestCase):
    @patch("things_ai.cli.write_llm_debug_log", return_value=Path("data/llm/logs/task-llm.md"))
    @patch(
        "things_ai.cli.write_task_request_artifacts",
        return_value={"json": Path("data/llm/task-requests/example.json"), "markdown": Path("data/llm/task-requests/example.md")},
    )
    @patch(
        "things_ai.cli.write_task_context_artifacts",
        return_value={"json": Path("data/llm/task-context/example.json"), "markdown": Path("data/llm/task-context/example.md")},
    )
    @patch("things_ai.cli.complete", return_value={"dry_run": True, "resolved_model": "claude-sonnet-4-5", "provider": "anthropic"})
    @patch("things_ai.cli.build_task_request_bundle", return_value={"request": {"prompt": "Rendered prompt"}})
    @patch("things_ai.cli.fetch_task_context_payload", return_value={"task": {"uuid": "todo-2"}})
    @patch("things_ai.cli.resolve_llm_config", return_value={"default_model": "claude-strong", "artifact_root": "data/llm", "max_tokens": 2048})
    def test_main_task_llm_builds_preview_and_logs(
        self,
        mock_config: object,
        mock_payload: object,
        mock_request_bundle: object,
        mock_complete: object,
        mock_artifacts: object,
        mock_request_artifacts: object,
        mock_log: object,
    ) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["task-llm", "--todo-uuid", "todo-2", "--prompt", "Review this task"])
        self.assertEqual(exit_code, 0)
        self.assertTrue(mock_payload.called)
        self.assertTrue(mock_request_bundle.called)
        self.assertTrue(mock_complete.called)
        self.assertTrue(mock_request_artifacts.called)
        self.assertFalse(mock_complete.call_args.kwargs["execute"])
        self.assertIn('"provider": "anthropic"', stdout.getvalue())
        self.assertIn('"request_artifacts": {', stdout.getvalue())

    @patch("things_ai.cli.complete")
    @patch("things_ai.cli.fetch_task_context_payload", return_value={"task": {"uuid": "todo-2"}})
    @patch("things_ai.cli.resolve_llm_config", return_value={"default_model": "claude-strong", "artifact_root": "data/llm", "max_tokens": 2048})
    def test_main_task_llm_requires_prompt(
        self,
        mock_config: object,
        mock_payload: object,
        mock_complete: object,
    ) -> None:
        with self.assertRaises(SystemExit):
            main(["task-llm", "--todo-uuid", "todo-2", "--no-log", "--no-write"])
        mock_complete.assert_not_called()


class TaskProposalCliTests(unittest.TestCase):
    @patch(
        "things_ai.cli.write_task_proposal_artifacts",
        return_value={"json": Path("data/llm/task-proposals/example.json"), "markdown": Path("data/llm/task-proposals/example.md")},
    )
    @patch(
        "things_ai.cli.build_task_action_proposals",
        return_value={"schema_version": "things-ai.task-proposal.v1", "counts": {"ready": 1}, "proposals": []},
    )
    @patch("things_ai.cli.parse_task_decision", return_value=sample_decision())
    @patch("things_ai.cli.load_task_request_bundle", return_value={"schema_version": "things-ai.task-request.v1", "request_kind": "task-decision", "request": {}})
    @patch("things_ai.cli.resolve_llm_config", return_value={"default_model": "claude-strong", "artifact_root": "data/llm", "max_tokens": 2048})
    def test_main_task_proposals_builds_preview_and_writes_artifacts(
        self,
        mock_config: object,
        mock_request_bundle: object,
        mock_decision: object,
        mock_proposals: object,
        mock_artifacts: object,
    ) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            exit_code = main(["task-proposals", "--request-file", "bundle.json", "--decision", "{}"])
        self.assertEqual(exit_code, 0)
        self.assertTrue(mock_request_bundle.called)
        self.assertTrue(mock_decision.called)
        self.assertTrue(mock_proposals.called)
        self.assertTrue(mock_artifacts.called)
        self.assertIn('"schema_version": "things-ai.task-proposal.v1"', stdout.getvalue())

    def test_main_task_proposals_requires_exactly_one_decision_source(self) -> None:
        with self.assertRaises(SystemExit):
            main(["task-proposals", "--request-file", "bundle.json"])


if __name__ == "__main__":
    unittest.main()