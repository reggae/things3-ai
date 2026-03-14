from __future__ import annotations

import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from things_ai.mcp import StdioMcpClient, extract_tool_payload
from things_ai.snapshot import (
    build_archive_bundle,
    build_inbox_question_set,
    build_restore_plan,
    extract_items,
    find_area,
    find_project,
    normalize_item,
    reconcile_normalized_snapshot,
    resolve_archive_reference,
    render_archive_bundle_markdown,
    render_inbox_question_markdown,
    render_snapshot_markdown,
    select_child_path,
    write_archive_bundle_artifacts,
    write_inbox_question_set_artifacts,
)


def todo_record(
    *,
    title: str,
    uuid: str | None,
    status: str = "incomplete",
    project: str | None = None,
    area: str | None = None,
    heading: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
) -> str:
    lines = [f"Title: {title}"]
    if uuid is not None:
        lines.append(f"UUID: {uuid}")
    lines.extend(["Type: to-do", f"Status: {status}"])
    if project:
        lines.append(f"Project: {project}")
    if area:
        lines.append(f"Area: {area}")
    if heading:
        lines.append(f"Heading: {heading}")
    if when:
        lines.append(f"When: {when}")
    if deadline:
        lines.append(f"Deadline: {deadline}")
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
    if notes:
        lines.append(f"Notes: {notes}")
    return "\n".join(lines)


def text_payload(*records: str) -> dict[str, str]:
    return {"result": "\n\n---\n\n".join(records)}


def normalized_snapshot(
    *,
    area_titles: list[str] | None = None,
    project_title: str | None = None,
    project_area_title: str | None = None,
    heading_titles: list[str] | None = None,
    available_tools: list[str] | None = None,
) -> dict[str, object]:
    projects: list[dict[str, object]] = []
    if project_title is not None:
        projects.append(
            {
                "uuid": "project-1",
                "title": project_title,
                "relationships": {"area_title": project_area_title} if project_area_title else {},
                "children": {"headings": [{"title": title} for title in (heading_titles or [])]},
            }
        )
    return {
        "generated_at": "2026-03-10T12:00:00Z",
        "source": {"integration": "things-mcp", "command": "uvx things-mcp"},
        "available_tools": available_tools or [],
        "normalized": {
            "todos": [],
            "projects": projects,
            "areas": [{"uuid": f"area-{index}", "title": title} for index, title in enumerate(area_titles or [], start=1)],
            "tags": [],
        },
    }


class ExtractToolPayloadTests(unittest.TestCase):
    def test_prefers_structured_content(self) -> None:
        result = {"structuredContent": {"todos": [{"uuid": "1"}]}}
        self.assertEqual(extract_tool_payload(result), {"todos": [{"uuid": "1"}]})

    def test_parses_json_text_content(self) -> None:
        result = {"content": [{"type": "text", "text": '{"items": [1, 2]}'}]}
        self.assertEqual(extract_tool_payload(result), {"items": [1, 2]})


class StdioMcpClientTransportTests(unittest.TestCase):
    def test_send_writes_newline_delimited_json(self) -> None:
        client = StdioMcpClient(command=["fake"])
        stdin = io.BytesIO()
        client._proc = SimpleNamespace(stdin=stdin, stdout=io.BytesIO())

        client._send({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})

        self.assertEqual(
            stdin.getvalue().decode("utf-8"),
            '{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}\n',
        )

    def test_read_message_parses_newline_delimited_json(self) -> None:
        client = StdioMcpClient(command=["fake"])
        client._proc = SimpleNamespace(
            stdin=io.BytesIO(),
            stdout=io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'),
        )

        self.assertEqual(client._read_message()["result"], {"ok": True})

    def test_read_message_supports_content_length_framing(self) -> None:
        client = StdioMcpClient(command=["fake"])
        body = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
        client._proc = SimpleNamespace(
            stdin=io.BytesIO(),
            stdout=io.BytesIO(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body),
        )

        self.assertEqual(client._read_message()["result"], {"ok": True})


