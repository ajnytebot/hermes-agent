from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from hermes_cli.codex_lane import CodexLaneInspection, CodexLaneResult, CodexLaneSummary


@dataclass(frozen=True)
class ParsedCodexCommand:
    action: str
    prompt: str = ""
    lane_name: Optional[str] = None
    message: Optional[str] = None


def codex_usage_text() -> str:
    return (
        "Usage: /codex run <prompt>\n"
        "       /codex inspect [lane-name]\n"
        "       /codex list"
    )


def parse_codex_command(text: str) -> ParsedCodexCommand:
    raw = (text or "").strip()
    if raw.startswith("/codex"):
        raw = raw[len("/codex"):].strip()
    if not raw:
        return ParsedCodexCommand(action="usage", message=codex_usage_text())

    parts = raw.split(None, 1)
    subcommand = parts[0].lower()
    remainder = parts[1].strip() if len(parts) > 1 else ""

    if subcommand == "run":
        if not remainder:
            return ParsedCodexCommand(action="error", message=codex_usage_text())
        return ParsedCodexCommand(action="run", prompt=remainder)

    if subcommand == "inspect":
        return ParsedCodexCommand(action="inspect", lane_name=remainder or None)

    if subcommand == "list":
        if remainder:
            return ParsedCodexCommand(action="error", message=codex_usage_text())
        return ParsedCodexCommand(action="list")

    return ParsedCodexCommand(
        action="error",
        message=f"Unknown /codex subcommand: {subcommand}\n{codex_usage_text()}",
    )


def get_session_codex_lane_ref(*, session_db: Any = None, session_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    if session_db is None or not session_id:
        return None
    getter = getattr(session_db, "get_session_model_config", None)
    if not callable(getter):
        return None
    model_config = getter(session_id) or {}
    if not isinstance(model_config, Mapping):
        return None
    lane_ref = model_config.get("codex_lane_ref")
    return dict(lane_ref) if isinstance(lane_ref, Mapping) else None


def choose_codex_lane_name(*, session_id: Optional[str] = None, session_db: Any = None) -> str:
    existing_ref = get_session_codex_lane_ref(session_db=session_db, session_id=session_id)
    existing_lane = (existing_ref or {}).get("lane_name") if existing_ref else None
    if isinstance(existing_lane, str) and existing_lane.strip():
        return existing_lane.strip()

    base = (session_id or "").strip()
    if not base:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        base = f"session-{timestamp}"
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-._") or "session"
    lane_name = f"codex-{base}"
    return lane_name[:64].rstrip("-._") or "codex-session"


def resolve_codex_inspect_lane(
    *,
    requested_lane_name: Optional[str],
    session_db: Any = None,
    session_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    if requested_lane_name:
        return requested_lane_name, None
    lane_ref = get_session_codex_lane_ref(session_db=session_db, session_id=session_id)
    lane_name = (lane_ref or {}).get("lane_name") if lane_ref else None
    if isinstance(lane_name, str) and lane_name.strip():
        return lane_name.strip(), None
    return None, "No Codex lane is stored for this session yet. Use /codex run <prompt> first, or /codex inspect <lane-name>."


def _preview(text: str, limit: int = 160) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "(no response)"
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 3] + "..."


def format_codex_run_started(*, lane_name: str, prompt: str) -> str:
    return (
        f"🔧 Codex lane started: {lane_name}\n"
        f"Prompt: \"{_preview(prompt, 80)}\"\n"
        "Runs in the background — results will appear here when done."
    )


def format_codex_run_completed(result: CodexLaneResult) -> str:
    changed = result.changed_files[:3]
    changed_suffix = ""
    if changed:
        extra = "" if len(result.changed_files) <= 3 else f" (+{len(result.changed_files) - 3} more)"
        changed_suffix = f"\nChanged: {', '.join(changed)}{extra}"
    return (
        f"✅ Codex lane complete: {result.lane_name}\n"
        f"Status: {result.status}\n"
        f"Final: {_preview(result.final_response)}{changed_suffix}"
    )


def format_codex_run_failed(*, lane_name: str, error: str) -> str:
    return f"❌ Codex lane failed: {lane_name}\nError: {_preview(error, 220)}"


def format_codex_inspection(inspection: CodexLaneInspection) -> str:
    lane = inspection.lane or {}
    lane_name = lane.get("lane_name") or "(unknown)"
    status = lane.get("status") or "unknown"
    thread_id = lane.get("thread_id") or "(none)"
    last_run = lane.get("last_run") or {}
    last_run_id = last_run.get("run_id") or "(none)"
    return (
        f"Codex lane: {lane_name}\n"
        f"Status: {status}\n"
        f"Thread: {thread_id}\n"
        f"Last run: {last_run_id}"
    )


def format_codex_lane_list(summary: CodexLaneSummary) -> str:
    lanes = list(summary.lanes or [])
    if not lanes:
        return "No Codex lanes found in this repo yet."
    lines = ["Codex lanes:"]
    for lane in lanes[:8]:
        lane_name = lane.get("lane_name") or "(unknown)"
        status = lane.get("status") or "unknown"
        thread_id = lane.get("thread_id") or "(no thread)"
        lines.append(f"- {lane_name} — {status} — {thread_id}")
    if len(lanes) > 8:
        lines.append(f"- ... {len(lanes) - 8} more")
    return "\n".join(lines)
