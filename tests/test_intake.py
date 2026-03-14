from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from things_ai.intake import propose_intake_packet, review_next_packet, start_intake_session


def sample_today_items() -> list[dict[str, object]]:
    return [
        {
            "kind": "todo",
            "uuid": "todo-1",
            "title": "Figure out trip",
            "status": "open",
            "notes": "Need to sort flights.",
            "relationships": {"area_uuid": "area-1", "area_title": "Home"},
        },
        {
            "kind": "todo",
            "uuid": "todo-2",
            "title": "Already done item",
            "status": "completed",
            "relationships": {"area_uuid": "area-1", "area_title": "Home"},
        },
    ]


def sample_snapshot() -> dict[str, object]:
    return {
        "generated_at": "2026-03-11T12:00:00Z",
        "normalized": {
            "areas": [{"kind": "area", "uuid": "area-1", "title": "Home"}],
            "projects": [],
            "todos": [
                {
                    "kind": "todo",
                    "uuid": "todo-1",
                    "title": "Figure out trip",
                    "status": "open",
                    "relationships": {"area_uuid": "area-1", "area_title": "Home"},
                }
            ],
            "tags": [],
        },
    }


def sample_project_intake_decision() -> dict[str, object]:
    return {
        "summary": "Turn this into a small travel planning project with a concrete first call.",
        "reasoning_summary": "The item is project-shaped, but the next useful move is still a single phone call.",
        "interpretation_kind": "project_with_next_action",
        "confidence": "medium",
        "proposed_project": "Plan trip",
        "proposed_outcome": "Trip booked and itinerary shared",
        "proposed_next_action": "Call airline about baggage policy",
        "proposed_supporting_tasks": ["Look up confirmation code", "Find passport number"],
        "proposed_contexts": ["phone", "travel"],
        "proposed_due": "2026-03-20",
        "recommended_home_kind": "new_project",
        "recommended_home_title": "Plan trip",
        "recommended_home_note": "Create a dedicated trip-planning project and keep the phone call as the first next action.",
        "retire_recommendation": "keep_source",
        "manual_review_flags": ["Project title may still need refinement"],
    }


def sample_single_action_intake_decision() -> dict[str, object]:
    return {
        "summary": "Keep this as a single action, but give it a clearer home.",
        "reasoning_summary": "One phone call would finish the commitment, so a full project would be unnecessary overhead.",
        "interpretation_kind": "single_action",
        "confidence": "high",
        "proposed_project": "",
        "proposed_outcome": "",
        "proposed_next_action": "Call airline about baggage policy",
        "proposed_supporting_tasks": ["Look up confirmation code"],
        "proposed_contexts": ["phone"],
        "proposed_due": "",
        "recommended_home_kind": "single_action_project",
        "recommended_home_title": "Home - Single Actions",
        "recommended_home_note": "Keep this as one action, but house it in a single-action project under Home.",
        "retire_recommendation": "keep_source",
        "manual_review_flags": [],
    }