class SnapshotHelpersTests(unittest.TestCase):
    def test_extract_items_finds_known_collection_keys(self) -> None:
        payload = {"projects": [{"uuid": "p1"}]}
        self.assertEqual(extract_items(payload), [{"uuid": "p1"}])

    def test_extract_items_parses_plain_text_records(self) -> None:
        payload = {
            "result": (
                "Title: Example todo\n"
                "UUID: todo-1\n"
                "Type: to-do\n"
                "Status: incomplete\n"
                "Project: Project Alpha\n"
                "Area: Home\n"
                "Tags: home, urgent\n"
                "Checklist:\n"
                "  ☐ First step\n"
                "  ☑ Done step\n"
                "Notes: Remember this\n"
                "- and this\n\n"
                "---\n\n"
                "Title: Example tag\n"
                "UUID: tag-1"
            )
        }

        items = extract_items(payload)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Example todo")
        self.assertEqual(items[0]["project"]["title"], "Project Alpha")
        self.assertEqual(items[0]["area"]["title"], "Home")
        self.assertEqual(items[0]["tags"], ["home", "urgent"])
        self.assertEqual(items[0]["notes"], "Remember this\n- and this")
        self.assertEqual(items[0]["items"][0], {"title": "First step", "status": "incomplete"})
        self.assertEqual(items[0]["items"][1], {"title": "Done step", "status": "complete"})
        self.assertEqual(items[1], {"title": "Example tag", "uuid": "tag-1"})

    def test_normalize_item_extracts_relationships_and_tags(self) -> None:
        item = {
            "uuid": "todo-1",
            "title": "Write this down",
            "status": "incomplete",
            "project": {"uuid": "project-1"},
            "area": {"uuid": "area-1"},
            "tags": [{"uuid": "tag-1", "title": "home"}],
            "items": [{"uuid": "sub-1"}, {"uuid": "sub-2"}],
        }
        normalized = normalize_item("todo", item)
        self.assertEqual(normalized["uuid"], "todo-1")
        self.assertEqual(normalized["relationships"]["project_uuid"], "project-1")
        self.assertEqual(normalized["relationships"]["area_uuid"], "area-1")
        self.assertEqual(normalized["relationships"]["tag_names"], ["home"])
        self.assertEqual(normalized["checklist_item_count"], 2)

    def test_normalize_item_keeps_text_relationship_titles(self) -> None:
        item = {
            "uuid": "todo-2",
            "title": "Parsed from text",
            "project": {"title": "Project Alpha"},
            "area": {"title": "Home"},
            "heading": {"title": "Next"},
            "tags": ["urgent"],
            "items": [{"title": "step 1"}],
        }

        normalized = normalize_item("todo", item)

        self.assertEqual(normalized["relationships"]["project_title"], "Project Alpha")
        self.assertEqual(normalized["relationships"]["area_title"], "Home")
        self.assertEqual(normalized["relationships"]["heading_title"], "Next")
        self.assertEqual(normalized["relationships"]["tag_names"], ["urgent"])
        self.assertEqual(normalized["checklist_item_count"], 1)

    def test_normalize_project_preserves_nested_todos_and_headings(self) -> None:
        item = {
            "uuid": "project-1",
            "title": "Project Alpha",
            "tasks": [{"title": "Do the thing", "status": "incomplete"}],
            "headings": [{"title": "Later"}],
            "area": {"title": "Work"},
        }

        normalized = normalize_item("project", item)

        self.assertEqual(normalized["children"]["todos"][0]["title"], "Do the thing")
        self.assertEqual(normalized["children"]["headings"][0]["title"], "Later")
        self.assertEqual(normalized["child_counts"]["todos"], 1)
        self.assertEqual(normalized["child_counts"]["headings"], 1)
        self.assertEqual(normalized["relationships"]["area_title"], "Work")

    def test_normalize_area_preserves_nested_projects_and_todos(self) -> None:
        item = {
            "uuid": "area-1",
            "title": "Home",
            "projects": [{"title": "Project One"}, {"title": "Project Two"}],
            "tasks": [{"title": "Loose task"}],
        }

        normalized = normalize_item("area", item)

        self.assertEqual(normalized["children"]["projects"][0]["title"], "Project One")
        self.assertEqual(normalized["children"]["todos"][0]["title"], "Loose task")
        self.assertEqual(normalized["child_counts"]["projects"], 2)
        self.assertEqual(normalized["child_counts"]["todos"], 1)

    def test_nested_children_inherit_parent_context(self) -> None:
        item = {
            "uuid": "project-1",
            "title": "Project Alpha",
            "area": {"uuid": "area-1", "title": "Work"},
            "tasks": [{"uuid": "todo-1", "title": "Loose task"}],
            "headings": [
                {
                    "uuid": "heading-1",
                    "title": "Later",
                    "tasks": [{"uuid": "todo-2", "title": "Under heading"}],
                }
            ],
        }

        normalized = normalize_item("project", item)
        project_todo = normalized["children"]["todos"][0]
        heading = normalized["children"]["headings"][0]
        heading_todo = heading["children"]["todos"][0]

        self.assertEqual(project_todo["parent"]["uuid"], "project-1")
        self.assertEqual(project_todo["relationships"]["project_uuid"], "project-1")
        self.assertEqual(project_todo["relationships"]["area_uuid"], "area-1")
        self.assertEqual(heading["parent"]["kind"], "project")
        self.assertEqual(heading["relationships"]["project_uuid"], "project-1")
        self.assertEqual(heading["relationships"]["area_uuid"], "area-1")
        self.assertEqual(heading_todo["parent"]["kind"], "heading")
        self.assertEqual(heading_todo["relationships"]["project_uuid"], "project-1")
        self.assertEqual(heading_todo["relationships"]["area_uuid"], "area-1")
        self.assertEqual(heading_todo["relationships"]["heading_uuid"], "heading-1")
        self.assertEqual(heading_todo["relationships"]["heading_title"], "Later")

    def test_find_helpers_and_select_child_path(self) -> None:
        project = {
            "uuid": "project-1",
            "title": "Project One",
            "children": {
                "headings": [
                    {
                        "uuid": "heading-1",
                        "title": "Later",
                        "children": {"todos": [{"uuid": "todo-1", "title": "Thing"}]},
                    }
                ]
            },
        }
        area = {"uuid": "area-1", "title": "Home", "children": {"projects": [project]}}
        snapshot = {
            "normalized": {
                "projects": [project],
                "areas": [area],
                "todos": [],
                "tags": [],
            }
        }

        self.assertEqual(find_project(snapshot, uuid="project-1"), project)
        self.assertEqual(find_area(snapshot, title="Home"), area)
        self.assertEqual(select_child_path(area, "projects"), [project])
        self.assertEqual(select_child_path(project, "headings", "todos"), [{"uuid": "todo-1", "title": "Thing"}])

    def test_reconcile_area_projects_fills_missing_uuid_and_children(self) -> None:
        canonical_project = {
            "uuid": "project-1",
            "title": "Project One",
            "relationships": {"area_title": "Home"},
            "children": {"todos": [{"uuid": "todo-1", "title": "Thing"}]},
            "child_counts": {"todos": 1},
        }
        nested_project = {
            "title": "Project One",
            "parent": {"kind": "area", "uuid": "area-1", "title": "Home"},
            "relationships": {"area_uuid": "area-1", "area_title": "Home"},
        }
        normalized = {
            "projects": [canonical_project],
            "areas": [{"uuid": "area-1", "title": "Home", "children": {"projects": [nested_project]}}],
            "todos": [],
            "tags": [],
        }

        reconciled = reconcile_normalized_snapshot(normalized)
        project = reconciled["areas"][0]["children"]["projects"][0]

        self.assertEqual(project["uuid"], "project-1")
        self.assertEqual(project["children"]["todos"][0]["uuid"], "todo-1")
        self.assertEqual(project["child_counts"]["todos"], 1)
        self.assertEqual(project["parent"]["kind"], "area")

    def test_reconcile_area_projects_skips_ambiguous_match(self) -> None:
        normalized = {
            "projects": [
                {"uuid": "project-1", "title": "Project One", "relationships": {"area_title": "Home"}},
                {"uuid": "project-2", "title": "Project One", "relationships": {"area_title": "Home"}},
            ],
            "areas": [
                {
                    "uuid": "area-1",
                    "title": "Home",
                    "children": {
                        "projects": [
                            {
                                "title": "Project One",
                                "relationships": {"area_uuid": "area-1", "area_title": "Home"},
                            }
                        ]
                    },
                }
            ],
            "todos": [],
            "tags": [],
        }

        reconciled = reconcile_normalized_snapshot(normalized)
        project = reconciled["areas"][0]["children"]["projects"][0]

        self.assertNotIn("uuid", project)

    def test_render_snapshot_markdown_includes_counts(self) -> None:
        snapshot = {
            "generated_at": "2026-03-09T12:00:00Z",
            "source": {"integration": "things-mcp", "command": "uvx things-mcp"},
            "summary": {"todos": 1, "projects": 0, "areas": 0, "tags": 0},
            "normalized": {
                "todos": [{"title": "Test item", "uuid": "todo-1", "status": "incomplete"}],
                "projects": [],
                "areas": [],
                "tags": [],
            },
        }
        markdown = render_snapshot_markdown(snapshot)
        self.assertIn("- todos: 1", markdown)
        self.assertIn("Test item", markdown)


