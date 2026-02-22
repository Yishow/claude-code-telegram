"""Tests for Telegram memory command and callbacks."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.callback import handle_memory_callback
from src.bot.handlers.command import memory_command
from src.storage.models import MemoryRuntimeSettingsModel


def _runtime_model(**overrides) -> MemoryRuntimeSettingsModel:
    base = {
        "scope_key": "1:2:0",
        "user_id": 1,
        "chat_id": 2,
        "message_thread_id": 0,
        "memory_system_plus_enabled": True,
        "memory_hooks_enabled": True,
        "memory_pre_hook_enabled": True,
        "memory_post_hook_enabled": True,
        "memory_ai_enhancement_enabled": True,
        "memory_ai_extractor_enabled": True,
        "memory_ai_reranker_enabled": True,
        "memory_ai_conflict_detector_enabled": True,
        "memory_ai_periodic_review_enabled": True,
        "memory_profile": "balanced",
        "memory_ai_model": "gpt-5-mini",
        "memory_ai_timeout_seconds": 20,
        "memory_recall_limit": 20,
        "memory_injection_token_budget": 800,
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return MemoryRuntimeSettingsModel(**base)


@pytest.mark.asyncio
async def test_memory_command_status_panel():
    """`/memory` should render status panel with keyboard."""
    memory_service = AsyncMock()
    runtime = _runtime_model()
    memory_service.get_runtime_settings = AsyncMock(return_value=runtime)
    memory_service.get_metrics_summary = AsyncMock(
        return_value={"total_events": 4, "fallback_events": 1}
    )

    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = 2
    update.effective_message.message_thread_id = 0
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = []
    context.bot_data = {"memory_service": memory_service, "audit_logger": None}

    await memory_command(update, context)

    update.message.reply_text.assert_called_once()
    kwargs = update.message.reply_text.call_args.kwargs
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"] is not None
    memory_service.get_runtime_settings.assert_called_once()


@pytest.mark.asyncio
async def test_memory_command_toggle_reranker():
    """`/memory toggle reranker` should flip module setting."""
    memory_service = AsyncMock()
    runtime = _runtime_model()
    toggled = _runtime_model(memory_ai_reranker_enabled=False)
    memory_service.get_runtime_settings = AsyncMock(return_value=runtime)
    memory_service.toggle_runtime_setting = AsyncMock(return_value=toggled)
    memory_service.get_metrics_summary = AsyncMock(
        return_value={"total_events": 1, "fallback_events": 0}
    )

    update = MagicMock()
    update.effective_user.id = 1
    update.effective_chat.id = 2
    update.effective_message.message_thread_id = 0
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = ["toggle", "reranker"]
    context.bot_data = {"memory_service": memory_service, "audit_logger": None}

    await memory_command(update, context)

    memory_service.toggle_runtime_setting.assert_called_once()
    call_kwargs = memory_service.toggle_runtime_setting.call_args.kwargs
    assert call_kwargs["field"] == "memory_ai_reranker_enabled"


@pytest.mark.asyncio
async def test_memory_callback_profile_switch_uses_scope():
    """Callback profile switch should persist with chat/thread scope."""
    memory_service = AsyncMock()
    runtime = _runtime_model()
    switched = _runtime_model(memory_profile="quality", message_thread_id=99)
    memory_service.get_runtime_settings = AsyncMock(return_value=runtime)
    memory_service.set_runtime_profile = AsyncMock(return_value=switched)
    memory_service.get_metrics_summary = AsyncMock(
        return_value={"total_events": 10, "fallback_events": 2}
    )

    query = MagicMock()
    query.from_user.id = 123
    query.message.chat.id = -555
    query.message.message_thread_id = 99
    query.edit_message_text = AsyncMock()
    query.answer = AsyncMock()

    context = MagicMock()
    context.bot_data = {"memory_service": memory_service}

    await handle_memory_callback(query, "profile:quality", context)

    memory_service.set_runtime_profile.assert_called_once()
    kwargs = memory_service.set_runtime_profile.call_args.kwargs
    assert kwargs["chat_id"] == -555
    assert kwargs["message_thread_id"] == 99
    query.edit_message_text.assert_called_once()
