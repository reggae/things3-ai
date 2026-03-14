from __future__ import annotations

from typing import Any

from .mcp import StdioMcpClient
from .snapshot import fetch_snapshot, select_child_path


class SelectionError(ValueError):
    """Raised when canonical selectors match zero or multiple Things items."""


def create_project(
    *,
    title: str,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    todos: list[str] | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
    dry_run: bool = True,
    command_text: str | None = None,
) -> dict[str, Any]:
    snapshot = fetch_snapshot(command_text=command_text)
    request = prepare_create_project_request(
        snapshot,
        title=title,
        notes=notes,
        when=when,
        deadline=deadline,
        tags=tags,
        todos=todos,
        area_uuid=area_uuid,
        area_title=area_title,
    )
    result = {
        "dry_run": dry_run,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "request": request,
    }
    if dry_run:
        return result

    with StdioMcpClient.from_environment(command_text=command_text) as client:
        result["response"] = client.call_tool("add_project", request["arguments"])
    return result


def create_todo(
    *,
    title: str,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    checklist_items: list[str] | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
    project_uuid: str | None = None,
    project_title: str | None = None,
    heading_uuid: str | None = None,
    heading_title: str | None = None,
    dry_run: bool = True,
    command_text: str | None = None,
) -> dict[str, Any]:
    snapshot = fetch_snapshot(command_text=command_text)
    request = prepare_create_todo_request(
        snapshot,
        title=title,
        notes=notes,
        when=when,
        deadline=deadline,
        tags=tags,
        checklist_items=checklist_items,
        area_uuid=area_uuid,
        area_title=area_title,
        project_uuid=project_uuid,
        project_title=project_title,
        heading_uuid=heading_uuid,
        heading_title=heading_title,
    )
    result = {
        "dry_run": dry_run,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "request": request,
    }
    if dry_run:
        return result

    with StdioMcpClient.from_environment(command_text=command_text) as client:
        result["response"] = client.call_tool("add_todo", request["arguments"])
    return result


def update_todo(
    *,
    todo_uuid: str | None = None,
    todo_title: str | None = None,
    title: str | None = None,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    completed: bool | None = None,
    canceled: bool | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
    project_uuid: str | None = None,
    project_title: str | None = None,
    heading_uuid: str | None = None,
    heading_title: str | None = None,
    move_area_uuid: str | None = None,
    move_area_title: str | None = None,
    move_project_uuid: str | None = None,
    move_project_title: str | None = None,
    move_heading_uuid: str | None = None,
    move_heading_title: str | None = None,
    dry_run: bool = True,
    command_text: str | None = None,
) -> dict[str, Any]:
    snapshot = fetch_snapshot(command_text=command_text)
    request = prepare_update_todo_request(
        snapshot,
        todo_uuid=todo_uuid,
        todo_title=todo_title,
        title=title,
        notes=notes,
        when=when,
        deadline=deadline,
        tags=tags,
        completed=completed,
        canceled=canceled,
        area_uuid=area_uuid,
        area_title=area_title,
        project_uuid=project_uuid,
        project_title=project_title,
        heading_uuid=heading_uuid,
        heading_title=heading_title,
        move_area_uuid=move_area_uuid,
        move_area_title=move_area_title,
        move_project_uuid=move_project_uuid,
        move_project_title=move_project_title,
        move_heading_uuid=move_heading_uuid,
        move_heading_title=move_heading_title,
    )
    result = {
        "dry_run": dry_run,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "request": request,
    }
    if dry_run:
        return result

    with StdioMcpClient.from_environment(command_text=command_text) as client:
        result["response"] = client.call_tool("update_todo", request["arguments"])
    return result