class ArchiveRestoreHelpersTests(unittest.TestCase):
    def test_build_archive_bundle_wraps_snapshot_and_summary(self) -> None:
        snapshot = normalized_snapshot(
            area_titles=["Home"],
            project_title="Project Alpha",
            project_area_title="Home",
            heading_titles=["Next"],
            available_tools=["add_todo", "add_project", "update_todo", "update_project"],
        )

        archive_bundle = build_archive_bundle(snapshot, archive_reason="manual-archive")

        self.assertEqual(archive_bundle["schema_version"], "things-ai.archive.v1")
        self.assertEqual(archive_bundle["kind"], "archive-bundle")
        self.assertEqual(archive_bundle["archive_reason"], "manual-archive")
        self.assertEqual(archive_bundle["summary"], {"todos": 0, "projects": 1, "areas": 1, "tags": 0, "headings": 1})
        markdown = render_archive_bundle_markdown(archive_bundle)
        self.assertIn("# Things Archive", markdown)
        self.assertIn("Full destructive restore is not currently supported", markdown)

    def test_write_archive_bundle_artifacts_writes_partitioned_files(self) -> None:
        archive_bundle = build_archive_bundle(normalized_snapshot(area_titles=["Home"]), archive_reason="manual-archive")

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_archive_bundle_artifacts(archive_bundle, output_dir=Path(tmpdir))
            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["markdown"].exists())
            self.assertEqual(paths["json"].parent.parent.name, "archives")
            self.assertIn('"schema_version": "things-ai.archive.v1"', paths["json"].read_text(encoding="utf-8"))
            self.assertIn("# Things Archive", paths["markdown"].read_text(encoding="utf-8"))

    def test_resolve_archive_reference_accepts_yyyymmdd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_dir = Path(tmpdir) / "archives" / "2026-03-10"
            archive_dir.mkdir(parents=True)
            older = archive_dir / "things-archive-20260310T010101Z.json"
            latest = archive_dir / "things-archive-20260310T020202Z.json"
            older.write_text("{}", encoding="utf-8")
            latest.write_text("{}", encoding="utf-8")

            resolved = resolve_archive_reference("20260310", output_dir=Path(tmpdir))

            self.assertEqual(resolved, latest)

    def test_build_restore_plan_reports_blockers_and_structure_gaps(self) -> None:
        archive_bundle = build_archive_bundle(
            normalized_snapshot(
                area_titles=["Home"],
                project_title="Project Alpha",
                project_area_title="Home",
                heading_titles=["Next"],
                available_tools=["add_todo", "add_project", "update_todo", "update_project"],
            ),
            archive_reason="manual-archive",
        )
        current_snapshot = normalized_snapshot(
            area_titles=["Work"],
            project_title="Project Alpha",
            project_area_title="Work",
            heading_titles=[],
            available_tools=["add_todo", "add_project", "update_todo", "update_project"],
        )

        restore_plan = build_restore_plan(archive_bundle, current_snapshot)

        self.assertEqual(restore_plan["schema_version"], "things-ai.restore-plan.v1")
        self.assertEqual(restore_plan["execution_mode"], "analysis-only")
        self.assertFalse(restore_plan["feasibility"]["destructive_restore_supported"])
        self.assertFalse(restore_plan["feasibility"]["full_fidelity_restore_supported"])
        self.assertEqual(restore_plan["structure_gaps"]["missing_area_titles"], ["Home"])
        self.assertEqual(restore_plan["structure_gaps"]["missing_heading_count"], 1)
        self.assertTrue(any("cannot wipe existing Things data" in reason for reason in restore_plan["blocking_reasons"]))


