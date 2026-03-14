from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from things_ai.inbox_review import build_inbox_answer_review, parse_inbox_answer_markdown


def sample_snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-03-09T20:00:00Z",
        "normalized": {
            "areas": [
                {"kind": "area", "uuid": "area-1", "title": "Home"},
                {"kind": "area", "uuid": "area-2", "title": "Work"},
            ],
            "projects": [
                {"kind": "project", "uuid": "project-1", "title": "Project Alpha", "relationships": {"area_uuid": "area-1", "area_title": "Home"}},
                {"kind": "project", "uuid": "project-2", "title": "Project Alpha", "relationships": {"area_uuid": "area-2", "area_title": "Work"}},
            ],
            "todos": [
                {"kind": "todo", "uuid": "todo-1", "title": "Follow up", "notes": "Old note", "relationships": {"project_uuid": "project-1", "project_title": "Project Alpha", "area_uuid": "area-1", "area_title": "Home"}},
                {"kind": "todo", "uuid": "todo-2", "title": "Loose task", "notes": "", "relationships": {"area_uuid": "area-1", "area_title": "Home"}},
            ],
            "tags": [],
        },
    }


class InboxReviewTests(unittest.TestCase):
    def test_parse_inbox_answer_markdown_supports_multiline_answers_and_quoted_notes(self) -> None:
        parsed = parse_inbox_answer_markdown(
            """## Q001 — Follow up

question_id: Q001
todo_uuid: todo-1
current_notes:
> first line
> second line
answer_summary:
clarified summary
still summary
answer_next_action: DELETE
answer_notes:
extra detail
"""
        )

        question = parsed["questions"][0]
        self.assertEqual(question["current"]["notes"], "first line\nsecond line")
        self.assertEqual(question["answers"]["summary"], "clarified summary\nstill summary")
        self.assertEqual(question["answers"]["next_action"], "DELETE")
        self.assertEqual(question["answers"]["notes"], "extra detail")

    def test_build_inbox_answer_review_leaves_unanswered_items_untouched(self) -> None:
        parsed = {"questions": [{"question_id": "Q001", "todo_uuid": "todo-1", "title": "Follow up", "sources": ["today"], "answers": {field: "" for field in ("summary", "next_action", "project", "area", "when", "deadline", "notes")}}]}

        review = build_inbox_answer_review(parsed, snapshot=sample_snapshot())

        self.assertEqual(review["counts"]["unanswered"], 1)
        self.assertEqual(review["counts"]["answered"], 0)
        self.assertEqual(review["questions"][0]["status"], "unanswered")

    def test_build_inbox_answer_review_maps_delete_to_manual_handoff(self) -> None:
        parsed = {"questions": [{"question_id": "Q001", "todo_uuid": "todo-1", "title": "Follow up", "sources": ["today"], "answers": {"summary": "", "next_action": "DELETE", "project": "", "area": "", "when": "", "deadline": "", "notes": ""}}]}

        review = build_inbox_answer_review(parsed, snapshot=sample_snapshot())

        question = review["questions"][0]
        self.assertEqual(question["status"], "manual_review")
        self.assertEqual(question["manual_handoff"]["action"], "delete_todo")
        self.assertNotIn("prepared_request", question)

    def test_build_inbox_answer_review_maps_complete_to_completion_only(self) -> None:
        parsed = {"questions": [{"question_id": "Q001", "todo_uuid": "todo-1", "title": "Follow up", "sources": ["today"], "answers": {"summary": "extra context", "next_action": "complete", "project": "", "area": "", "when": "", "deadline": "", "notes": ""}}]}

        review = build_inbox_answer_review(parsed, snapshot=sample_snapshot())

        question = review["questions"][0]
        self.assertEqual(question["status"], "ready")
        self.assertEqual(question["prepared_request"]["arguments"], {"id": "todo-1", "completed": True})
        self.assertIn("complete intent takes precedence", question["notes"][0])
        self.assertEqual(question["command_handoff"]["dry_run"]["argv"][:4], ["python3", "-m", "things_ai", "update-task"])

    def test_build_inbox_answer_review_builds_move_request_from_area_and_project_answers(self) -> None:
        parsed = {"questions": [{"question_id": "Q001", "todo_uuid": "todo-1", "title": "Follow up", "sources": ["today"], "answers": {"summary": "Move this to work", "next_action": "", "project": "Project Alpha", "area": "Work", "when": "Anytime is fine", "deadline": "20260331", "notes": "Keep it visible"}}]}

        review = build_inbox_answer_review(parsed, snapshot=sample_snapshot())

        question = review["questions"][0]
        self.assertEqual(question["status"], "ready")
        self.assertEqual(question["prepared_request"]["arguments"]["list_id"], "project-2")
        self.assertEqual(question["prepared_request"]["arguments"]["when"], "anytime")
        self.assertEqual(question["prepared_request"]["arguments"]["deadline"], "2026-03-31")
        self.assertEqual(question["prepared_request"]["target"]["move_area"]["uuid"], "area-2")
        self.assertEqual(question["prepared_request"]["target"]["move_project"]["uuid"], "project-2")

    def test_build_inbox_answer_review_surfaces_unsupported_schedule_as_partial(self) -> None:
        parsed = {"questions": [{"question_id": "Q001", "todo_uuid": "todo-1", "title": "Follow up", "sources": ["today"], "answers": {"summary": "Clarify this", "next_action": "", "project": "", "area": "", "when": "Next week", "deadline": "", "notes": ""}}]}

        review = build_inbox_answer_review(parsed, snapshot=sample_snapshot())

        question = review["questions"][0]
        self.assertEqual(question["status"], "partial")
        self.assertEqual(question["manual_fields"][0]["field"], "when")
        self.assertIn("Could not auto-apply 'when' value 'Next week'", question["manual_fields"][0]["reason"])
        self.assertEqual(question["prepared_request"]["arguments"]["id"], "todo-1")
        self.assertIn("Inbox clarification (Q001)", question["prepared_request"]["arguments"]["notes"])

    def test_build_inbox_answer_review_downgrades_unresolved_area_to_manual_field(self) -> None:
        snapshot = sample_snapshot()
        snapshot["normalized"]["areas"] = [{"kind": "area", "uuid": "area-2", "title": "📎 Work"}]
        parsed = {"questions": [{"question_id": "Q001", "todo_uuid": "todo-1", "title": "Follow up", "sources": ["today"], "answers": {"summary": "Clarify this", "next_action": "", "project": "", "area": "Work", "when": "", "deadline": "", "notes": ""}}]}

        review = build_inbox_answer_review(parsed, snapshot=snapshot)

        question = review["questions"][0]
        self.assertEqual(question["status"], "partial")
        self.assertEqual(question["manual_fields"][0]["field"], "area")
        self.assertIn("No area matched selector", question["manual_fields"][0]["reason"])
        self.assertEqual(question["prepared_request"]["arguments"]["id"], "todo-1")
        self.assertNotIn("list_id", question["prepared_request"]["arguments"])

    def test_build_inbox_answer_review_downgrades_unresolved_project_to_manual_field(self) -> None:
        parsed = {"questions": [{"question_id": "Q001", "todo_uuid": "todo-1", "title": "Follow up", "sources": ["today"], "answers": {"summary": "Clarify this", "next_action": "", "project": "Missing Project", "area": "", "when": "Anytime is fine", "deadline": "", "notes": ""}}]}

        review = build_inbox_answer_review(parsed, snapshot=sample_snapshot())

        question = review["questions"][0]
        self.assertEqual(question["status"], "partial")
        self.assertEqual(question["manual_fields"][0]["field"], "project")
        self.assertIn("No project matched selector", question["manual_fields"][0]["reason"])
        self.assertEqual(question["prepared_request"]["arguments"]["when"], "anytime")
        self.assertNotIn("list_id", question["prepared_request"]["arguments"])

    def test_build_inbox_answer_review_stringifies_source_paths(self) -> None:
        parsed = {"questions": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            markdown_path = Path(tmpdir) / "answers.md"
            question_set_path = markdown_path.with_suffix(".json")
            markdown_path.write_text("", encoding="utf-8")
            question_set_path.write_text("{}", encoding="utf-8")

            review = build_inbox_answer_review(parsed, snapshot=sample_snapshot(), source_path=markdown_path)

        self.assertEqual(review["source"]["markdown_path"], str(markdown_path))
        self.assertEqual(review["source"]["question_set_path"], str(question_set_path))


if __name__ == "__main__":
    unittest.main()