def update_project(
    *,
    project_uuid: str | None = None,
    project_title: str | None = None,
    title: str | None = None,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    completed: bool | None = None,
    canceled: bool | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
    dry_run: bool = True,
    command_text: str | None = None,
) -> dict[str, Any]:
    snapshot = fetch_snapshot(command_text=command_text)
    request = prepare_update_project_request(
        snapshot,
        project_uuid=project_uuid,
        project_title=project_title,
        title=title,
        notes=notes,
        when=when,
        deadline=deadline,
        tags=tags,
        completed=completed,
        canceled=canceled,
        area_uuid=area_uuid,
        area_title=area_title,
    )
    result = {
        "dry_run": dry_run,
        "snapshot_generated_at": snapshot.get("generated_at"),
        "request": request,
    }
    if dry_run:
        return result

    with StdioMcpClient.from_environment(command_text=command_text) as client:
        result["response"] = client.call_tool("update_project", request["arguments"])
    return result


def prepare_create_todo_request(
    snapshot: dict[str, Any],
    *,
    title: str,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    checklist_items: list[str] | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
    project_uuid: str | None = None,
    project_title: str | None = None,
    heading_uuid: str | None = None,
    heading_title: str | None = None,
) -> dict[str, Any]:
    area = resolve_area(snapshot, area_uuid=area_uuid, area_title=area_title)
    project = resolve_project(
        snapshot,
        project_uuid=project_uuid,
        project_title=project_title,
        area=area,
    )
    heading = resolve_heading(project, heading_uuid=heading_uuid, heading_title=heading_title)
    list_target = project or area

    arguments = compact_dict(
        {
            "title": title,
            "notes": notes,
            "when": when,
            "deadline": deadline,
            "tags": tags,
            "checklist_items": checklist_items,
        }
    )
    add_selector_arguments(arguments, list_target, id_key="list_id", title_key="list_title")
    add_selector_arguments(arguments, heading, id_key="heading_id", title_key="heading")

    return {
        "tool": "add_todo",
        "arguments": arguments,
        "target": compact_dict(
            {
                "list": describe_item(list_target),
                "area": describe_item(area),
                "project": describe_item(project),
                "heading": describe_item(heading),
            }
        ),
    }


def prepare_create_project_request(
    snapshot: dict[str, Any],
    *,
    title: str,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    todos: list[str] | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
) -> dict[str, Any]:
    area = resolve_area(snapshot, area_uuid=area_uuid, area_title=area_title)
    arguments = compact_dict(
        {
            "title": title,
            "notes": notes,
            "when": when,
            "deadline": deadline,
            "tags": tags,
            "todos": todos,
        }
    )
    add_selector_arguments(arguments, area, id_key="area_id", title_key="area_title")
    return {
        "tool": "add_project",
        "arguments": arguments,
        "target": compact_dict(
            {
                "area": describe_item(area),
            }
        ),
    }


def prepare_update_todo_request(
    snapshot: dict[str, Any],
    *,
    todo_uuid: str | None = None,
    todo_title: str | None = None,
    title: str | None = None,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    completed: bool | None = None,
    canceled: bool | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
    project_uuid: str | None = None,
    project_title: str | None = None,
    heading_uuid: str | None = None,
    heading_title: str | None = None,
    move_area_uuid: str | None = None,
    move_area_title: str | None = None,
    move_project_uuid: str | None = None,
    move_project_title: str | None = None,
    move_heading_uuid: str | None = None,
    move_heading_title: str | None = None,
) -> dict[str, Any]:
    validate_status_flags(completed=completed, canceled=canceled)
    area = resolve_area(snapshot, area_uuid=area_uuid, area_title=area_title)
    project = resolve_project(
        snapshot,
        project_uuid=project_uuid,
        project_title=project_title,
        area=area,
    )
    heading = resolve_heading(project, heading_uuid=heading_uuid, heading_title=heading_title)
    todo = resolve_todo(
        snapshot,
        todo_uuid=todo_uuid,
        todo_title=todo_title,
        area=area,
        project=project,
        heading=heading,
    )
    move_area = resolve_area(snapshot, area_uuid=move_area_uuid, area_title=move_area_title)
    move_project = resolve_project(
        snapshot,
        project_uuid=move_project_uuid,
        project_title=move_project_title,
        area=move_area,
    )
    move_heading = resolve_heading(move_project, heading_uuid=move_heading_uuid, heading_title=move_heading_title)
    move_list = move_project or move_area
    arguments = compact_dict(
        {
            "id": require_item_uuid(todo, kind="todo"),
            "title": title,
            "notes": notes,
            "when": when,
            "deadline": deadline,
            "tags": tags,
            "completed": completed,
            "canceled": canceled,
        }
    )
    add_selector_arguments(arguments, move_list, id_key="list_id", title_key="list")
    add_selector_arguments(arguments, move_heading, id_key="heading_id", title_key="heading")
    require_update_changes(arguments, kind="todo")
    return {
        "tool": "update_todo",
        "arguments": arguments,
        "target": compact_dict(
            {
                "todo": describe_item(todo),
                "area": describe_item(area),
                "project": describe_item(project),
                "heading": describe_item(heading),
                "list": describe_item(move_list),
                "move_area": describe_item(move_area),
                "move_project": describe_item(move_project),
                "move_heading": describe_item(move_heading),
            }
        ),
    }


