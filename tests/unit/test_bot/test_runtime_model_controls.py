"""Tests for provider-specific runtime model controls."""

from pathlib import Path

from src.bot.copilot_runtime import (
    ONCE_CLAUDE_MODEL_KEY,
    SESSION_CLAUDE_MODEL_KEY,
    SESSION_PROVIDER_KEY,
    consume_request_controls,
    get_runtime_snapshot,
)
from src.config import create_test_config


def test_runtime_snapshot_uses_claude_model_when_provider_is_claude(tmp_path: Path):
    settings = create_test_config(
        approved_directory=str(tmp_path),
        claude_model="claude-sonnet-4-6",
    )
    user_data = {
        SESSION_PROVIDER_KEY: "claude",
        SESSION_CLAUDE_MODEL_KEY: "claude-haiku-3-5",
    }

    snapshot = get_runtime_snapshot(settings, user_data)

    assert snapshot["provider"] == "claude"
    assert snapshot["model"] == "claude-haiku-3-5"


def test_consume_request_controls_applies_and_consumes_one_shot_claude_model(
    tmp_path: Path,
):
    settings = create_test_config(
        approved_directory=str(tmp_path),
        claude_model="claude-sonnet-4-6",
    )
    user_data = {
        SESSION_PROVIDER_KEY: "claude",
        ONCE_CLAUDE_MODEL_KEY: "claude-haiku-3-5",
    }

    controls = consume_request_controls(settings, user_data)

    assert controls["provider"] == "claude"
    assert controls["claude_model"] == "claude-haiku-3-5"
    assert ONCE_CLAUDE_MODEL_KEY not in user_data
