from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .control import SelectionError, prepare_create_todo_request, prepare_update_todo_request
from .llm_bridge import build_command_handoff, complete, parse_json_object, resolve_llm_config, resolve_model_name
from .mcp import StdioMcpClient
from .snapshot import compact, fetch_snapshot, normalize_collection, now_utc, single_line, timestamp_slug

INTAKE_SESSION_SCHEMA_VERSION = "things-ai.intake-session.v1"
INTAKE_PACKET_SCHEMA_VERSION = "things-ai.intake-packet.v1"
INTAKE_LLM_BUNDLE_SCHEMA_VERSION = "things-ai.intake-llm-bundle.v1"
INTAKE_LLM_REQUEST_SCHEMA_VERSION = "things-ai.intake-request.v1"
INTAKE_LLM_RESPONSE_SCHEMA_VERSION = "things-ai.intake-response.v1"
INTAKE_PROPOSAL_SCHEMA_VERSION = "things-ai.intake-proposal.v1"
INTAKE_REVIEWABLE_STATUSES = ("reviewing", "new")
INTAKE_PROPOSABLE_STATUSES = ("reviewed", "proposed")
INTAKE_CONFIDENCE_LEVELS = {"low", "medium", "high"}
INTAKE_RETIRE_RECOMMENDATIONS = {"keep_source", "complete_source", "cancel_source"}
INTAKE_INTERPRETATION_KINDS = {"single_action", "project_with_next_action", "complete_already", "retire", "unclear"}
INTAKE_HOME_KINDS = {
    "not_applicable",
    "current_location",
    "existing_project",
    "single_action_project",
    "new_project",
    "manual_review",
}
INTAKE_INTERPRETATION_ALIASES = {
    "project": "project_with_next_action",
    "project_with_next_action": "project_with_next_action",
    "project next action": "project_with_next_action",
    "project-shaped": "project_with_next_action",
    "single": "single_action",
    "single_action": "single_action",
    "single action": "single_action",
    "action": "single_action",
    "complete": "complete_already",
    "complete_already": "complete_already",
    "done": "complete_already",
    "retire": "retire",
    "cancel": "retire",
    "trash": "retire",
    "unclear": "unclear",
    "needs_review": "unclear",
}
INTAKE_HOME_ALIASES = {
    "": "",
    "n/a": "not_applicable",
    "not_applicable": "not_applicable",
    "none": "not_applicable",
    "current": "current_location",
    "current_location": "current_location",
    "keep_current_location": "current_location",
    "existing": "existing_project",
    "existing_project": "existing_project",
    "single_action_project": "single_action_project",
    "single action project": "single_action_project",
    "new": "new_project",
    "new_project": "new_project",
    "manual": "manual_review",
    "manual_review": "manual_review",
}
CLASSIFICATION_ALIASES = {
    "": "single_action",
    "1": "project",
    "2": "single_action",
    "3": "complete_already",
    "4": "trash",
    "5": "unclear",
    "project": "project",
    "project/outcome": "project",
    "outcome": "project",
    "single": "single_action",
    "single action": "single_action",
    "single_action": "single_action",
    "action": "single_action",
    "next action": "single_action",
    "complete": "complete_already",
    "complete already": "complete_already",
    "done": "complete_already",
    "trash": "trash",
    "retire": "trash",
    "trash / retire": "trash",
    "unclear": "unclear",
    "maybe": "unclear",
}


def start_intake_session(
    *,
    output_dir: Path,
    today_items: list[dict[str, Any]] | None = None,
    available_tools: list[str] | None = None,
    command_text: str | None = None,
) -> dict[str, Any]:
    if today_items is None:
        today_items, available_tools = fetch_today_incomplete_items(command_text=command_text)
    else:
        today_items = [item for item in today_items if is_incomplete_todo(item)]
        available_tools = available_tools or []
    session, packets = build_intake_session(
        today_items=today_items,
        available_tools=available_tools,
        command_text=command_text,
    )
    session_dir = write_session_state(output_dir=output_dir, session=session, packets=packets)
    return {
        "session": session,
        "message": "No incomplete Today items found." if not packets else None,
        "artifacts": {
            "session_dir": str(session_dir),
            "session_json": str(session_dir / "session.json"),
            "session_markdown": str(session_dir / "session.md"),
            "packet_directory": str(session_dir / "packets"),
            "llm_directory": str(session_dir / "llm-ready"),
        },
    }


def review_next_packet(
    *,
    output_dir: Path,
    session_ref: str | None = None,
    snapshot: dict[str, Any] | None = None,
    command_text: str | None = None,
    input_func: Callable[[str], str] = input,
    output_func: Callable[..., None] = print,
) -> dict[str, Any]:
    session_path = resolve_session_path(output_dir=output_dir, session_ref=session_ref)
    session = load_json(session_path)
    session_dir = session_path.parent
    packets = load_session_packets(session_dir, session)
    packet = choose_next_packet(packets)
    if packet is None:
        refresh_session_counts(session, packets)
        write_session_state(output_dir=output_dir, session=session, packets=packets, session_dir=session_dir)
        return {
            "status": "done",
            "session": session,
            "message": "No unresolved intake packets remain.",
            "artifacts": {
                "session_json": str(session_path),
                "session_markdown": str(session_dir / "session.md"),
            },
        }

    packet["status"] = "reviewing"
    packet["updated_at"] = now_utc()
    review = ensure_dict(packet.get("review"))
    review["started_at"] = review.get("started_at") or now_utc()
    packet["review"] = review
    write_session_state(output_dir=output_dir, session=session, packets=packets, session_dir=session_dir)

    display_packet_summary(packet, output_func=output_func)
    collect_review_answers(packet, input_func=input_func)
    write_session_state(output_dir=output_dir, session=session, packets=packets, session_dir=session_dir)

    snapshot = snapshot or fetch_snapshot(command_text=command_text)
    llm_bundle = build_llm_ready_bundle(packet=packet, session=session)
    llm_paths = write_llm_bundle(session_dir=session_dir, packet=packet, bundle=llm_bundle)
    packet["proposal"] = {
        "status": "pending_llm",
        "llm_bundle_ref": relative_to_session(session_dir, llm_paths["json"]),
        "llm_bundle_markdown_ref": relative_to_session(session_dir, llm_paths["markdown"]),
    }
    packet["staged_actions"] = build_staged_actions(packet=packet, snapshot=snapshot)
    packet["status"] = "reviewed"
    packet["updated_at"] = now_utc()
    write_session_state(output_dir=output_dir, session=session, packets=packets, session_dir=session_dir)

    return {
        "status": "reviewed",
        "session": session,
        "packet": packet,
        "artifacts": {
            "session_json": str(session_path),
            "session_markdown": str(session_dir / "session.md"),
            "packet_json": str(packet_json_path(session_dir, str(packet.get("packet_id") or ""))),
            "packet_markdown": str(packet_markdown_path(session_dir, str(packet.get("packet_id") or ""))),
            "llm_json": str(llm_paths["json"]),
            "llm_markdown": str(llm_paths["markdown"]),
        },
    }


