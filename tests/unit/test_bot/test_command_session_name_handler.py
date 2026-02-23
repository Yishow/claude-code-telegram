"""Tests for /session_name command handler."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import src.bot.handlers.command as command_module
from src.config import create_test_config


def _make_update(user_id: int = 42) -> MagicMock:
    update = MagicMock()
    update.effective_user.id = user_id
    update.message.reply_text = AsyncMock()
    return update


def _make_context(tmp_path: Path, args: list[str]) -> MagicMock:
    settings = create_test_config(approved_directory=str(tmp_path))
    context = MagicMock()
    context.args = args
    context.user_data = {
        "claude_session_id": "session-abc",
        "current_directory": tmp_path,
    }
    context.bot_data = {
        "settings": settings,
        "claude_integration": MagicMock(),
    }
    return context


async def test_session_name_sets_display_name(tmp_path: Path):
    """Setting /session_name should persist the provided name."""
    update = _make_update()
    context = _make_context(tmp_path, ["My", "Session"])
    context.bot_data["claude_integration"].get_session_info = AsyncMock(
        return_value={"session_id": "session-abc", "display_name": None}
    )
    context.bot_data["claude_integration"].set_session_display_name = AsyncMock(
        return_value={
            "session_id": "session-abc",
            "display_name": "My Session",
        }
    )

    await command_module.session_name_command(update, context)

    context.bot_data["claude_integration"].set_session_display_name.assert_awaited_once_with(
        session_id="session-abc",
        user_id=42,
        display_name="My Session",
    )
    update.message.reply_text.assert_called_once()
    assert "Session name updated" in update.message.reply_text.call_args.args[0]


async def test_session_name_reset_to_unnamed(tmp_path: Path):
    """Using keyword 未命名 should clear display name."""
    update = _make_update()
    context = _make_context(tmp_path, ["未命名"])
    context.bot_data["claude_integration"].get_session_info = AsyncMock(
        return_value={"session_id": "session-abc", "display_name": "Old"}
    )
    context.bot_data["claude_integration"].set_session_display_name = AsyncMock(
        return_value={
            "session_id": "session-abc",
            "display_name": None,
        }
    )

    await command_module.session_name_command(update, context)

    context.bot_data["claude_integration"].set_session_display_name.assert_awaited_once_with(
        session_id="session-abc",
        user_id=42,
        display_name=None,
    )
    update.message.reply_text.assert_called_once()
    assert "未命名" in update.message.reply_text.call_args.args[0]


async def test_session_name_rejects_over_100_chars(tmp_path: Path):
    """Name longer than 100 chars should be rejected."""
    update = _make_update()
    context = _make_context(tmp_path, ["x" * 101])
    context.bot_data["claude_integration"].get_session_info = AsyncMock(
        return_value={"session_id": "session-abc", "display_name": None}
    )
    context.bot_data["claude_integration"].set_session_display_name = AsyncMock()

    await command_module.session_name_command(update, context)

    context.bot_data["claude_integration"].set_session_display_name.assert_not_awaited()
    update.message.reply_text.assert_called_once()
    assert "Name too long" in update.message.reply_text.call_args.args[0]


async def test_session_name_empty_string_resets_to_unnamed(tmp_path: Path):
    """Quoted empty string should reset display name to unnamed."""
    update = _make_update()
    context = _make_context(tmp_path, ['""'])
    context.bot_data["claude_integration"].get_session_info = AsyncMock(
        return_value={"session_id": "session-abc", "display_name": "Old Name"}
    )
    context.bot_data["claude_integration"].set_session_display_name = AsyncMock(
        return_value={
            "session_id": "session-abc",
            "display_name": None,
        }
    )

    await command_module.session_name_command(update, context)

    context.bot_data["claude_integration"].set_session_display_name.assert_awaited_once_with(
        session_id="session-abc",
        user_id=42,
        display_name=None,
    )
    update.message.reply_text.assert_called_once()
    assert "未命名" in update.message.reply_text.call_args.args[0]
