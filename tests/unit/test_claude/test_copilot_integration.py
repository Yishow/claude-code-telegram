"""Tests for CopilotProcessManager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from src.claude.copilot_integration import (
    COPILOT_MODELS,
    CopilotProcessManager,
    CopilotStreamUpdate,
)
from src.claude.exceptions import ClaudeProcessError, ClaudeTimeoutError
from src.config.settings import Settings


@pytest.fixture
def config(tmp_path):
    return Settings(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=tmp_path,
    )


@pytest.fixture
def manager(config):
    return CopilotProcessManager(config)


# ── _build_command ────────────────────────────────────────────────────────────


class TestBuildCommand:
    def test_new_session(self, manager):
        cmd = manager._build_command("hello", None, False, "gpt-5-mini")
        assert cmd[0].endswith("copilot")
        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "hello"
        assert "--allow-all" in cmd
        assert "-s" in cmd
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-5-mini"
        assert "--resume" not in cmd

    def test_continue_session_with_id(self, manager):
        cmd = manager._build_command("next", "abc-123", True, "gpt-5-mini")
        assert "--resume" in cmd
        assert cmd[cmd.index("--resume") + 1] == "abc-123"
        assert "-p" in cmd

    def test_continue_session_without_id_skips_resume(self, manager):
        cmd = manager._build_command("next", None, True, "gpt-5-mini")
        assert "--resume" not in cmd

    def test_stream_off_not_in_command(self, manager):
        cmd = manager._build_command("hello", None, False, "gpt-5-mini")
        assert "--stream" not in cmd

    def test_custom_binary_path(self, config, tmp_path):
        config.copilot_binary_path = "/usr/local/bin/copilot"
        m = CopilotProcessManager(config)
        cmd = m._build_command("hello", None, False, "gpt-5-mini")
        assert cmd[0] == "/usr/local/bin/copilot"

    def test_gemini_model(self, manager):
        cmd = manager._build_command("hello", None, False, "gemini-3-pro-preview")
        assert cmd[cmd.index("--model") + 1] == "gemini-3-pro-preview"


# ── _find_session_id_for_directory ────────────────────────────────────────────


class TestFindSessionId:
    def test_returns_none_when_no_session_dir(self, manager, tmp_path):
        with patch(
            "src.claude.copilot_integration.COPILOT_SESSION_DIR",
            tmp_path / "nonexistent",
        ):
            result = manager._find_session_id_for_directory(tmp_path)
        assert result is None

    def test_finds_matching_session(self, manager, tmp_path):
        session_dir = tmp_path / "sessions"
        sid = "abc-123"
        sess = session_dir / sid
        sess.mkdir(parents=True)
        (sess / "workspace.yaml").write_text(
            yaml.dump(
                {
                    "id": sid,
                    "cwd": str(tmp_path / "project"),
                    "updated_at": "2026-02-20T10:00:00.000Z",
                }
            )
        )
        with patch("src.claude.copilot_integration.COPILOT_SESSION_DIR", session_dir):
            result = manager._find_session_id_for_directory(tmp_path / "project")
        assert result == sid

    def test_returns_most_recent_session(self, manager, tmp_path):
        session_dir = tmp_path / "sessions"
        project = tmp_path / "project"
        for sid, ts in [
            ("old-id", "2026-02-19T10:00:00.000Z"),
            ("new-id", "2026-02-20T10:00:00.000Z"),
        ]:
            sess = session_dir / sid
            sess.mkdir(parents=True)
            (sess / "workspace.yaml").write_text(
                yaml.dump({"id": sid, "cwd": str(project), "updated_at": ts})
            )
        with patch("src.claude.copilot_integration.COPILOT_SESSION_DIR", session_dir):
            result = manager._find_session_id_for_directory(project)
        assert result == "new-id"

    def test_ignores_different_directory(self, manager, tmp_path):
        session_dir = tmp_path / "sessions"
        sess = session_dir / "other-id"
        sess.mkdir(parents=True)
        (sess / "workspace.yaml").write_text(
            yaml.dump(
                {
                    "id": "other-id",
                    "cwd": "/some/other/path",
                    "updated_at": "2026-02-20T10:00:00.000Z",
                }
            )
        )
        with patch("src.claude.copilot_integration.COPILOT_SESSION_DIR", session_dir):
            result = manager._find_session_id_for_directory(tmp_path / "myproject")
        assert result is None


# ── execute_command ───────────────────────────────────────────────────────────


def _mock_process(stdout: bytes = b"Hello!", stderr: bytes = b"", returncode: int = 0):
    process = MagicMock()
    process.communicate = AsyncMock(return_value=(stdout, stderr))
    process.returncode = returncode
    process.kill = MagicMock()
    process.wait = AsyncMock()
    return process


class TestExecuteCommand:
    async def test_successful_execution(self, manager, tmp_path):
        process = _mock_process(stdout=b"Hello!")

        with patch("asyncio.create_subprocess_exec", return_value=process):
            with patch.object(
                manager, "_find_session_id_for_directory", return_value="sess-1"
            ):
                response = await manager.execute_command(
                    prompt="say hello",
                    working_directory=tmp_path,
                )

        assert response.content == "Hello!"
        assert response.is_error is False
        assert response.session_id == "sess-1"

    async def test_uses_correct_flags(self, manager, tmp_path):
        process = _mock_process(stdout=b"ok")
        captured_cmd = []

        async def mock_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return process

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch.object(
                manager, "_find_session_id_for_directory", return_value=""
            ):
                await manager.execute_command(prompt="test", working_directory=tmp_path)

        assert "-p" in captured_cmd
        assert "-s" in captured_cmd
        assert "--allow-all" in captured_cmd
        assert "--stream" not in captured_cmd

    async def test_timeout_raises_error(self, manager, tmp_path):
        process = MagicMock()
        process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        process.kill = MagicMock()
        process.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=process):
            with pytest.raises(ClaudeTimeoutError):
                await manager.execute_command(
                    prompt="test",
                    working_directory=tmp_path,
                )

    async def test_nonzero_exit_with_no_output_raises_error(self, manager, tmp_path):
        process = _mock_process(stdout=b"", stderr=b"auth error", returncode=1)

        with patch("asyncio.create_subprocess_exec", return_value=process):
            with pytest.raises(ClaudeProcessError, match="auth error"):
                await manager.execute_command(
                    prompt="test",
                    working_directory=tmp_path,
                )

    async def test_stream_callback_called(self, manager, tmp_path):
        process = _mock_process(stdout=b"Result text")
        updates = []

        async def callback(update: CopilotStreamUpdate):
            updates.append(update)

        with patch("asyncio.create_subprocess_exec", return_value=process):
            with patch.object(
                manager, "_find_session_id_for_directory", return_value=""
            ):
                await manager.execute_command(
                    prompt="test",
                    working_directory=tmp_path,
                    stream_callback=callback,
                )

        # Allow task to run
        await asyncio.sleep(0)
        assert any(u.type == "result" and u.content == "Result text" for u in updates)

    async def test_continue_session_resolves_from_filesystem(self, manager, tmp_path):
        process = _mock_process(stdout=b"continued")
        captured_cmd = []

        async def mock_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return process

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            with patch.object(
                manager, "_find_session_id_for_directory", return_value="fs-session-id"
            ):
                await manager.execute_command(
                    prompt="continue",
                    working_directory=tmp_path,
                    continue_session=True,
                    session_id=None,  # No explicit ID
                )

        assert "--resume" in captured_cmd
        assert captured_cmd[captured_cmd.index("--resume") + 1] == "fs-session-id"


# ── model list ────────────────────────────────────────────────────────────────


class TestModelList:
    def test_gemini_in_model_list(self):
        assert "gemini-3-pro-preview" in COPILOT_MODELS

    def test_claude_models_in_list(self):
        assert "claude-sonnet-4.5" in COPILOT_MODELS
        assert "claude-opus-4.6" in COPILOT_MODELS

    def test_gpt_models_in_list(self):
        assert "gpt-5-mini" in COPILOT_MODELS