class IntakeWorkflowTests(unittest.TestCase):
    def test_start_intake_session_writes_singleton_packet_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = start_intake_session(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                available_tools=["get_today"],
            )

            session = result["session"]
            artifacts = result["artifacts"]

            self.assertEqual(session["counts"]["total_packets"], 1)
            self.assertEqual(session["counts"]["new_packets"], 1)
            self.assertEqual(session["status"], "active")
            self.assertTrue(Path(artifacts["session_json"]).exists())
            self.assertTrue(Path(artifacts["packet_directory"]).exists())

            packet = json.loads(Path(artifacts["packet_directory"]) .joinpath("packet-001.json").read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "new")
            self.assertEqual(packet["source_items"][0]["title"], "Figure out trip")

    def test_review_next_packet_persists_answers_llm_bundle_and_create_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            start_result = start_intake_session(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                available_tools=["get_today"],
            )
            responses = iter(
                [
                    "1",
                    "Trip booked and itinerary shared",
                    "Call airline about baggage policy",
                    "Need passport number before calling",
                ]
            )
            lines: list[str] = []

            result = review_next_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                snapshot=sample_snapshot(),
                input_func=lambda _prompt: next(responses),
                output_func=lines.append,
            )

            packet = result["packet"]
            session_json = json.loads(Path(result["artifacts"]["session_json"]).read_text(encoding="utf-8"))
            llm_bundle = json.loads(Path(result["artifacts"]["llm_json"]).read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "reviewed")
            self.assertEqual(packet["status"], "reviewed")
            self.assertEqual(packet["review"]["normalized"]["classification"], "project")
            self.assertEqual(packet["review"]["normalized"]["next_action"], "Call airline about baggage policy")
            self.assertEqual(packet["staged_actions"]["create_items"][0]["prepared_request"]["tool"], "add_todo")
            self.assertTrue(
                any("project creation stays manual" in note for note in packet["staged_actions"]["manual_steps"])
            )
            self.assertEqual(llm_bundle["review"]["outcome"], "Trip booked and itinerary shared")
            self.assertEqual(session_json["status"], "complete")
            self.assertEqual(session_json["counts"]["reviewed_packets"], 1)
            self.assertIsNone(session_json["next_packet_id"])
            self.assertTrue(any("Packet packet-001" in line for line in lines))

    def test_review_next_packet_complete_already_stages_retire_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            start_result = start_intake_session(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                available_tools=["get_today"],
            )
            responses = iter(["3", "Finished earlier this week"])

            result = review_next_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                snapshot=sample_snapshot(),
                input_func=lambda _prompt: next(responses),
                output_func=lambda *_args: None,
            )

            retire_preview = result["packet"]["staged_actions"]["retire_legacy_items"][0]
            self.assertEqual(retire_preview["prepared_request"]["tool"], "update_todo")
            self.assertEqual(retire_preview["prepared_request"]["arguments"]["completed"], True)

    def test_propose_intake_packet_writes_request_response_and_proposal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            start_result = start_intake_session(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                available_tools=["get_today"],
            )
            responses = iter(
                [
                    "1",
                    "Trip booked and itinerary shared",
                    "Call airline about baggage policy",
                    "Need passport number before calling",
                ]
            )
            review_next_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                snapshot=sample_snapshot(),
                input_func=lambda _prompt: next(responses),
                output_func=lambda *_args: None,
            )

            result = propose_intake_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                packet_ref="packet-001",
                snapshot=sample_snapshot(),
                decision_text=json.dumps(sample_project_intake_decision()),
            )

            packet = result["packet"]
            session_json = json.loads(Path(result["artifacts"]["session_json"]).read_text(encoding="utf-8"))
            request_bundle = json.loads(Path(result["artifacts"]["request_json"]).read_text(encoding="utf-8"))
            request_markdown = Path(result["artifacts"]["request_markdown"]).read_text(encoding="utf-8")
            response_bundle = json.loads(Path(result["artifacts"]["response_json"]).read_text(encoding="utf-8"))
            response_markdown = Path(result["artifacts"]["response_markdown"]).read_text(encoding="utf-8")
            proposal_bundle = json.loads(Path(result["artifacts"]["proposal_json"]).read_text(encoding="utf-8"))
            proposal_markdown = Path(result["artifacts"]["proposal_markdown"]).read_text(encoding="utf-8")

            self.assertEqual(result["status"], "proposed")
            self.assertEqual(packet["status"], "proposed")
            self.assertEqual(packet["proposal"]["interpretation_kind"], "project_with_next_action")
            self.assertEqual(packet["proposal"]["confidence"], "medium")
            self.assertEqual(packet["proposal"]["recommended_home_kind"], "new_project")
            self.assertEqual(packet["proposal"]["recommended_home_title"], "Plan trip")
            self.assertEqual(request_bundle["request_kind"], "intake-decision")
            self.assertEqual(response_bundle["decision"]["retire_recommendation"], "keep_source")
            self.assertEqual(response_bundle["decision"]["interpretation_kind"], "project_with_next_action")
            self.assertEqual(proposal_bundle["decision"]["proposed_project"], "Plan trip")
            self.assertEqual(proposal_bundle["decision"]["recommended_home_kind"], "new_project")
            self.assertEqual(session_json["counts"]["proposed_packets"], 1)
            self.assertEqual(session_json["status"], "complete")

            create_preview = packet["staged_actions"]["create_items"][0]
            prepared_request = create_preview["prepared_request"]
            self.assertEqual(prepared_request["tool"], "add_todo")
            self.assertEqual(prepared_request["arguments"]["deadline"], "2026-03-20")
            self.assertEqual(
                prepared_request["arguments"]["checklist_items"],
                ["Look up confirmation code", "Find passport number"],
            )
            self.assertTrue(
                any(
                    "Project creation stays manual" in note
                    for note in packet["staged_actions"]["manual_steps"]
                )
            )
            self.assertTrue(
                any("Suggested project outcome" in note for note in packet["staged_actions"]["manual_steps"])
            )
            self.assertIn("## System Prompt", request_markdown)
            self.assertIn("## Response Contract", request_markdown)
            self.assertIn("## Reviewed Intake Bundle", request_markdown)
            self.assertNotIn("## Prompt", request_markdown)
            self.assertNotIn(str(request_bundle["request"]["prompt"]), request_markdown)
            self.assertEqual(
                request_markdown.count("Review the intake bundle and return a conservative JSON proposal."),
                1,
            )
            self.assertIn("## Structure", response_markdown)
            self.assertIn("Project with next action", response_markdown)
            self.assertIn("## Active Work", proposal_markdown)
            self.assertIn("## Supporting Tasks", proposal_markdown)
            self.assertIn("## Contexts", proposal_markdown)
            self.assertIn("New project", proposal_markdown)
            self.assertTrue(Path(result["artifacts"]["request_markdown"]).exists())
            self.assertTrue(Path(result["artifacts"]["response_markdown"]).exists())
            self.assertTrue(Path(result["artifacts"]["proposal_markdown"]).exists())

    def test_propose_intake_packet_supports_single_action_with_home_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            start_result = start_intake_session(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                available_tools=["get_today"],
            )
            responses = iter(
                [
                    "2",
                    "Call airline about baggage policy",
                    "Need passport number before calling",
                ]
            )
            review_next_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                snapshot=sample_snapshot(),
                input_func=lambda _prompt: next(responses),
                output_func=lambda *_args: None,
            )

            result = propose_intake_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                packet_ref="packet-001",
                snapshot=sample_snapshot(),
                decision_text=json.dumps(sample_single_action_intake_decision()),
            )

            packet = result["packet"]
            proposal_markdown = Path(result["artifacts"]["proposal_markdown"]).read_text(encoding="utf-8")

            self.assertEqual(result["status"], "proposed")
            self.assertEqual(packet["proposal"]["interpretation_kind"], "single_action")
            self.assertEqual(packet["proposal"]["recommended_home_kind"], "single_action_project")
            self.assertEqual(packet["proposal"]["recommended_home_title"], "Home - Single Actions")
            self.assertEqual(packet["staged_actions"]["create_items"][0]["prepared_request"]["tool"], "add_todo")
            self.assertTrue(
                any("single-action project" in note.lower() for note in packet["staged_actions"]["manual_steps"])
            )
            self.assertFalse(
                any("Project creation stays manual" in note for note in packet["staged_actions"]["manual_steps"])
            )
            self.assertIn("Single action", proposal_markdown)
            self.assertIn("Single-action project", proposal_markdown)
            self.assertIn("Home - Single Actions", proposal_markdown)

    def test_propose_intake_packet_accepts_fenced_json_with_surrounding_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            start_result = start_intake_session(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                available_tools=["get_today"],
            )
            responses = iter(
                [
                    "1",
                    "Trip booked and itinerary shared",
                    "Call airline about baggage policy",
                    "Need passport number before calling",
                ]
            )
            review_next_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                snapshot=sample_snapshot(),
                input_func=lambda _prompt: next(responses),
                output_func=lambda *_args: None,
            )

            decision_text = (
                "Here is the JSON proposal.\n```json\n"
                + json.dumps(sample_project_intake_decision(), indent=2)
                + "\n```\nThanks!"
            )
            result = propose_intake_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                packet_ref="packet-001",
                snapshot=sample_snapshot(),
                decision_text=decision_text,
            )

            self.assertEqual(result["status"], "proposed")
            self.assertEqual(result["packet"]["proposal"]["interpretation_kind"], "project_with_next_action")
            self.assertTrue(Path(result["artifacts"]["response_json"]).exists())

    def test_propose_intake_packet_writes_response_artifact_when_decision_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            start_result = start_intake_session(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                available_tools=["get_today"],
            )
            responses = iter(
                [
                    "1",
                    "Trip booked and itinerary shared",
                    "Call airline about baggage policy",
                    "Need passport number before calling",
                ]
            )
            review_next_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                snapshot=sample_snapshot(),
                input_func=lambda _prompt: next(responses),
                output_func=lambda *_args: None,
            )

            result = propose_intake_packet(
                output_dir=Path(tmpdir),
                session_ref=start_result["session"]["session_id"],
                packet_ref="packet-001",
                snapshot=sample_snapshot(),
                decision_text="not valid json at all",
            )

            response_bundle = json.loads(Path(result["artifacts"]["response_json"]).read_text(encoding="utf-8"))
            response_markdown = Path(result["artifacts"]["response_markdown"]).read_text(encoding="utf-8")
            self.assertEqual(result["status"], "response_invalid")
            self.assertEqual(result["packet"]["status"], "reviewed")
            self.assertEqual(result["packet"]["proposal"]["status"], "response_invalid")
            self.assertIn("parse_error", result["packet"]["proposal"])
            self.assertNotIn("proposal_json", result["artifacts"])
            self.assertEqual(response_bundle["parse_error"], "intake decision is not valid JSON")
            self.assertEqual(response_bundle["response_text"], "not valid json at all")
            self.assertIn("## Response Text", response_markdown)
            self.assertIn("Parse error: intake decision is not valid JSON", response_markdown)


if __name__ == "__main__":
    unittest.main()