"""Shared Copilot control-plane command handling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, MutableMapping, Optional, Tuple

from ..claude.facade import ClaudeIntegration
from ..config.settings import Settings
from ..utils.constants import SAFE_MESSAGE_LENGTH
from .copilot_runtime import (
    ONCE_REASONING_KEY,
    SESSION_DISABLED_SKILLS_KEY,
    SESSION_EXTERNAL_SERVER_KEY,
    SESSION_MCP_ENV_MODE_KEY,
    SESSION_REASONING_KEY,
    SESSION_SKILL_DIRS_KEY,
    get_runtime_snapshot,
)
from .utils.html_format import escape_html

_PREVIEW_CHAR_LIMIT = 300
_PRE_BLOCK_CHAR_LIMIT = 3000


def _as_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return [str(value)]


def _is_once_arg(value: str) -> bool:
    return value.strip().lower() in {"once", "--once", "-o"}


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    clipped = text[:limit].rstrip()
    return f"{clipped}\n... [truncated {omitted} chars]"


def _compact_value(value: Any, *, limit: int = _PREVIEW_CHAR_LIMIT) -> str:
    raw = str(value or "")
    if len(raw) <= limit:
        return raw
    omitted = len(raw) - limit
    return f"{raw[:limit].rstrip()}... (+{omitted} chars)"


def _preview_list(items: List[str], *, max_items: int = 12, max_chars: int = 1200) -> str:
    if not items:
        return "-"
    normalized = [str(x).strip() for x in items if str(x).strip()]
    if not normalized:
        return "-"

    shown = normalized[:max_items]
    preview = ", ".join(shown)
    preview = _truncate_text(preview, limit=max_chars).replace("\n", " ")

    hidden = len(normalized) - len(shown)
    if hidden > 0:
        preview = f"{preview} (+{hidden} more)"
    return preview


def _json_pre(payload: Any, *, limit: int = _PRE_BLOCK_CHAR_LIMIT) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return escape_html(_truncate_text(text, limit=limit))


def _require_new_session_for_runtime_controls(user_data: MutableMapping[str, Any]) -> None:
    """Runtime controls are bound at session creation; force a fresh session next turn."""
    user_data["claude_session_id"] = None
    user_data["force_new_session"] = True


async def run_copilot_control_command(
    *,
    args: List[str],
    settings: Settings,
    user_data: MutableMapping[str, Any],
    claude_integration: ClaudeIntegration,
    user_id: int = 0,
    working_directory: Optional[Path] = None,
) -> Tuple[str, Optional[str]]:
    """Execute /copilot subcommand and return (text, parse_mode)."""
    if not args:
        return (
            "<b>Copilot controls</b>\n\n"
            "<code>/copilot status</code>\n"
            "<code>/copilot doctor</code>\n"
            "<code>/copilot sessions</code>\n"
            "<code>/copilot switch &lt;session_id&gt;</code>\n"
            "<code>/copilot delete &lt;session_id&gt;</code>\n"
            "<code>/copilot reasoning low|medium|high|xhigh [once]</code>\n"
            "<code>/copilot skills show|add-dir|rm-dir|disable|enable ...</code>\n"
            "<code>/copilot mcp raw|masked|omit</code>\n"
            "<code>/copilot fallback sdk_only|sdk_then_cli</code>\n"
            "<code>/copilot external &lt;url|off&gt;</code>",
            "HTML",
        )

    sub = args[0].strip().lower()

    if sub == "status":
        status = await claude_integration.get_copilot_status()
        snapshot = get_runtime_snapshot(settings, user_data)
        return (
            "<b>Copilot Status</b>\n\n"
            f"Provider: <code>{escape_html(snapshot['provider'])}</code>\n"
            f"Model: <code>{escape_html(snapshot['model'])}</code>\n"
            f"Fallback: <code>{escape_html(snapshot['fallback_mode'])}</code>\n"
            f"Reasoning: <code>{escape_html(str(snapshot['reasoning_effort']))}</code>\n"
            f"MCP env mode: <code>{escape_html(str(snapshot['mcp_env_value_mode']))}</code>\n\n"
            f"<pre>{_json_pre(status, limit=2600)}</pre>",
            "HTML",
        )

    if sub == "doctor":
        doctor = await claude_integration.get_copilot_doctor_report()
        return (
            "<b>Copilot Doctor</b>\n\n" f"<pre>{_json_pre(doctor, limit=3200)}</pre>",
            "HTML",
        )

    if sub == "sessions":
        sessions = await claude_integration.list_copilot_sessions()
        if not sessions:
            return "No known Copilot sessions.", None

        header = "<b>Copilot Sessions</b>\n\n"
        lines: List[str] = []
        rendered = header
        for session in sessions:
            session_id = _compact_value(session.get("session_id", ""))
            user_value = _compact_value(session.get("user_id", "-"), limit=80)
            project_path = _compact_value(session.get("project_path", ""))
            source = _compact_value(session.get("source", "-"), limit=120)
            line = (
                f"• <code>{escape_html(session_id)}</code> "
                f"(user={escape_html(user_value)}, "
                f"project=<code>{escape_html(project_path)}</code>, "
                f"source=<code>{escape_html(source)}</code>)"
            )
            candidate = f"{rendered}\n{line}" if lines else f"{rendered}{line}"
            if len(candidate) > SAFE_MESSAGE_LENGTH:
                break
            lines.append(line)
            rendered = candidate

        hidden = len(sessions) - len(lines)
        if hidden > 0:
            tail = f"\n... and {hidden} more session(s)."
            if len(rendered) + len(tail) <= SAFE_MESSAGE_LENGTH:
                rendered += tail
        return (rendered, "HTML")

    if sub == "switch":
        if len(args) < 2:
            return "Usage: /copilot switch <session_id>", None
        if not working_directory:
            return "Cannot resolve current directory for switch.", None

        session_id = args[1].strip()
        if not session_id:
            return "Session ID cannot be empty.", None

        result = claude_integration.switch_copilot_session(
            user_id=user_id,
            working_directory=working_directory,
            session_id=session_id,
        )
        user_data["claude_session_id"] = session_id
        previous = result.get("previous_session_id") or "-"
        return (
            "<b>Copilot Session Switched</b>\n\n"
            f"Previous: <code>{escape_html(str(previous))}</code>\n"
            f"Current: <code>{escape_html(session_id)}</code>",
            "HTML",
        )

    if sub == "delete":
        if len(args) < 2:
            return "Usage: /copilot delete <session_id>", None
        result = await claude_integration.delete_copilot_session(args[1].strip())
        return (f"Delete result: <pre>{_json_pre(result, limit=1800)}</pre>", "HTML")

    if sub == "reasoning":
        levels = await claude_integration.get_copilot_reasoning_levels()
        if not levels:
            levels = ["low", "medium", "high"]

        if len(args) < 2:
            current = user_data.get(
                SESSION_REASONING_KEY, settings.copilot_reasoning_default
            )
            return (
                f"Current reasoning: <code>{escape_html(str(current))}</code>\n"
                f"Supported: <code>{escape_html(', '.join(levels))}</code>\n"
                "Usage: <code>/copilot reasoning "
                f"{escape_html('|'.join(levels))} [once]</code>",
                "HTML",
            )

        value = args[1].strip().lower()
        if value not in set(levels):
            return (
                f"Reasoning must be one of: <code>{escape_html(', '.join(levels))}</code>",
                "HTML",
            )

        once = len(args) > 2 and _is_once_arg(args[2])
        if once:
            user_data[ONCE_REASONING_KEY] = value
        else:
            user_data[SESSION_REASONING_KEY] = value
            _require_new_session_for_runtime_controls(user_data)

        claude_integration.update_copilot_runtime_controls(reasoning_effort=value)
        return (
            (
                f"Reasoning one-shot override: <code>{escape_html(value)}</code>"
                if once
                else f"Reasoning set to <code>{escape_html(value)}</code>"
            ),
            "HTML",
        )

    if sub == "skills":
        action = args[1].strip().lower() if len(args) > 1 else "show"
        if "|" in action:
            return "Choose one action only: show, add-dir, rm-dir, disable, or enable.", None
        dirs = _as_string_list(
            user_data.get(SESSION_SKILL_DIRS_KEY, settings.copilot_skill_directories)
        )
        disabled = _as_string_list(
            user_data.get(SESSION_DISABLED_SKILLS_KEY, settings.copilot_disabled_skills)
        )

        if action == "show":
            return (
                "<b>Copilot skills</b>\n\n"
                f"Directories: <code>{escape_html(_preview_list(dirs))}</code>\n"
                f"Disabled: <code>{escape_html(_preview_list(disabled))}</code>",
                "HTML",
            )

        if len(args) < 3:
            return "Usage: /copilot skills add-dir|rm-dir|disable|enable <value>", None

        value = " ".join(args[2:]).strip()
        if action == "add-dir":
            if value in dirs:
                return "Directory already exists.", None
            dirs.append(value)
        elif action == "rm-dir":
            if value not in dirs:
                return "Directory not found.", None
            dirs = [d for d in dirs if d != value]
        elif action == "disable":
            if value in disabled:
                return "Skill is already disabled.", None
            disabled.append(value)
        elif action == "enable":
            if value not in disabled:
                return "Skill is not disabled.", None
            disabled = [s for s in disabled if s != value]
        else:
            return "Unknown skills action.", None

        user_data[SESSION_SKILL_DIRS_KEY] = dirs
        user_data[SESSION_DISABLED_SKILLS_KEY] = disabled
        claude_integration.update_copilot_runtime_controls(
            skill_directories=dirs,
            disabled_skills=disabled,
        )
        _require_new_session_for_runtime_controls(user_data)
        return "Skills runtime controls updated. New settings apply on next request.", None

    if sub == "mcp":
        if len(args) < 2:
            current = user_data.get(
                SESSION_MCP_ENV_MODE_KEY, settings.mcp_env_value_mode
            )
            return (
                f"MCP env mode: <code>{escape_html(str(current))}</code>\n"
                "Usage: <code>/copilot mcp raw|masked|omit</code>",
                "HTML",
            )

        mode = args[1].strip().lower()
        if mode not in {"raw", "masked", "omit"}:
            return "Mode must be raw, masked, or omit.", None

        user_data[SESSION_MCP_ENV_MODE_KEY] = mode
        claude_integration.update_copilot_runtime_controls(mcp_env_value_mode=mode)
        _require_new_session_for_runtime_controls(user_data)
        return f"MCP env mode set to <code>{escape_html(mode)}</code>", "HTML"

    if sub == "external":
        if len(args) < 2:
            current = user_data.get(
                SESSION_EXTERNAL_SERVER_KEY, settings.copilot_external_cli_server
            )
            return (
                "Usage: <code>/copilot external &lt;url|off&gt;</code>\n"
                f"Current: <code>{escape_html(str(current or 'off'))}</code>",
                "HTML",
            )

        endpoint = args[1].strip()
        if endpoint.lower() == "off":
            endpoint = ""

        user_data[SESSION_EXTERNAL_SERVER_KEY] = endpoint or None
        claude_integration.update_copilot_runtime_controls(
            external_cli_server=(endpoint or None),
            external_cli_server_set=True,
        )
        _require_new_session_for_runtime_controls(user_data)
        return (
            f"External CLI server: <code>{escape_html(endpoint or 'off')}</code>",
            "HTML",
        )

    if sub == "fallback":
        if len(args) < 2:
            return (
                f"Current fallback: <code>{escape_html(settings.copilot_fallback_mode)}</code>\n"
                "Usage: <code>/copilot fallback sdk_only|sdk_then_cli</code>",
                "HTML",
            )

        mode = args[1].strip().lower()
        if mode not in {"sdk_only", "sdk_then_cli"}:
            return "Fallback must be sdk_only or sdk_then_cli.", None

        settings.copilot_fallback_mode = mode
        return f"Fallback mode set to <code>{escape_html(mode)}</code>", "HTML"

    return "Unknown subcommand. Use /copilot for help.", None