class InboxQuestionHelpersTests(unittest.TestCase):
    def test_build_inbox_question_set_stacks_today_first_and_dedupes_by_uuid(self) -> None:
        today_payload = text_payload(
            todo_record(title="Today first", uuid="todo-1", project="Project Alpha", area="Home"),
            todo_record(
                title="Shared task",
                uuid="todo-2",
                project="Project Alpha",
                area="Home",
                notes="Clarify the next step",
            ),
        )
        inbox_payload = text_payload(
            todo_record(title="Shared task", uuid="todo-2", project="Project Alpha", area="Home"),
            todo_record(
                title="Inbox later",
                uuid="todo-3",
                area="Home",
                when="Anytime",
                tags=["home", "errands"],
            ),
        )

        question_set = build_inbox_question_set(
            today_payload=today_payload,
            inbox_payload=inbox_payload,
            available_tools=["get_today", "get_inbox"],
        )

        self.assertEqual(question_set["schema_version"], "things-ai.inbox-questions.v1")
        self.assertEqual(question_set["counts"], {"today": 2, "inbox": 2, "questions": 3, "today_first_questions": 2})
        self.assertEqual([q["question_id"] for q in question_set["questions"]], ["Q001", "Q002", "Q003"])
        self.assertEqual([q["todo"]["uuid"] for q in question_set["questions"]], ["todo-1", "todo-2", "todo-3"])
        self.assertEqual(question_set["questions"][1]["sources"], ["today", "inbox"])
        self.assertEqual(question_set["questions"][1]["answers"]["next_action"], "")

    def test_build_inbox_question_set_dedupes_without_uuid_using_scope(self) -> None:
        today_payload = text_payload(todo_record(title="Review notes", uuid=None, project="Project Alpha", area="Home"))
        inbox_payload = text_payload(
            todo_record(title="Review notes", uuid=None, project="Project Alpha", area="Home"),
            todo_record(title="Review notes", uuid=None, project="Project Alpha", area="Work"),
        )

        question_set = build_inbox_question_set(today_payload=today_payload, inbox_payload=inbox_payload)

        self.assertEqual(question_set["counts"]["questions"], 2)
        self.assertEqual(question_set["questions"][0]["sources"], ["today", "inbox"])
        self.assertEqual(question_set["questions"][1]["todo"]["relationships"]["area_title"], "Work")

    def test_render_inbox_question_markdown_includes_answer_fields_and_metadata(self) -> None:
        question_set = build_inbox_question_set(
            today_payload=text_payload(
                todo_record(
                    title="Clarify margin task",
                    uuid="todo-9",
                    project="Pricing",
                    area="Work",
                    heading="Next",
                    when="Today",
                    deadline="2026-03-10",
                    tags=["important"],
                    notes="Need a cleaner summary",
                )
            ),
            inbox_payload=text_payload(),
        )

        markdown = render_inbox_question_markdown(question_set)

        self.assertIn("# Things Inbox Questions", markdown)
        self.assertIn("question_id: Q001", markdown)
        self.assertIn("sources: today", markdown)
        self.assertIn("current_project: Pricing", markdown)
        self.assertIn("current_heading: Next", markdown)
        self.assertIn("> Need a cleaner summary", markdown)
        self.assertIn("answer_summary: ", markdown)
        self.assertIn("answer_next_action: ", markdown)
        self.assertIn("answer_notes:", markdown)

    def test_write_inbox_question_set_artifacts_writes_date_partitioned_json_and_markdown(self) -> None:
        question_set = build_inbox_question_set(
            today_payload=text_payload(todo_record(title="Today first", uuid="todo-1")),
            inbox_payload=text_payload(todo_record(title="Inbox later", uuid="todo-2")),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_inbox_question_set_artifacts(question_set, output_dir=Path(tmpdir))
            json_text = paths["json"].read_text(encoding="utf-8")
            markdown_text = paths["markdown"].read_text(encoding="utf-8")
            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["markdown"].exists())
            self.assertEqual(paths["json"].parent.parent.name, "inbox-questions")
            self.assertIn('"schema_version": "things-ai.inbox-questions.v1"', json_text)
            self.assertIn("# Things Inbox Questions", markdown_text)


if __name__ == "__main__":
    unittest.main()