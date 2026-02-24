"""Tests for provider-aware /model behavior."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.bot.handlers import command
from src.bot.orchestrator import MessageOrchestrator
from src.config import create_test_config


async def test_model_command_lists_claude_models_when_provider_is_claude(
    tmp_path: Path,
):
    """Classic /model should list Claude models when provider is claude."""
    settings = create_test_config(
        approved_directory=str(tmp_path),
        claude_model="claude-sonnet-4-20250514",
    )

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []
    context.user_data = {"provider": "claude"}
    context.bot_data = {"settings": settings}

    await command.model_command(update, context)

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "Available models" in text
    assert "claude-sonnet-4-20250514" in text


async def test_model_command_switches_claude_model_when_provider_is_claude(
    tmp_path: Path,
):
    """Classic /model should set claude model under Claude provider."""
    settings = create_test_config(approved_directory=str(tmp_path))

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = ["claude-3-5-haiku-20241022"]
    context.user_data = {"provider": "claude"}
    context.bot_data = {"settings": settings}

    await command.model_command(update, context)

    assert context.user_data["claude_model"] == "claude-3-5-haiku-20241022"
    assert "copilot_model" not in context.user_data


async def test_model_command_sets_copilot_model_when_provider_is_copilot(
    tmp_path: Path,
):
    """Classic /model should keep existing Copilot behavior for copilot provider."""
    settings = create_test_config(approved_directory=str(tmp_path))

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = ["gpt-5-mini"]
    context.user_data = {"provider": "copilot"}
    context.bot_data = {"settings": settings}

    await command.model_command(update, context)

    assert context.user_data["copilot_model"] == "gpt-5-mini"


async def test_agentic_model_shows_claude_model_when_provider_is_claude(
    tmp_path: Path,
):
    """Agentic /model should expose Claude model options when provider is claude."""
    settings = create_test_config(
        approved_directory=str(tmp_path),
        agentic_mode=True,
        claude_model="claude-sonnet-4-20250514",
    )
    orchestrator = MessageOrchestrator(settings, {})

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []
    context.user_data = {"provider": "claude"}
    context.bot_data = {}

    await orchestrator.agentic_model(update, context)

    update.message.reply_text.assert_awaited_once()
    text = update.message.reply_text.await_args.args[0]
    assert "Available models" in text
    assert "claude-sonnet-4-20250514" in text
    keyboard = update.message.reply_text.await_args.kwargs["reply_markup"]
    callbacks = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert any(cb.startswith("model:claude-") for cb in callbacks)


async def test_agentic_model_callback_sets_claude_model_when_provider_is_claude(
    tmp_path: Path,
):
    """Inline model picker callback should set claude model under Claude provider."""
    settings = create_test_config(approved_directory=str(tmp_path), agentic_mode=True)
    orchestrator = MessageOrchestrator(settings, {})

    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = "model:claude-sonnet-4-20250514"

    update = MagicMock()
    update.callback_query = query

    context = MagicMock()
    context.user_data = {"provider": "claude"}
    context.bot_data = {"audit_logger": None}

    await orchestrator._model_callback(update, context)

    assert context.user_data["claude_model"] == "claude-sonnet-4-20250514"
    query.edit_message_text.assert_awaited_once()
