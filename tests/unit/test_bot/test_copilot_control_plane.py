"""Tests for shared Copilot control-plane command handler."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.bot.copilot_control_plane import run_copilot_control_command
from src.bot.copilot_runtime import (
    ONCE_REASONING_KEY,
    SESSION_EXTERNAL_SERVER_KEY,
    SESSION_REASONING_KEY,
)
from src.config import create_test_config


def _make_integration() -> MagicMock:
    integration = MagicMock()
    integration.get_copilot_status = AsyncMock(return_value={"health": "healthy"})
    integration.get_copilot_doctor_report = AsyncMock(
        return_value={"health": "healthy", "warnings": []}
    )
    integration.list_copilot_sessions = AsyncMock(return_value=[])
    integration.delete_copilot_session = AsyncMock(
        return_value={"session_id": "sid", "removed_local": True, "removed_sdk": True}
    )
    integration.get_copilot_reasoning_levels = AsyncMock(
        return_value=["low", "medium", "high", "xhigh"]
    )
    integration.update_copilot_runtime_controls = MagicMock()
    integration.switch_copilot_session = MagicMock(
        return_value={
            "previous_session_id": "old-sid",
            "current_session_id": "new-sid",
        }
    )
    return integration


async def test_help_contains_switch_and_doctor(tmp_path: Path):
    settings = create_test_config(approved_directory=str(tmp_path))
    integration = _make_integration()

    text, parse_mode = await run_copilot_control_command(
        args=[],
        settings=settings,
        user_data={},
        claude_integration=integration,
    )

    assert parse_mode == "HTML"
    assert "/copilot doctor" in text
    assert "/copilot switch" in text
    assert "xhigh" in text


async def test_reasoning_once_override_uses_dynamic_levels(tmp_path: Path):
    settings = create_test_config(approved_directory=str(tmp_path))
    integration = _make_integration()
    user_data = {}

    text, parse_mode = await run_copilot_control_command(
        args=["reasoning", "xhigh", "once"],
        settings=settings,
        user_data=user_data,
        claude_integration=integration,
    )

    assert parse_mode == "HTML"
    assert "one-shot" in text
    assert user_data[ONCE_REASONING_KEY] == "xhigh"
    integration.update_copilot_runtime_controls.assert_called_once_with(
        reasoning_effort="xhigh"
    )


async def test_external_off_clears_runtime_server(tmp_path: Path):
    settings = create_test_config(approved_directory=str(tmp_path))
    integration = _make_integration()
    user_data = {SESSION_EXTERNAL_SERVER_KEY: "https://copilot.internal"}

    text, parse_mode = await run_copilot_control_command(
        args=["external", "off"],
        settings=settings,
        user_data=user_data,
        claude_integration=integration,
    )

    assert parse_mode == "HTML"
    assert "off" in text
    assert user_data[SESSION_EXTERNAL_SERVER_KEY] is None
    integration.update_copilot_runtime_controls.assert_called_once_with(
        external_cli_server=None,
        external_cli_server_set=True,
    )


async def test_switch_sets_user_session_and_pins_mapping(tmp_path: Path):
    settings = create_test_config(approved_directory=str(tmp_path))
    integration = _make_integration()
    user_data = {SESSION_REASONING_KEY: "medium"}

    text, parse_mode = await run_copilot_control_command(
        args=["switch", "session-123"],
        settings=settings,
        user_data=user_data,
        claude_integration=integration,
        user_id=321,
        working_directory=tmp_path,
    )

    assert parse_mode == "HTML"
    assert "Session Switched" in text
    assert user_data["claude_session_id"] == "session-123"
    integration.switch_copilot_session.assert_called_once_with(
        user_id=321,
        working_directory=tmp_path,
        session_id="session-123",
    )


async def test_switch_requires_working_directory(tmp_path: Path):
    settings = create_test_config(approved_directory=str(tmp_path))
    integration = _make_integration()

    text, parse_mode = await run_copilot_control_command(
        args=["switch", "session-123"],
        settings=settings,
        user_data={},
        claude_integration=integration,
        user_id=321,
        working_directory=None,
    )

    assert parse_mode is None
    assert "Cannot resolve current directory" in text
    integration.switch_copilot_session.assert_not_called()