def prepare_update_project_request(
    snapshot: dict[str, Any],
    *,
    project_uuid: str | None = None,
    project_title: str | None = None,
    title: str | None = None,
    notes: str | None = None,
    when: str | None = None,
    deadline: str | None = None,
    tags: list[str] | None = None,
    completed: bool | None = None,
    canceled: bool | None = None,
    area_uuid: str | None = None,
    area_title: str | None = None,
) -> dict[str, Any]:
    validate_status_flags(completed=completed, canceled=canceled)
    area = resolve_area(snapshot, area_uuid=area_uuid, area_title=area_title)
    project = resolve_project(
        snapshot,
        project_uuid=project_uuid,
        project_title=project_title,
        area=area,
    )
    arguments = compact_dict(
        {
            "id": require_item_uuid(project, kind="project"),
            "title": title,
            "notes": notes,
            "when": when,
            "deadline": deadline,
            "tags": tags,
            "completed": completed,
            "canceled": canceled,
        }
    )
    require_update_changes(arguments, kind="project")
    return {
        "tool": "update_project",
        "arguments": arguments,
        "target": compact_dict(
            {
                "area": describe_item(area),
                "project": describe_item(project),
            }
        ),
    }


def resolve_area(
    snapshot: dict[str, Any], *, area_uuid: str | None = None, area_title: str | None = None
) -> dict[str, Any] | None:
    return resolve_unique_item(
        snapshot.get("normalized", {}).get("areas", []),
        kind="area",
        uuid=area_uuid,
        title=area_title,
    )


