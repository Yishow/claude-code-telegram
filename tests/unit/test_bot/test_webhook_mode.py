"""Tests for webhook startup behavior in ClaudeCodeBot."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.core import ClaudeCodeBot
from src.config import create_test_config


@pytest.mark.asyncio
async def test_start_webhook_passes_secret_token():
    settings = create_test_config(
        webhook_url="https://bot.example.com/webhook",
        webhook_path="/webhook",
        webhook_port=8443,
        telegram_webhook_secret_token="secret-token",
    )
    bot = ClaudeCodeBot(settings, {})

    app = MagicMock()
    app.run_webhook = AsyncMock(return_value=None)
    bot.app = app
    bot.initialize = AsyncMock(return_value=None)

    await bot.start()

    app.run_webhook.assert_awaited_once()
    kwargs = app.run_webhook.call_args.kwargs
    assert kwargs["webhook_url"] == "https://bot.example.com/webhook"
    assert kwargs["url_path"] == "/webhook"
    assert kwargs["port"] == 8443
    assert kwargs["secret_token"] == "secret-token"


@pytest.mark.asyncio
async def test_start_webhook_without_secret_token():
    settings = create_test_config(
        webhook_url="https://bot.example.com/webhook",
        webhook_path="/webhook",
        webhook_port=8443,
        telegram_webhook_secret_token=None,
    )
    bot = ClaudeCodeBot(settings, {})

    app = MagicMock()
    app.run_webhook = AsyncMock(return_value=None)
    bot.app = app
    bot.initialize = AsyncMock(return_value=None)

    await bot.start()

    kwargs = app.run_webhook.call_args.kwargs
    assert "secret_token" not in kwargs
