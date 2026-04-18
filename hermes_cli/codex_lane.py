from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_THREAD_OPTIONS: Dict[str, Any] = {
    "approvalPolicy": "never",
    "webSearchMode": "disabled",
    "skipGitRepoCheck": True,
    "sandboxMode": "workspace-write",
}


class CodexLaneError(RuntimeError):
    """Raised when the bounded Codex lane adapter fails."""


@dataclass
class CodexLaneResult:
    lane_name: str
    run_id: Optional[str]
    status: str
    final_response: str
    thread_id: Optional[str]
    usage: Optional[Dict[str, Any]]
    item_types: List[str]
    commands: List[Dict[str, Any]]
    changed_files: List[str]
    failed_items: List[Dict[str, Any]]
    todo_list: Any
    artifacts: Dict[str, str]
    raw: Dict[str, Any]


@dataclass
class CodexLaneInspection:
    lane: Dict[str, Any]


@dataclass
class CodexLaneSummary:
    lanes: List[Dict[str, Any]]


def _repo_root_path(repo_root: str) -> Path:
    root = Path(repo_root)
    if not root.is_absolute():
        raise CodexLaneError("repo_root must be an absolute path.")
    if not root.exists() or not root.is_dir():
        raise CodexLaneError(f"repo_root does not exist: {repo_root}")
    return root


def _adapter_path(repo_root: Path) -> Path:
    return repo_root / "scripts" / "codex-lane" / "adapter.mjs"


def _lane_dir(repo_root: Path) -> Path:
    return repo_root / "scripts" / "codex-lane"


def _sdk_package_path(repo_root: Path) -> Path:
    return _lane_dir(repo_root) / "node_modules" / "@openai" / "codex-sdk" / "package.json"


def _codex_bootstrap_command(repo_root: Path) -> str:
    return f"cd {shlex.quote(str(_lane_dir(repo_root)))} && npm install"


def _ensure_node_available(repo_root: Path) -> str:
    node_bin = shutil.which("node")
    if not node_bin:
        raise CodexLaneError(
            "Codex lane could not start because Node.js is missing. Hermes looked for `node` on PATH "
            "and did not find it. Install Node.js so both `node` and `npm` are available, then run: "
            f"{_codex_bootstrap_command(repo_root)}"
        )
    return node_bin