def resolve_project(
    snapshot: dict[str, Any],
    *,
    project_uuid: str | None = None,
    project_title: str | None = None,
    area: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    matches = filter_identity_matches(
        snapshot.get("normalized", {}).get("projects", []),
        uuid=project_uuid,
        title=project_title,
    )
    if area is not None:
        matches = [item for item in matches if item_matches_area(item, area)]
    return require_unique_match(matches, kind="project", uuid=project_uuid, title=project_title)


def resolve_todo(
    snapshot: dict[str, Any],
    *,
    todo_uuid: str | None = None,
    todo_title: str | None = None,
    area: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    heading: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    matches = filter_identity_matches(
        snapshot.get("normalized", {}).get("todos", []),
        uuid=todo_uuid,
        title=todo_title,
    )
    if area is not None:
        matches = [item for item in matches if item_matches_relationship(item, area, uuid_key="area_uuid", title_key="area_title")]
    if project is not None:
        matches = [
            item
            for item in matches
            if item_matches_relationship(item, project, uuid_key="project_uuid", title_key="project_title")
        ]
    if heading is not None:
        matches = [
            item
            for item in matches
            if item_matches_relationship(item, heading, uuid_key="heading_uuid", title_key="heading_title")
        ]
    return require_unique_match(matches, kind="todo", uuid=todo_uuid, title=todo_title)


def resolve_heading(
    project: dict[str, Any] | None, *, heading_uuid: str | None = None, heading_title: str | None = None
) -> dict[str, Any] | None:
    if heading_uuid in (None, "") and heading_title in (None, ""):
        return None
    if project is None:
        raise SelectionError("project selector is required when selecting a heading")
    headings = select_child_path(project, "headings")
    matches = filter_identity_matches(headings, uuid=heading_uuid, title=heading_title)
    return require_unique_match(matches, kind="heading", uuid=heading_uuid, title=heading_title)


def resolve_unique_item(
    items: Any, *, kind: str, uuid: str | None = None, title: str | None = None
) -> dict[str, Any] | None:
    matches = filter_identity_matches(items, uuid=uuid, title=title)
    return require_unique_match(matches, kind=kind, uuid=uuid, title=title)


def filter_identity_matches(items: Any, *, uuid: str | None = None, title: str | None = None) -> list[dict[str, Any]]:
    if uuid in (None, "") and title in (None, ""):
        return []
    if not isinstance(items, list):
        return []
    return [item for item in items if item_matches_identity(item, uuid=uuid, title=title)]


def require_unique_match(
    matches: list[dict[str, Any]], *, kind: str, uuid: str | None = None, title: str | None = None
) -> dict[str, Any] | None:
    if uuid in (None, "") and title in (None, ""):
        return None
    if not matches:
        raise SelectionError(f"No {kind} matched selector {describe_selector(uuid=uuid, title=title)}")
    if len(matches) > 1:
        raise SelectionError(f"Multiple {kind}s matched selector {describe_selector(uuid=uuid, title=title)}")
    return matches[0]


def item_matches_identity(item: Any, *, uuid: str | None = None, title: str | None = None) -> bool:
    if not isinstance(item, dict):
        return False
    if uuid not in (None, "") and item.get("uuid") != uuid:
        return False
    if title not in (None, "") and item.get("title") != title:
        return False
    return True


def item_matches_area(item: dict[str, Any], area: dict[str, Any]) -> bool:
    return item_matches_relationship(item, area, uuid_key="area_uuid", title_key="area_title")


def item_matches_relationship(
    item: dict[str, Any], related_item: dict[str, Any], *, uuid_key: str, title_key: str
) -> bool:
    relationships = item.get("relationships")
    if not isinstance(relationships, dict):
        relationships = {}
    scopes = {value for value in (related_item.get("uuid"), related_item.get("title")) if value not in (None, "")}
    candidates = {value for value in (relationships.get(uuid_key), relationships.get(title_key)) if value not in (None, "")}
    return bool(scopes & candidates)


def add_selector_arguments(arguments: dict[str, Any], item: dict[str, Any] | None, *, id_key: str, title_key: str) -> None:
    if not isinstance(item, dict):
        return
    if item.get("uuid") not in (None, ""):
        arguments[id_key] = item["uuid"]
    elif item.get("title") not in (None, ""):
        arguments[title_key] = item["title"]


def describe_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return compact_dict({"kind": item.get("kind"), "uuid": item.get("uuid"), "title": item.get("title")})


def describe_selector(*, uuid: str | None = None, title: str | None = None) -> str:
    values = compact_dict({"uuid": uuid, "title": title})
    return str(values)


def compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def require_item_uuid(item: dict[str, Any] | None, *, kind: str) -> str:
    if not isinstance(item, dict):
        raise SelectionError(f"{kind} selector is required")
    uuid = item.get("uuid")
    if uuid in (None, ""):
        raise SelectionError(f"Matched {kind} has no uuid and cannot be updated")
    return str(uuid)


def require_update_changes(arguments: dict[str, Any], *, kind: str) -> None:
    if set(arguments) <= {"id"}:
        raise ValueError(f"At least one {kind} field must be provided to update")


def validate_status_flags(*, completed: bool | None = None, canceled: bool | None = None) -> None:
    if completed is True and canceled is True:
        raise ValueError("completed and canceled cannot both be true")