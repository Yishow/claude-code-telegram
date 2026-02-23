"""Tests for /copilot command handler wiring."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import src.bot.handlers.command as command_module
from src.config import create_test_config


async def test_copilot_command_uses_effective_message(monkeypatch, tmp_path: Path):
    settings = create_test_config(approved_directory=str(tmp_path))
    run_mock = AsyncMock(return_value=("ok", "HTML"))
    monkeypatch.setattr(command_module, "run_copilot_control_command", run_mock)

    update = MagicMock()
    update.message = None
    update.effective_user.id = 42
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = ["status"]
    context.user_data = {}
    context.bot_data = {
        "settings": settings,
        "claude_integration": MagicMock(),
    }

    await command_module.copilot_command(update, context)

    update.effective_message.reply_text.assert_called_once_with("ok", parse_mode="HTML")
    run_mock.assert_awaited_once()
