from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermes_cli.codex_lane import CodexLaneResult
from tools.delegate_tool import delegate_task


def _make_parent():
    parent = MagicMock()
    parent.base_url = "https://chatgpt.com/backend-api/codex"
    parent.api_key = "test-token"
    parent.provider = "openai-codex"
    parent.api_mode = "codex_responses"
    parent.model = "gpt-5-codex"
    parent.platform = "cli"
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    parent._session_db = MagicMock()
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._print_fn = None
    parent.tool_progress_callback = None
    parent.thinking_callback = None
    parent.session_id = "parent-session"
    parent.parent_session_id = "root-session"
    parent._memory_manager = None
    return parent


def test_delegate_task_routes_to_codex_lane_backend():
    parent = _make_parent()
    fake_result = CodexLaneResult(
        lane_name="delegate-parent-session-1",
        run_id="run-1",
        status="completed",
        final_response="lane completed",
        thread_id="thread-123",
        usage={"input_tokens": 5, "output_tokens": 3},
        item_types=["command_execution", "agent_message"],
        commands=[{"type": "command_execution", "command": "pwd"}],
        changed_files=["src/main.py"],
        failed_items=[],
        todo_list=None,
        artifacts={"summary_path": ".hermes/codex-lanes/delegate-parent-session-1/runs/run-1/summary.json"},
        raw={},
    )

    with patch("tools.delegate_tool.run_codex_lane_turn", return_value=fake_result) as mock_run:
        payload = json.loads(
            delegate_task(
                goal="Implement the seam",
                context="repo local",
                execution_backend="codex_sdk_lane",
                parent_agent=parent,
            )
        )

    entry = payload["results"][0]
    assert entry["status"] == "completed"
    assert entry["summary"] == "lane completed"
    assert entry["execution_backend"] == "codex_sdk_lane"
    assert entry["codex_lane"]["lane_name"] == "delegate-parent-session-1"
    assert entry["codex_lane"]["thread_id"] == "thread-123"
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert kwargs["repo_root"].startswith("/")
    assert kwargs["session_id"] == "parent-session"
    assert kwargs["parent_session_id"] == "root-session"


def test_delegate_task_defaults_to_normal_child_path_without_execution_backend():
    parent = _make_parent()
    fake_child = SimpleNamespace(_delegate_saved_tool_names=None)
    fake_entry = {
        "task_index": 0,
        "status": "completed",
        "summary": "default child completed",
        "api_calls": 1,
        "duration_seconds": 0.01,
        "model": None,
        "exit_reason": "completed",
        "tokens": {"input": 0, "output": 0},
        "tool_trace": [],
    }

    with patch("tools.delegate_tool._load_config", return_value={}), patch(
        "tools.delegate_tool._resolve_delegation_credentials",
        return_value={"model": None, "provider": None, "base_url": None, "api_key": None, "api_mode": None},
    ), patch("tools.delegate_tool._build_child_agent", return_value=fake_child) as mock_build_child, patch(
        "tools.delegate_tool._run_single_child", return_value=fake_entry
    ) as mock_run_single_child, patch("tools.delegate_tool._run_codex_lane_child") as mock_run_codex_lane_child:
        payload = json.loads(
            delegate_task(
                goal="Implement the seam",
                context="repo local",
                parent_agent=parent,
            )
        )

    entry = payload["results"][0]
    assert entry["status"] == "completed"
    assert entry["summary"] == "default child completed"
    mock_build_child.assert_called_once()
    mock_run_single_child.assert_called_once_with(0, "Implement the seam", fake_child, parent)
    mock_run_codex_lane_child.assert_not_called()


def test_delegate_task_does_not_infer_codex_lane_from_parent_metadata():
    parent = _make_parent()
    fake_child = SimpleNamespace(_delegate_saved_tool_names=None)
    fake_entry = {
        "task_index": 0,
        "status": "completed",
        "summary": "normal child path",
        "api_calls": 1,
        "duration_seconds": 0.01,
        "model": None,
        "exit_reason": "completed",
        "tokens": {"input": 0, "output": 0},
        "tool_trace": [],
    }

    with patch("tools.delegate_tool._load_config", return_value={}), patch(
        "tools.delegate_tool._resolve_delegation_credentials",
        return_value={"model": None, "provider": None, "base_url": None, "api_key": None, "api_mode": None},
    ), patch("tools.delegate_tool._build_child_agent", return_value=fake_child), patch(
        "tools.delegate_tool._run_single_child", return_value=fake_entry
    ) as mock_run_single_child, patch("tools.delegate_tool._run_codex_lane_child") as mock_run_codex_lane_child:
        payload = json.loads(
            delegate_task(
                goal="Implement the seam",
                parent_agent=parent,
            )
        )

    entry = payload["results"][0]
    assert entry["summary"] == "normal child path"
    assert "execution_backend" not in entry
    mock_run_single_child.assert_called_once()
    mock_run_codex_lane_child.assert_not_called()


def test_delegate_task_rejects_invalid_codex_lane_mixins():
    parent = _make_parent()

    result = json.loads(
        delegate_task(
            goal="Implement the seam",
            execution_backend="codex_sdk_lane",
            acp_command="claude",
            parent_agent=parent,
        )
    )

    assert "error" in result
    assert "cannot be combined with ACP transport overrides" in result["error"]
