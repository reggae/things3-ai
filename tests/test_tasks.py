from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from things_ai.tasks import accept_task, list_tasks, next_task, open_task, review_task, show_task, sync_task_store


def sample_today_items() -> list[dict[str, object]]:
    return [
        {
            "kind": "todo",
            "uuid": "todo-1",
            "title": "Clarify revenue notes",
            "status": "open",
            "notes": "Need to reconcile the pricing language.",
            "relationships": {
                "area_title": "Work",
                "project_title": "Discount Revenue",
                "tag_names": ["docs"],
            },
        },
        {
            "kind": "todo",
            "uuid": "todo-2",
            "title": "Buy more coffee filters",
            "status": "open",
            "notes": "Check pantry first.",
            "relationships": {"area_title": "Home", "tag_names": ["errands"]},
        },
    ]


def sample_inbox_items() -> list[dict[str, object]]:
    return [
        {
            "kind": "todo",
            "uuid": "todo-1",
            "title": "Clarify revenue notes",
            "status": "open",
            "notes": "Need to reconcile the pricing language.",
            "relationships": {
                "area_title": "Work",
                "project_title": "Discount Revenue",
                "tag_names": ["docs"],
            },
        },
        {
            "kind": "todo",
            "uuid": "todo-3",
            "title": "Figure out dentist reschedule",
            "status": "open",
            "notes": "Need a Friday option.",
            "relationships": {"area_title": "Home"},
        },
    ]


