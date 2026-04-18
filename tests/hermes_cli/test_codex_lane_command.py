from __future__ import annotations

from pathlib import Path

from hermes_cli.codex_lane import CodexLaneInspection, CodexLaneResult, CodexLaneSummary
from hermes_cli.codex_lane_command import (
    choose_codex_lane_name,
    format_codex_inspection,
    format_codex_lane_list,
    format_codex_run_completed,
    get_session_codex_lane_ref,
    parse_codex_command,
    resolve_codex_inspect_lane,
)
from hermes_state import SessionDB


def test_parse_codex_run_command():
    parsed = parse_codex_command("/codex run implement the seam")

    assert parsed.action == "run"
    assert parsed.prompt == "implement the seam"


def test_parse_codex_bare_command_returns_usage():
    parsed = parse_codex_command("/codex")

    assert parsed.action == "usage"
    assert "Usage: /codex run <prompt>" in (parsed.message or "")


def test_choose_codex_lane_name_prefers_session_lane_ref(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="session-1", source="cli", model="gpt-5-codex")
    db.update_codex_lane_refs(
        "session-1",
        execution_backend="codex_sdk_lane",
        lane_name="delegate-session-1-1",
        thread_id="thread-1",
        last_run_id="run-1",
        status="completed",
    )

    assert get_session_codex_lane_ref(session_db=db, session_id="session-1")["lane_name"] == "delegate-session-1-1"
    assert choose_codex_lane_name(session_id="session-1", session_db=db) == "delegate-session-1-1"

    db.close()


def test_resolve_codex_inspect_lane_uses_session_lane_ref(tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="session-1", source="cli", model="gpt-5-codex")
    db.update_codex_lane_refs(
        "session-1",
        execution_backend="codex_sdk_lane",
        lane_name="delegate-session-1-1",
        thread_id="thread-1",
        last_run_id="run-1",
        status="completed",
    )

    lane_name, message = resolve_codex_inspect_lane(
        requested_lane_name=None,
        session_db=db,
        session_id="session-1",
    )

    assert lane_name == "delegate-session-1-1"
    assert message is None

    db.close()


def test_resolve_codex_inspect_lane_without_ref_returns_guidance():
    lane_name, message = resolve_codex_inspect_lane(
        requested_lane_name=None,
        session_db=None,
        session_id=None,
    )

    assert lane_name is None
    assert "Use /codex run <prompt> first" in (message or "")


def test_formatters_render_concise_summaries():
    result = CodexLaneResult(
        lane_name="codex-session-1",
        run_id="run-1",
        status="completed",
        final_response="Implemented the seam and updated tests.",
        thread_id="thread-1",
        usage=None,
        item_types=[],
        commands=[],
        changed_files=["cli.py", "gateway/run.py", "tests/gateway/test_codex_command.py", "tests/hermes_cli/test_commands.py"],
        failed_items=[],
        todo_list=None,
        artifacts={"summary_path": ".hermes/codex-lanes/foo/summary.md"},
        raw={},
    )
    inspection = CodexLaneInspection(
        lane={
            "lane_name": "codex-session-1",
            "status": "completed",
            "thread_id": "thread-1",
            "last_run": {"run_id": "run-1"},
        }
    )
    summary = CodexLaneSummary(
        lanes=[
            {"lane_name": "codex-session-1", "status": "completed", "thread_id": "thread-1"},
            {"lane_name": "codex-session-2", "status": "running", "thread_id": "thread-2"},
        ]
    )

    run_text = format_codex_run_completed(result)
    inspect_text = format_codex_inspection(inspection)
    list_text = format_codex_lane_list(summary)

    assert "✅ Codex lane complete: codex-session-1" in run_text
    assert "Changed: cli.py, gateway/run.py, tests/gateway/test_codex_command.py (+1 more)" in run_text
    assert "Codex lane: codex-session-1" in inspect_text
    assert "Codex lanes:" in list_text
    assert "codex-session-2 — running — thread-2" in list_text