def propose_intake_packet(
    *,
    output_dir: Path,
    packet_ref: str,
    session_ref: str | None = None,
    snapshot: dict[str, Any] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    env_path: Path | None = None,
    execute: bool = False,
    decision_text: str | None = None,
    command_text: str | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    session_path = resolve_session_path(output_dir=output_dir, session_ref=session_ref)
    session = load_json(session_path)
    session_dir = session_path.parent
    packets = load_session_packets(session_dir, session)
    packet = require_packet(packets, packet_ref)
    if packet.get("status") not in INTAKE_PROPOSABLE_STATUSES:
        raise ValueError(f"Packet {packet_ref} is not ready for proposal generation")

    config = resolve_llm_config(env_path=env_path)
    llm_bundle = load_llm_bundle(session_dir=session_dir, packet=packet)
    request_bundle = build_intake_request_bundle(
        llm_bundle,
        session=session,
        packet=packet,
        model=model,
        max_tokens=max_tokens,
        config=config,
    )
    request_paths = write_intake_request_bundle(session_dir=session_dir, packet=packet, request_bundle=request_bundle)
    prompt = str(ensure_dict(request_bundle.get("request")).get("prompt") or "")
    system = str(ensure_dict(request_bundle.get("request")).get("system") or "")
    llm_result = complete(
        prompt,
        model=model,
        system=system,
        max_tokens=max_tokens,
        env_path=env_path,
        execute=execute,
        transport=transport,
    )

    proposal = ensure_dict(packet.get("proposal"))
    proposal["status"] = "request_prepared"
    proposal["llm_bundle_ref"] = proposal.get("llm_bundle_ref") or relative_to_session(
        session_dir, llm_ready_json_path(session_dir, str(packet.get("packet_id") or ""))
    )
    proposal["llm_request_ref"] = relative_to_session(session_dir, request_paths["json"])
    proposal["llm_request_markdown_ref"] = relative_to_session(session_dir, request_paths["markdown"])
    packet["proposal"] = proposal
    packet["updated_at"] = now_utc()

    result: dict[str, Any] = {
        "status": "request_prepared",
        "config": config,
        "session": session,
        "packet": packet,
        "request_bundle": request_bundle,
        "llm": llm_result,
        "artifacts": {
            "session_json": str(session_path),
            "session_markdown": str(session_dir / "session.md"),
            "packet_json": str(packet_json_path(session_dir, str(packet.get("packet_id") or ""))),
            "packet_markdown": str(packet_markdown_path(session_dir, str(packet.get("packet_id") or ""))),
            "request_json": str(request_paths["json"]),
            "request_markdown": str(request_paths["markdown"]),
        },
    }

    resolved_decision_text = clean_text(decision_text) or clean_text(llm_result.get("response_text"))
    if not resolved_decision_text:
        write_session_state(output_dir=output_dir, session=session, packets=packets, session_dir=session_dir)
        return result

    try:
        decision = parse_intake_decision(resolved_decision_text)
    except ValueError as exc:
        response_bundle = build_intake_response_bundle(
            request_bundle=request_bundle,
            decision=None,
            llm_result=llm_result,
            response_text=resolved_decision_text,
            source="provider" if execute else "provided",
            parse_error=str(exc),
        )
        response_paths = write_intake_response_bundle(session_dir=session_dir, packet=packet, response_bundle=response_bundle)
        packet["proposal"] = compact(
            {
                "status": "response_invalid",
                "parse_error": str(exc),
                "llm_bundle_ref": proposal.get("llm_bundle_ref"),
                "llm_request_ref": relative_to_session(session_dir, request_paths["json"]),
                "llm_request_markdown_ref": relative_to_session(session_dir, request_paths["markdown"]),
                "llm_response_ref": relative_to_session(session_dir, response_paths["json"]),
                "llm_response_markdown_ref": relative_to_session(session_dir, response_paths["markdown"]),
            }
        )
        packet["updated_at"] = now_utc()
        write_session_state(output_dir=output_dir, session=session, packets=packets, session_dir=session_dir)
        result.update({"status": "response_invalid", "error": str(exc)})
        result["artifacts"].update(
            {
                "response_json": str(response_paths["json"]),
                "response_markdown": str(response_paths["markdown"]),
            }
        )
        return result

    response_bundle = build_intake_response_bundle(
        request_bundle=request_bundle,
        decision=decision,
        llm_result=llm_result,
        response_text=resolved_decision_text,
        source="provider" if execute else "provided",
    )
    response_paths = write_intake_response_bundle(session_dir=session_dir, packet=packet, response_bundle=response_bundle)
    snapshot = snapshot or fetch_snapshot(command_text=command_text)
    proposal_bundle = build_intake_proposal_bundle(
        request_bundle=request_bundle,
        decision=decision,
        packet=packet,
        snapshot=snapshot,
    )
    proposal_paths = write_intake_proposal_bundle(session_dir=session_dir, packet=packet, proposal_bundle=proposal_bundle)
    packet["proposal"] = compact(
        {
            "status": "generated",
            "summary": decision.get("summary"),
            "reasoning_summary": decision.get("reasoning_summary"),
            "interpretation_kind": decision.get("interpretation_kind"),
            "confidence": decision.get("confidence"),
            "proposed_project": decision.get("proposed_project"),
            "proposed_outcome": decision.get("proposed_outcome"),
            "proposed_next_action": decision.get("proposed_next_action"),
            "proposed_supporting_tasks": decision.get("proposed_supporting_tasks"),
            "proposed_contexts": decision.get("proposed_contexts"),
            "proposed_due": decision.get("proposed_due"),
            "recommended_home_kind": decision.get("recommended_home_kind"),
            "recommended_home_title": decision.get("recommended_home_title"),
            "recommended_home_note": decision.get("recommended_home_note"),
            "retire_recommendation": decision.get("retire_recommendation"),
            "manual_review_flags": decision.get("manual_review_flags"),
            "llm_bundle_ref": proposal.get("llm_bundle_ref"),
            "llm_request_ref": relative_to_session(session_dir, request_paths["json"]),
            "llm_request_markdown_ref": relative_to_session(session_dir, request_paths["markdown"]),
            "llm_response_ref": relative_to_session(session_dir, response_paths["json"]),
            "llm_response_markdown_ref": relative_to_session(session_dir, response_paths["markdown"]),
            "proposal_ref": relative_to_session(session_dir, proposal_paths["json"]),
            "proposal_markdown_ref": relative_to_session(session_dir, proposal_paths["markdown"]),
        }
    )
    packet["staged_actions"] = ensure_dict(proposal_bundle.get("staged_actions"))
    packet["status"] = "proposed"
    packet["updated_at"] = now_utc()
    write_session_state(output_dir=output_dir, session=session, packets=packets, session_dir=session_dir)

    result.update(
        {
            "status": "proposed",
            "decision": decision,
            "proposal_bundle": proposal_bundle,
        }
    )
    result["artifacts"].update(
        {
            "response_json": str(response_paths["json"]),
            "response_markdown": str(response_paths["markdown"]),
            "proposal_json": str(proposal_paths["json"]),
            "proposal_markdown": str(proposal_paths["markdown"]),
        }
    )
    return result


def fetch_today_incomplete_items(command_text: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    with StdioMcpClient.from_environment(command_text=command_text) as client:
        tools = client.list_tools()
        payload = client.call_tool("get_today", {})["payload"]
    today_items = normalize_collection("todo", payload)
    candidates = [item for item in today_items if is_incomplete_todo(item)]
    available_tools = [tool.get("name") for tool in tools if isinstance(tool, dict) and tool.get("name")]
    return candidates, available_tools


def build_intake_session(
    *,
    today_items: list[dict[str, Any]],
    available_tools: list[str] | None = None,
    command_text: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session_id = f"intake-{timestamp_slug()}"
    created_at = now_utc()
    packets = [
        build_intake_packet(session_id=session_id, packet_id=f"packet-{index:03d}", source_item=item)
        for index, item in enumerate(today_items, start=1)
    ]
    session = {
        "schema_version": INTAKE_SESSION_SCHEMA_VERSION,
        "session_id": session_id,
        "generated_at": created_at,
        "updated_at": created_at,
        "status": "active",
        "source": {
            "integration": "things-mcp",
            "transport": "stdio",
            "command": command_text or "uvx things-mcp",
        },
        "available_tools": available_tools or [],
        "selection_mode": "today-incomplete-singletons",
        "packet_directory": "packets",
        "llm_directory": "llm-ready",
        "packet_order": [packet["packet_id"] for packet in packets],
    }
    refresh_session_counts(session, packets)
    return session, packets


def build_intake_packet(*, session_id: str, packet_id: str, source_item: dict[str, Any]) -> dict[str, Any]:
    created_at = now_utc()
    return compact(
        {
            "schema_version": INTAKE_PACKET_SCHEMA_VERSION,
            "session_id": session_id,
            "packet_id": packet_id,
            "status": "new",
            "review_unit_type": "single_action",
            "created_at": created_at,
            "updated_at": created_at,
            "source_items": [source_item],
            "review": {
                "raw_answers": {},
                "normalized": {},
                "transcript": [],
            },
            "proposal": {"status": "not_started"},
            "staged_actions": {
                "create_items": [],
                "update_items": [],
                "retire_legacy_items": [],
                "manual_steps": [],
            },
        }
    )


def collect_review_answers(packet: dict[str, Any], *, input_func: Callable[[str], str]) -> None:
    review = ensure_dict(packet.get("review"))
    raw_answers = ensure_dict(review.get("raw_answers"))
    normalized = ensure_dict(review.get("normalized"))
    transcript = review.get("transcript")
    if not isinstance(transcript, list):
        transcript = []

    raw_classification = input_func("Kind [1 project, 2 single next action, 3 complete, 4 trash, 5 unclear] (default 2): ")
    classification = normalize_classification(raw_classification)
    raw_answers["classification"] = raw_classification
    normalized["classification"] = classification
    transcript.append(transcript_entry(prompt="classification", answer=raw_classification, normalized=classification))
    packet["review_unit_type"] = classification
    packet["updated_at"] = now_utc()

    if classification == "project":
        outcome = input_func("What does done mean for this project/outcome? ")
        raw_answers["outcome"] = outcome
        normalized["outcome"] = clean_text(outcome)
        transcript.append(transcript_entry(prompt="outcome", answer=outcome, normalized=clean_text(outcome)))

    if classification not in {"complete_already", "trash"}:
        next_action = input_func("What is the next physical action? ")
        raw_answers["next_action"] = next_action
        normalized["next_action"] = clean_text(next_action)
        transcript.append(
            transcript_entry(prompt="next_action", answer=next_action, normalized=clean_text(next_action))
        )

    notes = input_func("Any supporting notes or context? (optional) ")
    raw_answers["notes"] = notes
    normalized["notes"] = clean_text(notes)
    transcript.append(transcript_entry(prompt="notes", answer=notes, normalized=clean_text(notes)))

    review["raw_answers"] = raw_answers
    review["normalized"] = normalized
    review["transcript"] = transcript
    review["completed_at"] = now_utc()
    packet["review"] = review


def build_llm_ready_bundle(*, packet: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    review = normalized_review(packet)
    source_items = packet.get("source_items") if isinstance(packet.get("source_items"), list) else []
    return compact(
        {
            "schema_version": INTAKE_LLM_BUNDLE_SCHEMA_VERSION,
            "generated_at": now_utc(),
            "session_id": session.get("session_id"),
            "packet_id": packet.get("packet_id"),
            "packet_status": packet.get("status"),
            "review_unit_type": packet.get("review_unit_type"),
            "source_items": source_items,
            "review": review,
            "guidance": {
                "goal": "Convert source scraps into clearer projects or next actions without mutating Things yet.",
                "expected_outputs": [
                    "interpretation_kind",
                    "reasoning_summary",
                    "proposed_project",
                    "proposed_outcome",
                    "proposed_next_action",
                    "proposed_supporting_tasks",
                    "recommended_home_kind",
                    "recommended_home_title",
                    "recommended_home_note",
                    "retire_recommendation",
                    "manual_review_flags",
                ],
            },
        }
    )


def build_intake_request_bundle(
    llm_bundle: dict[str, Any],
    *,
    session: dict[str, Any],
    packet: dict[str, Any],
    model: str | None = None,
    max_tokens: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or resolve_llm_config()
    requested_model = model or config["default_model"]
    resolved_model = resolve_model_name(requested_model, config=config)
    token_limit = max_tokens if max_tokens is not None else int(config["max_tokens"])
    system = "You are helping reconcile reviewed Things intake packets into preview-only proposal artifacts."
    instruction = (
        "Review the intake bundle and return a conservative JSON proposal. "
        "Do not assume any Things mutation has happened. Decide explicitly whether the packet is best handled as "
        "single_action, project_with_next_action, complete_already, retire, or unclear. Prefer single_action when "
        "one physical action would truly finish the commitment. Use project_with_next_action only when the work "
        "implies multiple actions or a meaningful outcome boundary. If a single action still needs a better home, "
        "recommend that home separately instead of inflating it into a full project. Keep project creation manual "
        "and still suggest the next action whenever the item should remain active."
    )
    response_contract = build_intake_decision_response_contract()
    prompt = build_intake_decision_prompt(llm_bundle, instruction=instruction, response_contract=response_contract)
    return compact(
        {
            "schema_version": INTAKE_LLM_REQUEST_SCHEMA_VERSION,
            "request_kind": "intake-decision",
            "generated_at": now_utc(),
            "session": {
                "session_id": session.get("session_id"),
                "status": session.get("status"),
            },
            "packet": {
                "packet_id": packet.get("packet_id"),
                "status": packet.get("status"),
                "review_unit_type": packet.get("review_unit_type"),
            },
            "consumer_modes": {
                "external_llm": True,
                "augment": True,
            },
            "augment_usage": {
                "summary": "Augment can read this request bundle directly and help author or review the intake proposal.",
                "notes": [
                    "All recommended actions remain preview-only.",
                    "Keep any project creation recommendation manual unless an explicit safe command exists.",
                ],
            },
            "request": {
                "instruction": instruction,
                "system": system,
                "prompt": prompt,
                "requested_model": requested_model,
                "resolved_model": resolved_model,
                "max_tokens": token_limit,
                "response_contract": response_contract,
            },
            "payload": llm_bundle,
        }
    )


def build_intake_decision_response_contract() -> dict[str, Any]:
    return {
        "schema_version": INTAKE_LLM_RESPONSE_SCHEMA_VERSION,
        "kind": "intake-decision",
        "instructions": [
            "Return a JSON object only.",
            "Treat all recommendations as preview-only guidance.",
            "Prefer one clear next action over speculative restructuring.",
            "Make the GTD structure explicit with interpretation_kind.",
            "Keep project creation recommendations manual; do not imply that a project has already been created.",
        ],
        "required": [
            "summary",
            "reasoning_summary",
            "interpretation_kind",
            "confidence",
            "proposed_project",
            "proposed_outcome",
            "proposed_next_action",
            "proposed_supporting_tasks",
            "proposed_contexts",
            "proposed_due",
            "recommended_home_kind",
            "recommended_home_title",
            "recommended_home_note",
            "retire_recommendation",
            "manual_review_flags",
        ],
        "properties": {
            "summary": "Short overall proposal summary.",
            "reasoning_summary": "Brief explanation grounded in the intake review.",
            "interpretation_kind": [
                "single_action",
                "project_with_next_action",
                "complete_already",
                "retire",
                "unclear",
            ],
            "confidence": ["low", "medium", "high"],
            "proposed_project": "Project title only when interpretation_kind is project_with_next_action; otherwise empty string.",
            "proposed_outcome": "Outcome/done-state when interpretation_kind is project_with_next_action; otherwise optional empty string.",
            "proposed_next_action": "Concrete next physical action to create or keep whenever the item should remain active.",
            "proposed_supporting_tasks": ["Optional supporting tasks or checklist items."],
            "proposed_contexts": ["Optional contexts, tools, or tags worth preserving in notes."],
            "proposed_due": "Optional due/deadline in YYYY-MM-DD format or empty string.",
            "recommended_home_kind": [
                "not_applicable",
                "current_location",
                "existing_project",
                "single_action_project",
                "new_project",
                "manual_review",
            ],
            "recommended_home_title": "Suggested project/home title if relevant; otherwise empty string.",
            "recommended_home_note": "Short explanation of where the item should live, or why no home recommendation applies.",
            "retire_recommendation": ["keep_source", "complete_source", "cancel_source"],
            "manual_review_flags": ["Any ambiguity or caution flags requiring human review."],
        },
    }


def build_intake_decision_prompt(
    llm_bundle: dict[str, Any], *, instruction: str, response_contract: dict[str, Any]
) -> str:
    return "\n\n".join(
        [
            instruction.strip(),
            "Return JSON only that matches this response contract:",
            json.dumps(response_contract, indent=2, sort_keys=True),
            "Reviewed intake bundle JSON:",
            json.dumps(llm_bundle, indent=2, sort_keys=True),
        ]
    ) + "\n"


def parse_intake_decision(text: str) -> dict[str, Any]:
    decision = parse_json_object(text, label="intake decision")
    decision = normalize_intake_decision(decision)
    validate_intake_decision(decision)
    return decision


def normalize_intake_decision(decision: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(decision)
    interpretation_kind = normalize_interpretation_kind(normalized)
    normalized["interpretation_kind"] = interpretation_kind
    recommended_home_kind = normalize_home_kind(normalized, interpretation_kind=interpretation_kind)
    normalized["recommended_home_kind"] = recommended_home_kind
    recommended_home_title = clean_text(normalized.get("recommended_home_title"))
    if not recommended_home_title and interpretation_kind == "project_with_next_action":
        recommended_home_title = clean_text(normalized.get("proposed_project"))
    normalized["recommended_home_title"] = recommended_home_title
    normalized["recommended_home_note"] = clean_text(normalized.get("recommended_home_note")) or default_home_note(
        interpretation_kind=interpretation_kind,
        recommended_home_kind=recommended_home_kind,
        recommended_home_title=recommended_home_title,
    )
    return normalized


def validate_intake_decision(decision: dict[str, Any]) -> None:
    required = [
        "summary",
        "reasoning_summary",
        "interpretation_kind",
        "confidence",
        "proposed_project",
        "proposed_outcome",
        "proposed_next_action",
        "proposed_supporting_tasks",
        "proposed_contexts",
        "proposed_due",
        "recommended_home_kind",
        "recommended_home_title",
        "recommended_home_note",
        "retire_recommendation",
        "manual_review_flags",
    ]
    for field_name in required:
        if field_name not in decision:
            raise ValueError(f"intake decision is missing required field: {field_name}")
    interpretation_kind = clean_text(decision.get("interpretation_kind")).lower()
    if interpretation_kind not in INTAKE_INTERPRETATION_KINDS:
        raise ValueError(f"Unsupported intake decision interpretation_kind: {decision.get('interpretation_kind')}")
    confidence = clean_text(decision.get("confidence")).lower()
    if confidence not in INTAKE_CONFIDENCE_LEVELS:
        raise ValueError(f"Unsupported intake decision confidence: {decision.get('confidence')}")
    recommended_home_kind = clean_text(decision.get("recommended_home_kind")).lower()
    if recommended_home_kind not in INTAKE_HOME_KINDS:
        raise ValueError(f"Unsupported intake decision recommended_home_kind: {decision.get('recommended_home_kind')}")
    retire_recommendation = clean_text(decision.get("retire_recommendation")).lower()
    if retire_recommendation not in INTAKE_RETIRE_RECOMMENDATIONS:
        raise ValueError(
            f"Unsupported intake decision retire_recommendation: {decision.get('retire_recommendation')}"
        )
    for field_name in ["proposed_supporting_tasks", "proposed_contexts", "manual_review_flags"]:
        if not isinstance(decision.get(field_name), list):
            raise ValueError(f"intake decision {field_name} must be a list")


def build_intake_response_bundle(
    *,
    request_bundle: dict[str, Any],
    decision: dict[str, Any] | None,
    llm_result: dict[str, Any],
    response_text: str,
    source: str,
    parse_error: str | None = None,
) -> dict[str, Any]:
    request = ensure_dict(request_bundle.get("request"))
    return compact(
        {
            "schema_version": INTAKE_LLM_RESPONSE_SCHEMA_VERSION,
            "response_kind": "intake-decision",
            "generated_at": now_utc(),
            "source": source,
            "request": {
                "requested_model": request.get("requested_model"),
                "resolved_model": request.get("resolved_model"),
                "max_tokens": request.get("max_tokens"),
            },
            "llm": {
                "dry_run": llm_result.get("dry_run"),
                "provider": llm_result.get("provider"),
                "request_preview": llm_result.get("request_preview"),
            },
            "response_text": response_text,
            "parse_error": parse_error,
            "decision": decision,
        }
    )


def build_intake_proposal_bundle(
    *,
    request_bundle: dict[str, Any],
    decision: dict[str, Any],
    packet: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    staged_actions = build_proposed_staged_actions(packet=packet, decision=decision, snapshot=snapshot)
    return compact(
        {
            "schema_version": INTAKE_PROPOSAL_SCHEMA_VERSION,
            "proposal_kind": "intake-action-proposals",
            "generated_at": now_utc(),
            "request_bundle": compact(
                {
                    "schema_version": request_bundle.get("schema_version"),
                    "request_kind": request_bundle.get("request_kind"),
                    "generated_at": request_bundle.get("generated_at"),
                    "packet": request_bundle.get("packet"),
                    "session": request_bundle.get("session"),
                }
            ),
            "decision": decision,
            "staged_actions": staged_actions,
            "counts": {
                "create_items": len(ensure_list_of_dicts(ensure_dict(staged_actions).get("create_items"))),
                "update_items": len(ensure_list_of_dicts(ensure_dict(staged_actions).get("update_items"))),
                "retire_legacy_items": len(ensure_list_of_dicts(ensure_dict(staged_actions).get("retire_legacy_items"))),
                "manual_steps": len(clean_string_list(staged_actions.get("manual_steps"))),
            },
        }
    )


def build_proposed_staged_actions(
    *, packet: dict[str, Any], decision: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    source_item = first_source_item(packet)
    review = normalized_review(packet)
    interpretation_kind = normalize_interpretation_kind(decision)
    proposal_review = {
        **review,
        "outcome": clean_text(decision.get("proposed_outcome")) or review.get("outcome") or "",
        "next_action": clean_text(decision.get("proposed_next_action")) or review.get("next_action") or "",
        "notes": review.get("notes") or "",
        "supporting_tasks": clean_string_list(decision.get("proposed_supporting_tasks")),
        "contexts": clean_string_list(decision.get("proposed_contexts")),
        "due": clean_text(decision.get("proposed_due")),
    }
    result = {
        "create_items": [],
        "update_items": [],
        "retire_legacy_items": [],
        "manual_steps": [],
    }
    if interpretation_kind in {"single_action", "project_with_next_action"}:
        if proposal_review["next_action"]:
            preview = build_create_preview(source_item=source_item, review=proposal_review, snapshot=snapshot)
            if preview is not None:
                result["create_items"].append(preview)
            else:
                result["manual_steps"].append("Could not build a create-task preview automatically from the proposal.")
        else:
            result["manual_steps"].append(
                "Proposal did not produce a concrete next action; do not stage automatic create actions."
            )
    elif interpretation_kind == "unclear":
        result["manual_steps"].append("Packet remains unclear after the proposal; do not stage automatic reconcile actions.")
    elif interpretation_kind == "complete_already":
        result["manual_steps"].append("Proposal says the commitment may already be complete; do not stage a replacement next action.")
    elif interpretation_kind == "retire":
        result["manual_steps"].append("Proposal says the source item should likely be retired; do not stage a replacement next action.")

    retire_recommendation = clean_text(decision.get("retire_recommendation")).lower()
    if retire_recommendation == "complete_source":
        preview = build_retire_preview(source_item=source_item, snapshot=snapshot, completed=True)
        if preview is not None:
            result["retire_legacy_items"].append(preview)
    elif retire_recommendation == "cancel_source":
        preview = build_retire_preview(source_item=source_item, snapshot=snapshot, canceled=True)
        if preview is not None:
            result["retire_legacy_items"].append(preview)
    else:
        result["manual_steps"].append("Keep the original source item until the proposed replacement has been reviewed.")

    result["manual_steps"].extend(build_home_manual_steps(decision))

    for flag in clean_string_list(decision.get("manual_review_flags")):
        result["manual_steps"].append(flag)
    if clean_text(decision.get("confidence")).lower() == "low":
        result["manual_steps"].append("LLM confidence is low; require human review before acting on previews.")
    return compact(result)


def build_staged_actions(*, packet: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    source_item = first_source_item(packet)
    review = normalized_review(packet)
    classification = str(review.get("classification") or "single_action")
    result = {
        "create_items": [],
        "update_items": [],
        "retire_legacy_items": [],
        "manual_steps": [],
    }
    if classification in {"single_action", "project"}:
        preview = build_create_preview(source_item=source_item, review=review, snapshot=snapshot)
        if preview is not None:
            result["create_items"].append(preview)
        elif classification == "single_action":
            result["manual_steps"].append("Could not build a create-task preview automatically.")
        if classification == "project":
            result["manual_steps"].append("Project-shaped packet captured; project creation stays manual in this first slice.")
        result["manual_steps"].append("Review whether the original source item should be retired after accepting the clarified structure.")
    elif classification == "complete_already":
        preview = build_retire_preview(source_item=source_item, snapshot=snapshot, completed=True)
        if preview is not None:
            result["retire_legacy_items"].append(preview)
    elif classification == "trash":
        preview = build_retire_preview(source_item=source_item, snapshot=snapshot, canceled=True)
        if preview is not None:
            result["retire_legacy_items"].append(preview)
    else:
        result["manual_steps"].append("Packet is still unclear; no automatic reconcile preview was staged.")
    return compact(result)


def build_create_preview(
    *, source_item: dict[str, Any], review: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any] | None:
    relationships = ensure_dict(source_item.get("relationships"))
    title = str(review.get("next_action") or source_item.get("title") or "").strip()
    if not title:
        return None
    try:
        request = prepare_create_todo_request(
            snapshot,
            title=title,
            notes=build_preview_notes(source_item=source_item, review=review),
            deadline=string_or_none(review.get("due")),
            checklist_items=clean_string_list(review.get("supporting_tasks")) or None,
            area_uuid=string_or_none(relationships.get("area_uuid")),
            area_title=string_or_none(relationships.get("area_title")),
            project_uuid=string_or_none(relationships.get("project_uuid")),
            project_title=string_or_none(relationships.get("project_title")),
            heading_uuid=string_or_none(relationships.get("heading_uuid")),
            heading_title=string_or_none(relationships.get("heading_title")),
        )
    except (SelectionError, ValueError):
        return None
    return {
        "prepared_request": request,
        "command_handoff": build_command_handoff(request),
    }


def build_retire_preview(
    *,
    source_item: dict[str, Any],
    snapshot: dict[str, Any],
    completed: bool = False,
    canceled: bool = False,
) -> dict[str, Any] | None:
    todo_uuid = string_or_none(source_item.get("uuid"))
    if not todo_uuid:
        return None
    try:
        request = prepare_update_todo_request(snapshot, todo_uuid=todo_uuid, completed=completed, canceled=canceled)
    except (SelectionError, ValueError):
        return None
    return {
        "prepared_request": request,
        "command_handoff": build_command_handoff(request),
    }


def build_preview_notes(*, source_item: dict[str, Any], review: dict[str, Any]) -> str | None:
    sections: list[str] = []
    outcome = string_or_none(review.get("outcome"))
    notes = string_or_none(review.get("notes"))
    contexts = clean_string_list(review.get("contexts"))
    supporting_tasks = clean_string_list(review.get("supporting_tasks"))
    due = string_or_none(review.get("due"))
    source_notes = string_or_none(source_item.get("notes"))
    if outcome:
        sections.append(f"Outcome: {outcome}")
    if notes:
        sections.append(notes)
    if contexts:
        sections.append("Contexts: " + ", ".join(contexts))
    if due:
        sections.append(f"Suggested due: {due}")
    if supporting_tasks:
        sections.append("Supporting tasks:\n- " + "\n- ".join(supporting_tasks))
    if source_notes:
        sections.append(f"Source notes:\n{source_notes}")
    if not sections:
        return None
    return "\n\n".join(sections)


def display_packet_summary(packet: dict[str, Any], *, output_func: Callable[..., None]) -> None:
    source_item = first_source_item(packet)
    relationships = ensure_dict(source_item.get("relationships"))
    output_func("")
    output_func(f"Packet {packet.get('packet_id')}: {single_line(source_item.get('title') or '(untitled)')}")
    if relationships.get("project_title") or relationships.get("area_title"):
        output_func(
            f"Current location: {relationships.get('area_title') or '-'} / {relationships.get('project_title') or '-'}"
        )
    if source_item.get("notes"):
        output_func(f"Notes: {single_line(str(source_item.get('notes')))}")
    output_func("")


def write_session_state(
    *,
    output_dir: Path,
    session: dict[str, Any],
    packets: list[dict[str, Any]],
    session_dir: Path | None = None,
) -> Path:
    resolved_dir = session_dir or default_session_dir(output_dir=output_dir, session_id=str(session.get("session_id") or ""))
    (resolved_dir / "packets").mkdir(parents=True, exist_ok=True)
    (resolved_dir / "llm-ready").mkdir(parents=True, exist_ok=True)
    (resolved_dir / "llm-requests").mkdir(parents=True, exist_ok=True)
    (resolved_dir / "llm-responses").mkdir(parents=True, exist_ok=True)
    (resolved_dir / "proposals").mkdir(parents=True, exist_ok=True)
    refresh_session_counts(session, packets)
    session["updated_at"] = now_utc()
    (resolved_dir / "session.json").write_text(json.dumps(session, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (resolved_dir / "session.md").write_text(render_session_markdown(session, packets), encoding="utf-8")
    for packet in packets:
        packet_id = str(packet.get("packet_id") or "")
        packet_json_path(resolved_dir, packet_id).write_text(
            json.dumps(packet, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        packet_markdown_path(resolved_dir, packet_id).write_text(render_packet_markdown(packet), encoding="utf-8")
    return resolved_dir


def write_llm_bundle(*, session_dir: Path, packet: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Path]:
    packet_id = str(packet.get("packet_id") or "packet")
    json_path = llm_ready_json_path(session_dir, packet_id)
    markdown_path = llm_ready_markdown_path(session_dir, packet_id)
    json_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_llm_bundle_markdown(bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def write_intake_request_bundle(*, session_dir: Path, packet: dict[str, Any], request_bundle: dict[str, Any]) -> dict[str, Path]:
    packet_id = str(packet.get("packet_id") or "packet")
    json_path = session_dir / "llm-requests" / f"{packet_id}.json"
    markdown_path = session_dir / "llm-requests" / f"{packet_id}.md"
    json_path.write_text(json.dumps(request_bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_intake_request_markdown(request_bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def write_intake_response_bundle(*, session_dir: Path, packet: dict[str, Any], response_bundle: dict[str, Any]) -> dict[str, Path]:
    packet_id = str(packet.get("packet_id") or "packet")
    json_path = session_dir / "llm-responses" / f"{packet_id}.json"
    markdown_path = session_dir / "llm-responses" / f"{packet_id}.md"
    json_path.write_text(json.dumps(response_bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_intake_response_markdown(response_bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def write_intake_proposal_bundle(*, session_dir: Path, packet: dict[str, Any], proposal_bundle: dict[str, Any]) -> dict[str, Path]:
    packet_id = str(packet.get("packet_id") or "packet")
    json_path = session_dir / "proposals" / f"{packet_id}.json"
    markdown_path = session_dir / "proposals" / f"{packet_id}.md"
    json_path.write_text(json.dumps(proposal_bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_intake_proposal_markdown(proposal_bundle), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def resolve_session_path(*, output_dir: Path, session_ref: str | None) -> Path:
    if session_ref:
        explicit = Path(session_ref)
        if explicit.exists():
            if explicit.is_dir():
                explicit = explicit / "session.json"
            return explicit
        matches = sorted((output_dir / "intake").glob(f"*/{session_ref}/session.json"))
        if matches:
            return matches[-1]
        raise FileNotFoundError(f"Could not resolve intake session '{session_ref}'")
    candidates = sorted((output_dir / "intake").glob("*/*/session.json"))
    if not candidates:
        raise FileNotFoundError("No intake sessions found. Run `things intake start` first.")
    return candidates[-1]


def load_session_packets(session_dir: Path, session: dict[str, Any]) -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    for packet_id in session.get("packet_order", []):
        packets.append(load_json(packet_json_path(session_dir, str(packet_id))))
    return packets


def require_packet(packets: list[dict[str, Any]], packet_ref: str) -> dict[str, Any]:
    for packet in packets:
        if packet.get("packet_id") == packet_ref:
            return packet
    raise ValueError(f"Could not resolve intake packet '{packet_ref}'")


def load_llm_bundle(*, session_dir: Path, packet: dict[str, Any]) -> dict[str, Any]:
    proposal = ensure_dict(packet.get("proposal"))
    bundle_ref = proposal.get("llm_bundle_ref") or proposal.get("llm_request_ref")
    if bundle_ref:
        return load_json(session_dir / str(bundle_ref))
    packet_id = str(packet.get("packet_id") or "")
    return load_json(llm_ready_json_path(session_dir, packet_id))


def choose_next_packet(packets: list[dict[str, Any]]) -> dict[str, Any] | None:
    for status in INTAKE_REVIEWABLE_STATUSES:
        for packet in packets:
            if packet.get("status") == status:
                return packet
    return None


def refresh_session_counts(session: dict[str, Any], packets: list[dict[str, Any]]) -> None:
    counts = {
        "total_packets": len(packets),
        "new_packets": sum(1 for packet in packets if packet.get("status") == "new"),
        "reviewing_packets": sum(1 for packet in packets if packet.get("status") == "reviewing"),
        "reviewed_packets": sum(1 for packet in packets if packet.get("status") == "reviewed"),
        "proposed_packets": sum(1 for packet in packets if packet.get("status") == "proposed"),
    }
    next_packet = choose_next_packet(packets)
    session["counts"] = counts
    session["next_packet_id"] = next_packet.get("packet_id") if isinstance(next_packet, dict) else None
    session["status"] = "complete" if next_packet is None else "active"


def is_incomplete_todo(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().lower()
    return status not in {"complete", "completed", "canceled", "cancelled"}


def normalize_classification(answer: str) -> str:
    normalized = " ".join(str(answer).strip().lower().split())
    return CLASSIFICATION_ALIASES.get(normalized, "unclear")


def transcript_entry(*, prompt: str, answer: str, normalized: str) -> dict[str, Any]:
    return compact(
        {
            "prompt": prompt,
            "answer": answer,
            "normalized": normalized,
            "recorded_at": now_utc(),
        }
    )


def normalized_review(packet: dict[str, Any]) -> dict[str, Any]:
    review = ensure_dict(packet.get("review"))
    normalized = ensure_dict(review.get("normalized"))
    return {
        "classification": normalized.get("classification") or packet.get("review_unit_type") or "single_action",
        "outcome": normalized.get("outcome") or "",
        "next_action": normalized.get("next_action") or "",
        "notes": normalized.get("notes") or "",
    }


def first_source_item(packet: dict[str, Any]) -> dict[str, Any]:
    source_items = packet.get("source_items")
    if isinstance(source_items, list) and source_items:
        first = source_items[0]
        if isinstance(first, dict):
            return first
    return {}


def render_session_markdown(session: dict[str, Any], packets: list[dict[str, Any]]) -> str:
    counts = ensure_dict(session.get("counts"))
    lines = [
        "# Things Intake Session",
        "",
        f"- Session ID: {session.get('session_id')}",
        f"- Generated at: {session.get('generated_at')}",
        f"- Updated at: {session.get('updated_at')}",
        f"- Next packet: {session.get('next_packet_id') or 'none'}",
        f"- Total packets: {counts.get('total_packets', 0)}",
        f"- Reviewed packets: {counts.get('reviewed_packets', 0)}",
        "",
        "## Packets",
    ]
    for packet in packets:
        source_item = first_source_item(packet)
        lines.append(
            f"- {packet.get('packet_id')}: [{packet.get('status')}] {single_line(source_item.get('title') or '(untitled)')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_packet_markdown(packet: dict[str, Any]) -> str:
    source_item = first_source_item(packet)
    review = normalized_review(packet)
    lines = [
        "# Things Intake Packet",
        "",
        f"- Packet ID: {packet.get('packet_id')}",
        f"- Status: {packet.get('status')}",
        f"- Review type: {packet.get('review_unit_type')}",
        f"- Source title: {single_line(source_item.get('title') or '(untitled)')}",
        f"- Outcome: {review.get('outcome') or ''}",
        f"- Next action: {review.get('next_action') or ''}",
        "",
        "## Notes",
        review.get("notes") or "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_llm_bundle_markdown(bundle: dict[str, Any]) -> str:
    review = ensure_dict(bundle.get("review"))
    lines = [
        "# Things Intake LLM Bundle",
        "",
        f"- Packet ID: {bundle.get('packet_id')}",
        f"- Review type: {bundle.get('review_unit_type')}",
        f"- Classification: {review.get('classification') or ''}",
        f"- Outcome: {review.get('outcome') or ''}",
        f"- Next action: {review.get('next_action') or ''}",
        "",
        "## Notes",
        str(review.get("notes") or ""),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_intake_request_markdown(request_bundle: dict[str, Any]) -> str:
    request = ensure_dict(request_bundle.get("request"))
    packet = ensure_dict(request_bundle.get("packet"))
    payload = ensure_dict(request_bundle.get("payload"))
    lines = [
        "# Things Intake Request",
        "",
        f"- Generated at: {request_bundle.get('generated_at')}",
        f"- Packet ID: {packet.get('packet_id')}",
        f"- Requested model: {request.get('requested_model')}",
        f"- Resolved model: {request.get('resolved_model')}",
        f"- Max tokens: {request.get('max_tokens')}",
        "",
        "## Consumer Options",
        "",
        "- External LLM: use the system prompt, instruction, response contract, and reviewed bundle below.",
        "- Full machine-readable request JSON is stored next to this Markdown file.",
        "",
        "## System Prompt",
        "",
        str(request.get("system") or ""),
        "",
        "## Instruction",
        "",
        str(request.get("instruction") or ""),
        "",
        "## Response Contract",
        "",
        json.dumps(request.get("response_contract", {}), indent=2, sort_keys=True),
        "",
        "## Reviewed Intake Bundle",
        "",
        json.dumps(payload, indent=2, sort_keys=True),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_intake_response_markdown(response_bundle: dict[str, Any]) -> str:
    decision = ensure_dict(response_bundle.get("decision"))
    parse_error = clean_text(response_bundle.get("parse_error"))
    if parse_error:
        lines = [
            "# Things Intake Response",
            "",
            f"- Generated at: {response_bundle.get('generated_at')}",
            f"- Source: {response_bundle.get('source')}",
            f"- Parse error: {parse_error}",
            "",
            "## Response Text",
            "",
            str(response_bundle.get("response_text") or ""),
        ]
        return "\n".join(lines).rstrip() + "\n"
    interpretation_kind = normalize_interpretation_kind(decision)
    recommended_home_kind = normalize_home_kind(decision, interpretation_kind=interpretation_kind)
    recommended_home_title = clean_text(decision.get("recommended_home_title"))
    lines = [
        "# Things Intake Response",
        "",
        f"- Generated at: {response_bundle.get('generated_at')}",
        f"- Source: {response_bundle.get('source')}",
        f"- Interpretation: {describe_interpretation_kind(interpretation_kind)}",
        f"- Confidence: {decision.get('confidence')}",
        f"- Retire recommendation: {decision.get('retire_recommendation')}",
        "",
        "## Structure",
        "",
        f"- Recommended home: {describe_home_kind(recommended_home_kind)}",
        f"- Recommended home title: {recommended_home_title or '-'}",
        f"- Home note: {clean_text(decision.get('recommended_home_note')) or '-'}",
        "",
        "## Summary",
        "",
        str(decision.get("summary") or ""),
        "",
        "## Response Text",
        "",
        str(response_bundle.get("response_text") or ""),
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_intake_proposal_markdown(proposal_bundle: dict[str, Any]) -> str:
    decision = ensure_dict(proposal_bundle.get("decision"))
    counts = ensure_dict(proposal_bundle.get("counts"))
    staged_actions = ensure_dict(proposal_bundle.get("staged_actions"))
    interpretation_kind = normalize_interpretation_kind(decision)
    recommended_home_kind = normalize_home_kind(decision, interpretation_kind=interpretation_kind)
    recommended_home_title = clean_text(decision.get("recommended_home_title"))
    supporting_tasks = clean_string_list(decision.get("proposed_supporting_tasks"))
    contexts = clean_string_list(decision.get("proposed_contexts"))
    lines = [
        "# Things Intake Proposal",
        "",
        f"- Generated at: {proposal_bundle.get('generated_at')}",
        f"- Interpretation: {describe_interpretation_kind(interpretation_kind)}",
        f"- Confidence: {decision.get('confidence')}",
        f"- Create previews: {counts.get('create_items', 0)}",
        f"- Retire previews: {counts.get('retire_legacy_items', 0)}",
        f"- Manual steps: {counts.get('manual_steps', 0)}",
        "",
        "## Summary",
        "",
        str(decision.get("summary") or ""),
        "",
        "## Reasoning",
        "",
        str(decision.get("reasoning_summary") or ""),
        "",
        "## Structure",
        "",
        f"- Interpretation: {describe_interpretation_kind(interpretation_kind)}",
        f"- Recommended home: {describe_home_kind(recommended_home_kind)}",
        f"- Recommended home title: {recommended_home_title or '-'}",
        f"- Home note: {clean_text(decision.get('recommended_home_note')) or '-'}",
        f"- Proposed project: {clean_text(decision.get('proposed_project')) or '-'}",
        f"- Proposed outcome: {clean_text(decision.get('proposed_outcome')) or '-'}",
        "",
        "## Active Work",
        "",
        f"- Proposed next action: {clean_text(decision.get('proposed_next_action')) or '-'}",
        f"- Suggested due: {clean_text(decision.get('proposed_due')) or '-'}",
    ]
    if supporting_tasks:
        lines.extend(["", "## Supporting Tasks", ""])
        lines.extend(f"- {item}" for item in supporting_tasks)
    if contexts:
        lines.extend(["", "## Contexts", ""])
        lines.extend(f"- {item}" for item in contexts)
    manual_steps = clean_string_list(staged_actions.get("manual_steps"))
    if manual_steps:
        lines.extend(["", "## Manual Steps", ""])
        lines.extend(f"- {item}" for item in manual_steps)
    return "\n".join(lines).rstrip() + "\n"


def default_session_dir(*, output_dir: Path, session_id: str) -> Path:
    day = session_id.removeprefix("intake-").split("T", 1)[0]
    return output_dir / "intake" / f"{day[:4]}-{day[4:6]}-{day[6:8]}" / session_id


def packet_json_path(session_dir: Path, packet_id: str) -> Path:
    return session_dir / "packets" / f"{packet_id}.json"


def packet_markdown_path(session_dir: Path, packet_id: str) -> Path:
    return session_dir / "packets" / f"{packet_id}.md"


def llm_ready_json_path(session_dir: Path, packet_id: str) -> Path:
    return session_dir / "llm-ready" / f"{packet_id}.json"


def llm_ready_markdown_path(session_dir: Path, packet_id: str) -> Path:
    return session_dir / "llm-ready" / f"{packet_id}.md"


def relative_to_session(session_dir: Path, path: Path) -> str:
    return str(path.relative_to(session_dir))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def ensure_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def string_or_none(value: Any) -> str | None:
    text = clean_text(value)
    return text or None


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (clean_text(entry) for entry in value) if item]


def normalize_interpretation_kind(decision: dict[str, Any]) -> str:
    raw = " ".join(clean_text(decision.get("interpretation_kind")).lower().split())
    if raw in INTAKE_INTERPRETATION_ALIASES:
        return INTAKE_INTERPRETATION_ALIASES[raw]
    classification = normalize_classification(clean_text(decision.get("classification")))
    if classification == "project":
        return "project_with_next_action"
    if classification == "trash":
        return "retire"
    if classification in {"single_action", "complete_already", "unclear"}:
        return classification
    if clean_text(decision.get("proposed_project")) or clean_text(decision.get("proposed_outcome")):
        return "project_with_next_action"
    retire_recommendation = clean_text(decision.get("retire_recommendation")).lower()
    if retire_recommendation == "complete_source" and not clean_text(decision.get("proposed_next_action")):
        return "complete_already"
    if retire_recommendation == "cancel_source" and not clean_text(decision.get("proposed_next_action")):
        return "retire"
    if clean_text(decision.get("proposed_next_action")):
        return "single_action"
    return "unclear"


def normalize_home_kind(decision: dict[str, Any], *, interpretation_kind: str) -> str:
    raw = " ".join(clean_text(decision.get("recommended_home_kind")).lower().split())
    aliased = INTAKE_HOME_ALIASES.get(raw)
    if aliased:
        return aliased
    if interpretation_kind in {"complete_already", "retire"}:
        return "not_applicable"
    if interpretation_kind == "unclear":
        return "manual_review"
    if interpretation_kind == "project_with_next_action":
        return "new_project"
    return "single_action_project"


def default_home_note(*, interpretation_kind: str, recommended_home_kind: str, recommended_home_title: str) -> str:
    if interpretation_kind == "complete_already":
        return "No new home is needed if this commitment is already complete."
    if interpretation_kind == "retire":
        return "No new home is needed if this item should be retired."
    if interpretation_kind == "unclear":
        return "Choose the final home manually after clarifying the item."
    if recommended_home_kind == "current_location":
        return "Keeping this in its current location is a reasonable default."
    if recommended_home_kind == "existing_project":
        if recommended_home_title:
            return f"File this under the existing project '{recommended_home_title}'."
        return "File this under the relevant existing project."
    if recommended_home_kind == "single_action_project":
        if recommended_home_title:
            return f"A single-action project such as '{recommended_home_title}' would give this action a stable home."
        return "A single-action project under the relevant area would give this action a stable home."
    if recommended_home_kind == "new_project":
        if recommended_home_title:
            return f"Create a project such as '{recommended_home_title}' if you want a dedicated home for this work."
        return "Create a dedicated project if this work needs its own home."
    return "Review the final home manually before filing the item."


def describe_interpretation_kind(value: str) -> str:
    return {
        "single_action": "Single action",
        "project_with_next_action": "Project with next action",
        "complete_already": "Complete already",
        "retire": "Retire",
        "unclear": "Unclear / needs review",
    }.get(value, value or "-")


def describe_home_kind(value: str) -> str:
    return {
        "not_applicable": "Not applicable",
        "current_location": "Current location",
        "existing_project": "Existing project",
        "single_action_project": "Single-action project",
        "new_project": "New project",
        "manual_review": "Manual review",
    }.get(value, value or "-")


def build_home_manual_steps(decision: dict[str, Any]) -> list[str]:
    interpretation_kind = normalize_interpretation_kind(decision)
    recommended_home_kind = normalize_home_kind(decision, interpretation_kind=interpretation_kind)
    recommended_home_title = clean_text(decision.get("recommended_home_title"))
    steps: list[str] = []
    if interpretation_kind == "project_with_next_action":
        steps.append("Project creation stays manual in this slice; review the proposed project before creating it.")
        if clean_text(decision.get("proposed_project")):
            steps.append(f"Suggested project title: {clean_text(decision.get('proposed_project'))}")
        if clean_text(decision.get("proposed_outcome")):
            steps.append(f"Suggested project outcome: {clean_text(decision.get('proposed_outcome'))}")
    if recommended_home_kind == "single_action_project":
        if recommended_home_title:
            steps.append(f"Consider placing this inside a single-action project: {recommended_home_title}")
        else:
            steps.append("Consider placing this inside a single-action project under the relevant area so it has a stable home.")
    elif recommended_home_kind == "existing_project":
        if recommended_home_title:
            steps.append(f"Consider filing this under existing project: {recommended_home_title}")
        else:
            steps.append("Consider filing this under the appropriate existing project.")
    elif recommended_home_kind == "new_project":
        if recommended_home_title:
            steps.append(f"Consider creating a project to hold this work: {recommended_home_title}")
        else:
            steps.append("Consider creating a project to hold this work.")
    elif recommended_home_kind == "current_location":
        steps.append("Keeping this in its current location is a reasonable default.")
    elif recommended_home_kind == "manual_review":
        steps.append("The item's final home needs human review before filing it.")
    return steps