def _ensure_codex_sdk_dependency(repo_root: Path) -> None:
    if os.getenv("HERMES_CODEX_SDK_MODULE"):
        return

    sdk_package = _sdk_package_path(repo_root)
    if sdk_package.exists():
        return

    lane_dir = _lane_dir(repo_root)
    npm_bin = shutil.which("npm")
    bootstrap_command = _codex_bootstrap_command(repo_root)
    if not npm_bin:
        raise CodexLaneError(
            "Codex lane dependency missing: Hermes expected `@openai/codex-sdk` at "
            f"{sdk_package}, but it is not installed. Hermes also looked for `npm` on PATH and did not "
            f"find it, so it could not bootstrap the dependency automatically. After `npm` is available, run: {bootstrap_command}"
        )

    completed = subprocess.run(
        [npm_bin, "install", "--no-audit", "--no-fund"],
        cwd=str(lane_dir),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not sdk_package.exists():
        detail = completed.stderr.strip() or completed.stdout.strip() or "npm install failed"
        raise CodexLaneError(
            "Codex lane bootstrap failed. Hermes expected `@openai/codex-sdk` at "
            f"{sdk_package} and tried to install it by running `{bootstrap_command}` in {lane_dir}, "
            f"but the bootstrap step failed: {detail}"
        )


def _request_payload(
    *,
    command: str,
    repo_root: Path,
    lane_name: str,
    working_directory: Optional[str] = None,
    prompt: Optional[str] = None,
    output_schema: Optional[dict] = None,
    resume: bool = True,
    session_id: Optional[str] = None,
    parent_session_id: Optional[str] = None,
    thread_options: Optional[Dict[str, Any]] = None,
    turn_options: Optional[Dict[str, Any]] = None,
    artifacts_dir: Optional[str] = None,
) -> Dict[str, Any]:
    wd = Path(working_directory) if working_directory else repo_root
    if not wd.is_absolute():
        raise CodexLaneError("working_directory must be an absolute path.")

    merged_thread_options = dict(DEFAULT_THREAD_OPTIONS)
    if thread_options:
        merged_thread_options.update(thread_options)
    merged_thread_options["workingDirectory"] = str(wd)

    merged_turn_options = dict(turn_options or {})
    if output_schema is not None:
        merged_turn_options["outputSchema"] = output_schema

    payload: Dict[str, Any] = {
        "command": command,
        "repo_root": str(repo_root),
        "lane_name": lane_name,
        "resume": resume,
        "thread_options": merged_thread_options,
        "hermes_refs": {
            "session_id": session_id,
            "parent_session_id": parent_session_id,
        },
    }
    if prompt is not None:
        payload["prompt"] = prompt
    if merged_turn_options:
        payload["turn_options"] = merged_turn_options
    if artifacts_dir is not None:
        payload["artifacts_dir"] = artifacts_dir
    return payload


def _invoke_adapter(repo_root: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    adapter = _adapter_path(repo_root)
    if not adapter.exists():
        raise CodexLaneError(f"Codex lane adapter not found: {adapter}")

    node_bin = _ensure_node_available(repo_root)
    if payload.get("command") == "run-turn":
        _ensure_codex_sdk_dependency(repo_root)

    request_root = repo_root / ".hermes" / "codex-lanes" / "_requests"
    request_root.mkdir(parents=True, exist_ok=True)
    request_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            prefix="request-",
            dir=request_root,
            delete=False,
        ) as handle:
            json.dump(payload, handle)
            handle.write("\n")
            request_path = handle.name

        env = os.environ.copy()
        lane_node_modules = repo_root / "scripts" / "codex-lane" / "node_modules"
        existing_path = env.get("NODE_PATH", "")
        env["NODE_PATH"] = os.pathsep.join([str(lane_node_modules), existing_path]) if existing_path else str(lane_node_modules)

        completed = subprocess.run(
            [node_bin, str(adapter), payload["command"], "--request", request_path],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "adapter failed"
            raise CodexLaneError(detail)
        stdout = completed.stdout.strip()
        if not stdout:
            raise CodexLaneError("adapter returned empty stdout")
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CodexLaneError(f"adapter returned invalid JSON: {exc}") from exc
    finally:
        if request_path:
            try:
                Path(request_path).unlink(missing_ok=True)
            except Exception:
                pass


def _normalize_result(data: Dict[str, Any]) -> CodexLaneResult:
    return CodexLaneResult(
        lane_name=str(data.get("lane_name") or ""),
        run_id=data.get("run_id"),
        status=str(data.get("status") or "error"),
        final_response=str(data.get("final_response") or ""),
        thread_id=data.get("thread_id"),
        usage=data.get("usage"),
        item_types=list(data.get("item_types") or []),
        commands=list(data.get("commands") or []),
        changed_files=list(data.get("changed_files") or []),
        failed_items=list(data.get("failed_items") or []),
        todo_list=data.get("todo_list"),
        artifacts=dict(data.get("artifacts") or {}),
        raw=data,
    )


def run_codex_lane_turn(
    *,
    repo_root: str,
    lane_name: str,
    working_directory: str,
    prompt: str,
    output_schema: Optional[dict] = None,
    resume: bool = True,
    session_id: Optional[str] = None,
    parent_session_id: Optional[str] = None,
    thread_options: Optional[Dict[str, Any]] = None,
    turn_options: Optional[Dict[str, Any]] = None,
    artifacts_dir: Optional[str] = None,
    session_db: Any = None,
) -> CodexLaneResult:
    root = _repo_root_path(repo_root)
    payload = _request_payload(
        command="run-turn",
        repo_root=root,
        lane_name=lane_name,
        working_directory=working_directory,
        prompt=prompt,
        output_schema=output_schema,
        resume=resume,
        session_id=session_id,
        parent_session_id=parent_session_id,
        thread_options=thread_options,
        turn_options=turn_options,
        artifacts_dir=artifacts_dir,
    )
    result = _normalize_result(_invoke_adapter(root, payload))
    if result.status != "completed" and not result.final_response:
        raise CodexLaneError(
            str(
                result.raw.get("error")
                or f"Codex lane run ended with status '{result.status}' without a final response."
            )
        )
    if session_db is not None and session_id:
        session_db.update_codex_lane_refs(
            session_id,
            execution_backend="codex_sdk_lane",
            lane_name=result.lane_name,
            thread_id=result.thread_id,
            last_run_id=result.run_id,
            status=result.status,
            summary_path=result.artifacts.get("summary_path"),
            final_path=result.artifacts.get("final_path"),
        )
    return result


def inspect_codex_lane(
    *,
    repo_root: str,
    lane_name: str,
    session_id: Optional[str] = None,
    parent_session_id: Optional[str] = None,
) -> CodexLaneInspection:
    root = _repo_root_path(repo_root)
    payload = _request_payload(
        command="inspect-lane",
        repo_root=root,
        lane_name=lane_name,
        session_id=session_id,
        parent_session_id=parent_session_id,
    )
    return CodexLaneInspection(lane=_invoke_adapter(root, payload)["lane"])


def list_codex_lanes(*, repo_root: str) -> CodexLaneSummary:
    root = _repo_root_path(repo_root)
    payload = _request_payload(
        command="list-lanes",
        repo_root=root,
        lane_name="registry",
    )
    return CodexLaneSummary(lanes=list(_invoke_adapter(root, payload).get("lanes") or []))
