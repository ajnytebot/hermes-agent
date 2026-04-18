from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_cli.codex_lane import (
    CodexLaneError,
    inspect_codex_lane,
    list_codex_lanes,
    run_codex_lane_turn,
)
from hermes_state import SessionDB


REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_SDK_SOURCE = """
class FakeThread {
  constructor(threadId) {
    this.threadId = threadId;
  }

  async runStreamed(prompt, options = {}) {
    const outputSchema = options.outputSchema || null;
    const events = [
      { type: 'thread.started', thread_id: this.threadId },
      { type: 'item.completed', item: { type: 'command_execution', command: 'pwd', exit_code: 0, cwd: '/tmp', status: 'completed' } },
      { type: 'item.completed', item: { type: 'file_change', path: 'src/example.py', change_type: 'modified' } },
      { type: 'item.completed', item: { type: 'agent_message', text: `done:${prompt}:${outputSchema ? 'schema' : 'plain'}` } },
      { type: 'turn.completed', usage: { input_tokens: 11, output_tokens: 7 } },
    ];
    return {
      events: (async function* () {
        for (const event of events) {
          yield event;
        }
      })(),
    };
  }
}

export class Codex {
  startThread() {
    return new FakeThread('thread-started');
  }

  resumeThread(threadId) {
    return new FakeThread(threadId || 'thread-resumed');
  }
}
"""


@pytest.fixture()
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo_root = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT / "scripts" / "codex-lane",
        repo_root / "scripts" / "codex-lane",
        ignore=shutil.ignore_patterns("node_modules"),
    )
    (repo_root / ".hermes").mkdir(parents=True, exist_ok=True)
    fake_sdk = tmp_path / "fake-codex-sdk.mjs"
    fake_sdk.write_text(FAKE_SDK_SOURCE, encoding="utf-8")
    monkeypatch.setenv("HERMES_CODEX_SDK_MODULE", str(fake_sdk))
    return repo_root


def test_run_codex_lane_turn_round_trips_artifacts_and_session_refs(fake_repo: Path, tmp_path: Path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="session-1", source="cli", model="gpt-5-codex")

    result = run_codex_lane_turn(
        repo_root=str(fake_repo),
        lane_name="delegate-session-1-1",
        working_directory=str(fake_repo),
        prompt="implement the seam",
        output_schema={"type": "object", "properties": {"status": {"type": "string"}}},
        session_id="session-1",
        parent_session_id="parent-1",
        session_db=db,
    )

    assert result.status == "completed"
    assert result.thread_id == "thread-started"
    assert result.final_response == "done:implement the seam:schema"
    assert result.changed_files == ["src/example.py"]
    assert result.commands[0]["command"] == "pwd"
    assert (fake_repo / result.artifacts["summary_path"]).exists()
    assert (fake_repo / result.artifacts["event_log_path"]).exists()

    model_config = db.get_session_model_config("session-1")
    assert model_config["execution_backend"] == "codex_sdk_lane"
    assert model_config["codex_lane_ref"]["lane_name"] == "delegate-session-1-1"
    assert model_config["codex_lane_ref"]["thread_id"] == "thread-started"

    inspection = inspect_codex_lane(repo_root=str(fake_repo), lane_name="delegate-session-1-1")
    assert inspection.lane["thread_id"] == "thread-started"

    lanes = list_codex_lanes(repo_root=str(fake_repo))
    assert lanes.lanes[0]["lane_name"] == "delegate-session-1-1"

    db.close()


def test_run_codex_lane_turn_requires_absolute_paths(fake_repo: Path):
    with pytest.raises(CodexLaneError):
        run_codex_lane_turn(
            repo_root="relative/path",
            lane_name="lane",
            working_directory=str(fake_repo),
            prompt="hello",
        )


def test_run_codex_lane_turn_surfaces_missing_node_with_recovery_command(
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("hermes_cli.codex_lane.shutil.which", lambda _name: None)

    with pytest.raises(CodexLaneError) as excinfo:
        run_codex_lane_turn(
            repo_root=str(fake_repo),
            lane_name="lane",
            working_directory=str(fake_repo),
            prompt="hello",
        )

    message = str(excinfo.value)
    assert "Node.js is missing" in message
    assert "looked for `node` on PATH" in message
    assert "cd " in message
    assert "npm install" in message


def test_run_codex_lane_turn_surfaces_missing_sdk_when_npm_is_unavailable(
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("HERMES_CODEX_SDK_MODULE", raising=False)
    monkeypatch.setattr("hermes_cli.codex_lane.shutil.which", lambda name: "/usr/bin/node" if name == "node" else None)

    with pytest.raises(CodexLaneError) as excinfo:
        run_codex_lane_turn(
            repo_root=str(fake_repo),
            lane_name="lane",
            working_directory=str(fake_repo),
            prompt="hello",
        )

    message = str(excinfo.value)
    assert "@openai/codex-sdk" in message
    assert "looked for `npm` on PATH" in message
    assert str(fake_repo / "scripts" / "codex-lane" / "node_modules" / "@openai" / "codex-sdk" / "package.json") in message
    assert "npm install" in message


def test_run_codex_lane_turn_surfaces_bootstrap_failure_with_context(
    fake_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("HERMES_CODEX_SDK_MODULE", raising=False)
    monkeypatch.setattr("hermes_cli.codex_lane.shutil.which", lambda _name: "/usr/bin/fake")
    monkeypatch.setattr(
        "hermes_cli.codex_lane.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="network timeout"),
    )

    with pytest.raises(CodexLaneError) as excinfo:
        run_codex_lane_turn(
            repo_root=str(fake_repo),
            lane_name="lane",
            working_directory=str(fake_repo),
            prompt="hello",
        )

    message = str(excinfo.value)
    assert "bootstrap failed" in message
    assert "@openai/codex-sdk" in message
    assert "network timeout" in message
    assert "npm install" in message
    assert str(fake_repo / "scripts" / "codex-lane") in message
