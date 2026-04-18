from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key
from hermes_cli.codex_lane import CodexLaneInspection, CodexLaneResult


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
        thread_id="thread-1",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    session_entry = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._background_tasks = set()
    runner._session_db = MagicMock()
    runner._session_db.get_session_model_config.return_value = {}
    runner._draining = False
    runner._is_user_authorized = lambda _source: True
    runner._run_in_executor_with_context = _run_sync_in_context
    return runner


async def _run_sync_in_context(func, *args):
    return func(*args)


@pytest.mark.asyncio
async def test_codex_run_is_non_blocking_and_posts_completion(monkeypatch, tmp_path):
    import gateway.run as gateway_run
    import hermes_cli.codex_lane as codex_lane

    runner = _make_runner()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    def _fake_run_codex_lane_turn(**kwargs):
        assert kwargs["repo_root"] == str(tmp_path)
        assert kwargs["working_directory"] == str(tmp_path)
        assert kwargs["session_id"] == "sess-1"
        return CodexLaneResult(
            lane_name=kwargs["lane_name"],
            run_id="run-1",
            status="completed",
            final_response="done",
            thread_id="thread-1",
            usage=None,
            item_types=[],
            commands=[],
            changed_files=["cli.py"],
            failed_items=[],
            todo_list=None,
            artifacts={},
            raw={},
        )

    monkeypatch.setattr(codex_lane, "run_codex_lane_turn", _fake_run_codex_lane_turn)
    monkeypatch.setattr(gateway_run, "logger", MagicMock())

    result = await runner._handle_codex_command(_make_event("/codex run implement seam"))

    assert "Codex lane started" in result
    assert runner._background_tasks

    await asyncio.gather(*list(runner._background_tasks))

    runner.adapters[Platform.TELEGRAM].send.assert_awaited_once()
    sent_kwargs = runner.adapters[Platform.TELEGRAM].send.await_args.kwargs
    assert "✅ Codex lane complete:" in sent_kwargs["content"]
    assert sent_kwargs["metadata"] == {"thread_id": "thread-1"}


@pytest.mark.asyncio
async def test_codex_inspect_defaults_to_session_lane_ref(monkeypatch, tmp_path):
    import hermes_cli.codex_lane as codex_lane

    runner = _make_runner()
    runner._session_db.get_session_model_config.return_value = {
        "codex_lane_ref": {"lane_name": "delegate-session-1-1"}
    }
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))

    def _fake_inspect_codex_lane(*, repo_root: str, lane_name: str):
        assert repo_root == str(tmp_path)
        assert lane_name == "delegate-session-1-1"
        return CodexLaneInspection(
            lane={
                "lane_name": lane_name,
                "status": "completed",
                "thread_id": "thread-1",
                "last_run": {"run_id": "run-1"},
            }
        )

    monkeypatch.setattr(codex_lane, "inspect_codex_lane", _fake_inspect_codex_lane)

    result = await runner._handle_codex_command(_make_event("/codex inspect"))

    assert "Codex lane: delegate-session-1-1" in result
    runner.adapters[Platform.TELEGRAM].send.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_dispatches_codex_command(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()
    runner._handle_codex_command = AsyncMock(return_value="codex ok")
    runner._run_agent = AsyncMock(side_effect=AssertionError("codex leaked to the agent"))

    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"}
    )

    result = await runner._handle_message(_make_event("/codex list"))

    assert result == "codex ok"
    runner._handle_codex_command.assert_awaited_once()
    runner._run_agent.assert_not_called()


@pytest.mark.asyncio
async def test_codex_bypasses_running_agent_guard(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()
    session_key = build_session_key(_make_source())
    runner._running_agents[session_key] = MagicMock()
    runner._running_agents_ts[session_key] = 0
    runner._handle_codex_command = AsyncMock(return_value="codex guard bypassed")
    runner._run_agent = AsyncMock(side_effect=AssertionError("codex leaked to the agent"))

    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"}
    )

    result = await runner._handle_message(_make_event("/codex list"))

    assert result == "codex guard bypassed"
    runner._handle_codex_command.assert_awaited_once()
    runner._run_agent.assert_not_called()