class TaskStoreTests(unittest.TestCase):
    def test_sync_task_store_writes_items_documents_and_dedupes_today_plus_inbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = sync_task_store(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            items = result["items"]
            self.assertEqual([item["key"] for item in items], ["T-001", "T-002", "T-003"])
            self.assertEqual(items[0]["sources"], ["today", "inbox"])

            index = json.loads(Path(tmpdir, "tasks", "index.json").read_text(encoding="utf-8"))
            task_json = json.loads(Path(tmpdir, "tasks", "items", "T-001.json").read_text(encoding="utf-8"))
            task_markdown = Path(tmpdir, "tasks", "items", "T-001.md").read_text(encoding="utf-8")

            self.assertEqual(index["next_key_number"], 4)
            self.assertEqual(index["source_index"]["uuid:todo-1"], "T-001")
            self.assertEqual(task_json["project"], "Discount Revenue")
            self.assertIn("## Original Capture", task_markdown)
            self.assertIn("Clarify revenue notes", task_markdown)
            self.assertIn("Need to reconcile the pricing language.", task_markdown)

    def test_sync_task_store_preserves_stable_keys_across_resync(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sync_task_store(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            updated_today = sample_today_items()
            updated_today[0] = {
                **updated_today[0],
                "title": "Clarify revenue documentation",
            }
            updated_inbox = sample_inbox_items() + [
                {
                    "kind": "todo",
                    "uuid": "todo-4",
                    "title": "Call the insurance office",
                    "status": "open",
                    "relationships": {"area_title": "Home"},
                }
            ]

            result = sync_task_store(
                output_dir=Path(tmpdir),
                today_items=updated_today,
                inbox_items=updated_inbox,
                available_tools=["get_today", "get_inbox"],
            )

            keys_by_source = {item["source_uuid"]: item["key"] for item in result["items"]}
            self.assertEqual(keys_by_source["todo-1"], "T-001")
            self.assertEqual(keys_by_source["todo-4"], "T-004")

    def test_list_next_and_show_support_cached_numeric_selectors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            listed = list_tasks(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )
            self.assertIn("1 | 📎 Work / Discount Revenue | T-001 | Clarify revenue notes", listed["rendered"])
            self.assertIn("2 | 📎 Home | T-002 | Buy more coffee filters", listed["rendered"])

            next_result = next_task(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )
            self.assertIn("Actions: r review | o open | d done | x retire | q quit", next_result["rendered"])

            shown = show_task(output_dir=Path(tmpdir), selector="2")
            self.assertIn("2 | 📎 Home | T-002 | Buy more coffee filters", shown["rendered"])
            self.assertIn("## Original Capture", shown["rendered"])

    def test_review_task_updates_document_and_transitions_to_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            list_tasks(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            answers = iter(
                [
                    "project",
                    "Clean up the revenue messaging work",
                    "Work",
                    "Draft the corrected pricing note",
                    "Revenue messaging is consistent across the pricing docs",
                    "Review the pricing deck\nUpdate the reference notes",
                    "Waiting on one finance confirmation",
                ]
            )

            reviewed = review_task(output_dir=Path(tmpdir), selector="1", input_func=lambda prompt: next(answers))
            item = reviewed["task"]
            task_json = json.loads(Path(tmpdir, "tasks", "items", "T-001.json").read_text(encoding="utf-8"))
            task_markdown = Path(tmpdir, "tasks", "items", "T-001.md").read_text(encoding="utf-8")

            self.assertEqual(item["state"], "proposed")
            self.assertEqual(item["kind"], "project")
            self.assertEqual(task_json["title"], "Clean up the revenue messaging work")
            self.assertEqual(task_json["area"], "Work")
            self.assertIn("# Task Review", reviewed["rendered"])
            self.assertIn("Outcome: Revenue messaging is consistent across the pricing docs", reviewed["rendered"])
            self.assertIn("## Outcome", task_markdown)
            self.assertIn("Revenue messaging is consistent across the pricing docs", task_markdown)
            self.assertIn("## Next Action", task_markdown)
            self.assertIn("Draft the corrected pricing note", task_markdown)
            self.assertIn("## Steps", task_markdown)
            self.assertIn("Update the reference notes", task_markdown)
            self.assertIn("### Review ", task_markdown)
            self.assertIn("Waiting on one finance confirmation", task_markdown)
            self.assertIn("## Original Capture", task_markdown)
            self.assertIn("Need to reconcile the pricing language.", task_markdown)

    def test_open_task_launches_editor_and_skips_ai_polish_when_user_declines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            list_tasks(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            opened: list[Path] = []
            result = open_task(
                output_dir=Path(tmpdir),
                selector="1",
                editor_func=lambda path: opened.append(path),
                input_func=lambda prompt: "n",
            )

            item = result["task"]
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0].name, "T-001.md")
            self.assertFalse(result["polished"])
            self.assertEqual(item["state"], "new")
            self.assertIn("# Task Open", result["rendered"])
            self.assertIn("AI polish: skipped.", result["rendered"])

    def test_open_task_polishes_document_and_preserves_original_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            list_tasks(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            def editor(path: Path) -> None:
                path.write_text(
                    "\n".join(
                        [
                            "# Revenue cleanup draft",
                            "",
                            "- key: T-001",
                            "- state: new",
                            "- kind: unknown",
                            "- area: Work",
                            "- project: Discount Revenue",
                            "- tags: docs",
                            "- source_uuid: todo-1",
                            "",
                            "## Outcome",
                            "",
                            "Messaging is consistent everywhere.",
                            "",
                            "## Next Action",
                            "",
                            "Draft the corrected pricing note.",
                            "",
                            "## Steps",
                            "",
                            "Review the deck",
                            "",
                            "## Notes",
                            "",
                            "Need one finance confirmation.",
                            "",
                            "## Original Capture",
                            "",
                            "Clarify revenue notes",
                            "",
                            "Need to reconcile the pricing language.",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )

            polished_document = "\n".join(
                [
                    "```markdown",
                    "# Clean up revenue messaging",
                    "",
                    "- key: T-001",
                    "- state: proposed",
                    "- kind: project",
                    "- area: Work",
                    "- project: Pricing Cleanup",
                    "- tags: docs",
                    "- source_uuid: todo-1",
                    "",
                    "## Outcome",
                    "",
                    "Revenue messaging is aligned across pricing docs.",
                    "",
                    "## Next Action",
                    "",
                    "Draft the corrected pricing note and send it for review.",
                    "",
                    "## Steps",
                    "",
                    "Review the pricing deck\nUpdate the reference notes",
                    "",
                    "## Notes",
                    "",
                    "Waiting on finance confirmation.",
                    "",
                    "## Original Capture",
                    "",
                    "THIS SHOULD NOT SURVIVE",
                    "```",
                ]
            )

            result = open_task(
                output_dir=Path(tmpdir),
                selector="1",
                editor_func=editor,
                input_func=lambda prompt: "",
                polish_func=lambda item, document: polished_document,
            )

            item = result["task"]
            task_json = json.loads(Path(tmpdir, "tasks", "items", "T-001.json").read_text(encoding="utf-8"))
            task_markdown = Path(tmpdir, "tasks", "items", "T-001.md").read_text(encoding="utf-8")

            self.assertTrue(result["polished"])
            self.assertEqual(item["state"], "proposed")
            self.assertEqual(item["kind"], "project")
            self.assertEqual(task_json["title"], "Clean up revenue messaging")
            self.assertEqual(task_json["project"], "Pricing Cleanup")
            self.assertIn("AI polish: applied.", result["rendered"])
            self.assertIn("Outcome: Revenue messaging is aligned across pricing docs.", result["rendered"])
            self.assertIn("## Original Capture", task_markdown)
            self.assertIn("Clarify revenue notes", task_markdown)
            self.assertIn("Need to reconcile the pricing language.", task_markdown)
            self.assertNotIn("THIS SHOULD NOT SURVIVE", task_markdown)

    def test_open_task_polish_normalizes_combined_single_actions_home_for_project_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            list_tasks(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            polished_document = "\n".join(
                [
                    "```markdown",
                    "# Price Detection for Delivery - Base Price + Current Margin",
                    "",
                    "- key: T-001",
                    "- state: proposed",
                    "- kind: project",
                    "- area: Product",
                    "- project: Product / Single Actions",
                    "- tags: docs",
                    "- source_uuid: todo-1",
                    "",
                    "## Outcome",
                    "",
                    "Delivery and Sales have signed off on the logic.",
                    "",
                    "## Next Action",
                    "",
                    "Document the full logic and equations.",
                    "",
                    "## Steps",
                    "",
                    "Review with Joao\nPresent to Delivery and Sales",
                    "",
                    "## Notes",
                    "",
                    "Keep the underlying assumptions visible.",
                    "",
                    "## Original Capture",
                    "",
                    "Price Detection for Delivery - Base Price + Current Margin",
                    "",
                    "https://example.test/spec-sheet",
                    "```",
                ]
            )

            result = open_task(
                output_dir=Path(tmpdir),
                selector="1",
                editor_func=lambda path: None,
                input_func=lambda prompt: "",
                polish_func=lambda item, document: polished_document,
            )

            task_json = json.loads(Path(tmpdir, "tasks", "items", "T-001.json").read_text(encoding="utf-8"))
            task_markdown = Path(tmpdir, "tasks", "items", "T-001.md").read_text(encoding="utf-8")

            self.assertEqual(result["task"]["area"], "Product")
            self.assertEqual(result["task"]["project"], "Price Detection for Delivery - Base Price + Current Margin")
            self.assertEqual(task_json["project"], "Price Detection for Delivery - Base Price + Current Margin")
            self.assertIn("- project: Price Detection for Delivery - Base Price + Current Margin", task_markdown)

    def test_accept_task_creates_fallback_single_actions_home_and_marks_item_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            list_tasks(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            answers = iter(
                [
                    "task",
                    "Buy more coffee filters",
                    "Home",
                    "Buy coffee filters at the store",
                    "yes",
                    "",
                    "Buy coffee filters at the store",
                ]
            )
            review_task(output_dir=Path(tmpdir), selector="2", input_func=lambda prompt: next(answers))

            created_projects: list[dict[str, object]] = []
            updated_todos: list[dict[str, object]] = []
            result = accept_task(
                output_dir=Path(tmpdir),
                selector="2",
                create_project_func=lambda **kwargs: created_projects.append(kwargs) or {"status": "created"},
                update_todo_func=lambda **kwargs: updated_todos.append(kwargs) or {"status": "updated"},
                project_lookup_func=lambda area_title, project_title, command_text: None,
            )

            task_json = json.loads(Path(tmpdir, "tasks", "items", "T-002.json").read_text(encoding="utf-8"))
            task_markdown = Path(tmpdir, "tasks", "items", "T-002.md").read_text(encoding="utf-8")

            self.assertEqual(result["task"]["state"], "active")
            self.assertEqual(task_json["project"], "Single Actions")
            self.assertEqual(len(created_projects), 1)
            self.assertEqual(created_projects[0]["title"], "Single Actions")
            self.assertEqual(created_projects[0]["area_title"], "Home")
            self.assertEqual(created_projects[0]["dry_run"], False)
            self.assertEqual(updated_todos[0]["todo_uuid"], "todo-2")
            self.assertEqual(updated_todos[0]["title"], "Buy coffee filters at the store")
            self.assertEqual(updated_todos[0]["move_project_title"], "Single Actions")
            self.assertEqual(updated_todos[0]["move_area_title"], "Home")
            self.assertIn("# Task Accept", result["rendered"])
            self.assertIn("Target Home: 📎 Home / Single Actions", result["rendered"])
            self.assertIn("Additional steps kept in notes for now: no", result["rendered"])
            self.assertIn("- state: active", task_markdown)

    def test_accept_task_ensures_project_and_updates_next_action_for_project_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            list_tasks(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            def editor(path: Path) -> None:
                path.write_text(
                    "\n".join(
                        [
                            "# Clean up revenue messaging",
                            "",
                            "- key: T-001",
                            "- state: proposed",
                            "- kind: project",
                            "- area: Work",
                            "- project: Pricing Cleanup",
                            "- tags: docs",
                            "- source_uuid: todo-1",
                            "",
                            "## Outcome",
                            "",
                            "Revenue messaging is aligned across pricing docs.",
                            "",
                            "## Next Action",
                            "",
                            "Draft the corrected pricing note and send it for review.",
                            "",
                            "## Steps",
                            "",
                            "Review the pricing deck\nUpdate the reference notes",
                            "",
                            "## Notes",
                            "",
                            "Waiting on finance confirmation.",
                            "",
                            "## Original Capture",
                            "",
                            "Clarify revenue notes",
                            "",
                            "Need to reconcile the pricing language.",
                            "",
                        ]
                    ),
                    encoding="utf-8",
                )

            open_task(
                output_dir=Path(tmpdir),
                selector="1",
                editor_func=editor,
                input_func=lambda prompt: "n",
            )
            item_path = Path(tmpdir, "tasks", "items", "T-001.json")
            item = json.loads(item_path.read_text(encoding="utf-8"))
            item["state"] = "proposed"
            item["kind"] = "project"
            item_path.write_text(json.dumps(item, indent=2, sort_keys=True) + "\n", encoding="utf-8")

            created_projects: list[dict[str, object]] = []
            updated_todos: list[dict[str, object]] = []
            result = accept_task(
                output_dir=Path(tmpdir),
                selector="1",
                create_project_func=lambda **kwargs: created_projects.append(kwargs) or {"status": "created"},
                update_todo_func=lambda **kwargs: updated_todos.append(kwargs) or {"status": "updated"},
                project_lookup_func=lambda area_title, project_title, command_text: None,
            )

            task_json = json.loads(item_path.read_text(encoding="utf-8"))
            self.assertEqual(task_json["state"], "active")
            self.assertEqual(task_json["kind"], "project")
            self.assertEqual(task_json["project"], "Pricing Cleanup")
            self.assertEqual(created_projects[0]["title"], "Pricing Cleanup")
            self.assertEqual(created_projects[0]["notes"].splitlines()[0], "Outcome")
            self.assertEqual(updated_todos[0]["todo_uuid"], "todo-1")
            self.assertEqual(updated_todos[0]["title"], "Draft the corrected pricing note and send it for review.")
            self.assertEqual(updated_todos[0]["move_project_title"], "Pricing Cleanup")
            self.assertIn("Final Kind: project", result["rendered"])
            self.assertIn("Project Title: Pricing Cleanup", result["rendered"])
            self.assertIn("Additional steps kept in notes for now: yes", result["rendered"])

    def test_accept_task_recovers_project_title_from_single_actions_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            list_tasks(
                output_dir=Path(tmpdir),
                today_items=sample_today_items(),
                inbox_items=sample_inbox_items(),
                available_tools=["get_today", "get_inbox"],
            )

            item_path = Path(tmpdir, "tasks", "items", "T-001.json")
            doc_path = Path(tmpdir, "tasks", "items", "T-001.md")
            item = json.loads(item_path.read_text(encoding="utf-8"))
            item["title"] = "Price Detection for Delivery - Base Price + Current Margin"
            item["kind"] = "project"
            item["area"] = "Product"
            item["project"] = "Product / Single Actions"
            item["state"] = "proposed"
            item_path.write_text(json.dumps(item, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            doc_path.write_text(
                "\n".join(
                    [
                        "# Price Detection for Delivery - Base Price + Current Margin",
                        "",
                        "- key: T-001",
                        "- state: proposed",
                        "- kind: project",
                        "- area: Product",
                        "- project: Product / Single Actions",
                        "- tags: docs",
                        f"- source_uuid: {item['source_uuid']}",
                        "",
                        "## Outcome",
                        "",
                        "Delivery and Sales have signed off on the logic.",
                        "",
                        "## Next Action",
                        "",
                        "Document the full logic and equations.",
                        "",
                        "## Steps",
                        "",
                        "Review with Joao\nPresent to Delivery and Sales",
                        "",
                        "## Notes",
                        "",
                        "Keep the underlying assumptions visible.",
                        "",
                        "## Original Capture",
                        "",
                        "Price Detection for Delivery - Base Price + Current Margin",
                        "",
                        "https://example.test/spec-sheet",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            created_projects: list[dict[str, object]] = []
            updated_todos: list[dict[str, object]] = []
            result = accept_task(
                output_dir=Path(tmpdir),
                selector="1",
                create_project_func=lambda **kwargs: created_projects.append(kwargs) or {"status": "created"},
                update_todo_func=lambda **kwargs: updated_todos.append(kwargs) or {"status": "updated"},
                project_lookup_func=lambda area_title, project_title, command_text: None,
            )

            task_json = json.loads(item_path.read_text(encoding="utf-8"))
            self.assertEqual(task_json["project"], "Price Detection for Delivery - Base Price + Current Margin")
            self.assertEqual(created_projects[0]["title"], "Price Detection for Delivery - Base Price + Current Margin")
            self.assertEqual(updated_todos[0]["move_project_title"], "Price Detection for Delivery - Base Price + Current Margin")
            self.assertIn("Project Title: Price Detection for Delivery - Base Price + Current Margin", result["rendered"])