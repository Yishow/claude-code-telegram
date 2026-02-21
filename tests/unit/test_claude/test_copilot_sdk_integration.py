"""Tests for CopilotSDKManager session lifecycle."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from copilot import CopilotClient as _CC  # noqa: F401
except ImportError:
    pytest.skip("github-copilot-sdk not installed", allow_module_level=True)

from src.claude.copilot_sdk_integration import CopilotSDKManager  # noqa: E402
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
    return CopilotSDKManager(config)


def _make_session(session_id: str = "sess-abc", content: str = "Hello!") -> MagicMock:
    """Build a mock CopilotSession."""
    event_data = MagicMock()
    event_data.content = content

    result_event = MagicMock()
    result_event.data = event_data

    session = MagicMock()
    session.session_id = session_id
    session.send_and_wait = AsyncMock(return_value=result_event)
    session.on = MagicMock(return_value=lambda: None)
    return session


def _make_client(session: MagicMock) -> MagicMock:
    client = MagicMock()
    client.start = AsyncMock()
    client.stop = AsyncMock()
    client.create_session = AsyncMock(return_value=session)
    client.resume_session = AsyncMock(return_value=session)
    return client


# ── basic execution ───────────────────────────────────────────────────────────


class TestExecuteCommand:
    async def test_new_session_returns_content(self, manager, tmp_path):
        session = _make_session("sid-1", "Hi there!")
        client = _make_client(session)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            response = await manager.execute_command(
                prompt="hello",
                working_directory=tmp_path,
                user_id=1,
            )

        assert response.content == "Hi there!"
        assert response.session_id == "sid-1"
        assert response.is_error is False

    async def test_session_id_stored_after_execution(self, manager, tmp_path):
        session = _make_session("stored-sid")
        client = _make_client(session)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            await manager.execute_command(
                prompt="hello", working_directory=tmp_path, user_id=42
            )

        key = manager._session_key(42, tmp_path)
        assert manager._session_map[key] == "stored-sid"

    async def test_timeout_raises_error(self, manager, tmp_path):
        session = _make_session()
        session.send_and_wait = AsyncMock(side_effect=asyncio.TimeoutError())
        client = _make_client(session)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            with pytest.raises(ClaudeTimeoutError):
                await manager.execute_command(
                    prompt="slow",
                    working_directory=tmp_path,
                    user_id=1,
                )

    async def test_sdk_error_raises_process_error(self, manager, tmp_path):
        session = _make_session()
        session.send_and_wait = AsyncMock(side_effect=RuntimeError("rpc failed"))
        client = _make_client(session)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            with pytest.raises(ClaudeProcessError, match="rpc failed"):
                await manager.execute_command(
                    prompt="broken",
                    working_directory=tmp_path,
                    user_id=1,
                )


# ── session lifecycle ─────────────────────────────────────────────────────────


class TestSessionLifecycle:
    async def test_second_call_resumes_session(self, manager, tmp_path):
        session = _make_session("session-xyz", "remembered!")
        client = _make_client(session)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            # First call — creates new session
            await manager.execute_command(
                prompt="remember X", working_directory=tmp_path, user_id=5
            )
            # Second call — should resume
            await manager.execute_command(
                prompt="what did I say?",
                working_directory=tmp_path,
                user_id=5,
                continue_session=True,
            )

        client.create_session.assert_called_once()
        client.resume_session.assert_called_once()
        call_args = client.resume_session.call_args
        assert call_args[0][0] == "session-xyz"

    async def test_force_new_does_not_resume(self, manager, tmp_path):
        session = _make_session("old-sid")
        client = _make_client(session)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            # Seed a stored session
            manager._session_map[manager._session_key(7, tmp_path)] = "old-sid"
            # continue_session=False means fresh start
            await manager.execute_command(
                prompt="fresh start",
                working_directory=tmp_path,
                user_id=7,
                continue_session=False,
            )

        client.resume_session.assert_not_called()
        client.create_session.assert_called_once()

    async def test_resume_failure_falls_back_to_new_session(self, manager, tmp_path):
        session_new = _make_session("new-sid", "fresh response")
        client = MagicMock()
        client.start = AsyncMock()
        client.stop = AsyncMock()
        client.resume_session = AsyncMock(side_effect=RuntimeError("session expired"))
        client.create_session = AsyncMock(return_value=session_new)

        manager._session_map[manager._session_key(9, tmp_path)] = "expired-sid"

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            response = await manager.execute_command(
                prompt="hello again",
                working_directory=tmp_path,
                user_id=9,
                continue_session=True,
                session_id="expired-sid",
            )

        assert response.content == "fresh response"
        client.create_session.assert_called_once()

    async def test_forget_session_removes_stored_id(self, manager, tmp_path):
        manager._session_map[manager._session_key(3, tmp_path)] = "to-forget"
        manager.forget_session(3, tmp_path)
        assert manager._session_key(3, tmp_path) not in manager._session_map

    async def test_different_users_get_separate_sessions(self, manager, tmp_path):
        sessions = {
            "u1": _make_session("sid-user1", "user1 response"),
            "u2": _make_session("sid-user2", "user2 response"),
        }
        call_count = 0

        async def create_session_side_effect(config=None):
            nonlocal call_count
            call_count += 1
            return sessions[f"u{call_count}"]

        client = MagicMock()
        client.start = AsyncMock()
        client.create_session = AsyncMock(side_effect=create_session_side_effect)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            r1 = await manager.execute_command(
                prompt="hi", working_directory=tmp_path, user_id=1
            )
            r2 = await manager.execute_command(
                prompt="hi", working_directory=tmp_path, user_id=2
            )

        assert r1.session_id == "sid-user1"
        assert r2.session_id == "sid-user2"
        assert manager._session_map[manager._session_key(1, tmp_path)] == "sid-user1"
        assert manager._session_map[manager._session_key(2, tmp_path)] == "sid-user2"


# ── client lifecycle ──────────────────────────────────────────────────────────


class TestClientLifecycle:
    async def test_client_started_once(self, manager, tmp_path):
        session = _make_session()
        client = _make_client(session)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            await manager.execute_command(
                prompt="a", working_directory=tmp_path, user_id=1
            )
            await manager.execute_command(
                prompt="b", working_directory=tmp_path, user_id=1
            )

        client.start.assert_called_once()

    async def test_shutdown_stops_client(self, manager, tmp_path):
        session = _make_session()
        client = _make_client(session)

        with patch(
            "copilot.CopilotClient", return_value=client
        ):
            await manager.execute_command(
                prompt="hi", working_directory=tmp_path, user_id=1
            )
            await manager.shutdown()

        client.stop.assert_called_once()
        assert manager._client is None


# helper for pytest.approx with ANY
class ANY:
    def __eq__(self, other):
        return True
