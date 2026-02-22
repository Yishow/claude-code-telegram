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
    app.initialize = AsyncMock(return_value=None)
    app.start = AsyncMock(return_value=None)
    app.updater = MagicMock()
    app.updater.start_webhook = AsyncMock(return_value=None)
    bot.app = app
    bot.initialize = AsyncMock(return_value=None)

    async def stop_after_first_sleep(_: float) -> None:
        bot.is_running = False

    sleep_mock = AsyncMock(side_effect=stop_after_first_sleep)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.bot.core.asyncio.sleep", sleep_mock)
        await bot.start()

    app.initialize.assert_awaited_once()
    app.start.assert_awaited_once()
    app.updater.start_webhook.assert_awaited_once()
    kwargs = app.updater.start_webhook.call_args.kwargs
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
    app.initialize = AsyncMock(return_value=None)
    app.start = AsyncMock(return_value=None)
    app.updater = MagicMock()
    app.updater.start_webhook = AsyncMock(return_value=None)
    bot.app = app
    bot.initialize = AsyncMock(return_value=None)

    async def stop_after_first_sleep(_: float) -> None:
        bot.is_running = False

    sleep_mock = AsyncMock(side_effect=stop_after_first_sleep)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.bot.core.asyncio.sleep", sleep_mock)
        await bot.start()

    kwargs = app.updater.start_webhook.call_args.kwargs
    assert "secret_token" not in kwargs
