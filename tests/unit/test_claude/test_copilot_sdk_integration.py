"""Tests for CopilotSDKManager session lifecycle."""

import asyncio
from types import SimpleNamespace
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
        copilot_permission_mode="interactive",
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
    async def test_uses_configurable_permission_timeout(self, tmp_path):
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            copilot_permission_timeout_seconds=321,
            copilot_ask_user_timeout_seconds=654,
        )
        manager = CopilotSDKManager(config)

        assert manager.interaction_bridge.permission_timeout_seconds == 321
        assert manager.interaction_bridge.ask_user_timeout_seconds == 654

    async def test_new_session_returns_content(self, manager, tmp_path):
        session = _make_session("sid-1", "Hi there!")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            response = await manager.execute_command(
                prompt="hello",
                working_directory=tmp_path,
                user_id=1,
            )

        assert response.content == "Hi there!"
        assert response.session_id == "sid-1"
        assert response.is_error is False
        expected_timeout = manager.config.copilot_timeout_seconds + max(
            manager.interaction_bridge.ask_user_timeout_seconds,
            manager.interaction_bridge.permission_timeout_seconds,
        )
        assert session.send_and_wait.call_args.kwargs["timeout"] == pytest.approx(
            expected_timeout
        )

    async def test_session_id_stored_after_execution(self, manager, tmp_path):
        session = _make_session("stored-sid")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="hello", working_directory=tmp_path, user_id=42
            )

        key = manager._session_key(42, tmp_path)
        assert manager._session_map[key] == "stored-sid"

    async def test_timeout_raises_error(self, manager, tmp_path):
        session = _make_session()
        session.send_and_wait = AsyncMock(side_effect=asyncio.TimeoutError())
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
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

        with patch("copilot.CopilotClient", return_value=client):
            with pytest.raises(ClaudeProcessError, match="rpc failed"):
                await manager.execute_command(
                    prompt="broken",
                    working_directory=tmp_path,
                    user_id=1,
                )

    async def test_empty_model_omits_model_override(self, manager, tmp_path):
        session = _make_session("sid-empty-model")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="hello",
                working_directory=tmp_path,
                user_id=1,
                model="",
            )

        session_config = client.create_session.call_args[0][0]
        assert session_config.get("model") is None

    async def test_session_config_registers_hooks_container(self, manager, tmp_path):
        session = _make_session("sid-hooks-config")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="hello",
                working_directory=tmp_path,
                user_id=1,
            )

        session_config = client.create_session.call_args[0][0]
        hooks = session_config.get("hooks")
        assert isinstance(hooks, dict)
        assert callable(hooks.get("on_pre_tool_use"))
        assert callable(hooks.get("on_error_occurred"))

    async def test_pre_tool_use_auto_allows_sandbox_excluded_command(self, tmp_path):
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            sandbox_enabled=True,
            sandbox_excluded_commands=["git"],
        )
        manager = CopilotSDKManager(config)

        session = _make_session("sid-sandbox-allow")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="allow git",
                working_directory=tmp_path,
                user_id=1,
            )

        session_config = client.create_session.call_args[0][0]
        pre_tool_cb = session_config["hooks"]["on_pre_tool_use"]
        result = await pre_tool_cb(
            SimpleNamespace(toolName="Bash", toolArgs={"command": "git commit -m x"}),
            {},
        )
        assert result["permissionDecision"] == "allow"
        assert "git" in result["permissionDecisionReason"]

    async def test_pre_tool_use_auto_allows_cd_then_excluded_command(self, tmp_path):
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            sandbox_enabled=True,
            sandbox_excluded_commands=["git"],
        )
        manager = CopilotSDKManager(config)

        session = _make_session("sid-sandbox-cd-git")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="allow cd && git",
                working_directory=tmp_path,
                user_id=1,
            )

        session_config = client.create_session.call_args[0][0]
        pre_tool_cb = session_config["hooks"]["on_pre_tool_use"]
        result = await pre_tool_cb(
            SimpleNamespace(
                toolName="Bash", toolArgs={"command": "cd repo && git status"}
            ),
            {},
        )
        assert result["permissionDecision"] == "allow"
        assert "git" in result["permissionDecisionReason"]

    async def test_pre_tool_use_auto_allows_python_module_excluded_command(
        self, tmp_path
    ):
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            sandbox_enabled=True,
            sandbox_excluded_commands=["pip"],
        )
        manager = CopilotSDKManager(config)

        session = _make_session("sid-sandbox-python-pip")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="allow python -m pip",
                working_directory=tmp_path,
                user_id=1,
            )

        session_config = client.create_session.call_args[0][0]
        pre_tool_cb = session_config["hooks"]["on_pre_tool_use"]
        result = await pre_tool_cb(
            SimpleNamespace(
                toolName="Bash", toolArgs={"command": "python -m pip install -U pip"}
            ),
            {},
        )
        assert result["permissionDecision"] == "allow"
        assert "pip" in result["permissionDecisionReason"]

    async def test_pre_tool_use_denies_shell_outside_approved_directory(self, tmp_path):
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            sandbox_enabled=True,
            sandbox_excluded_commands=["git"],
        )
        manager = CopilotSDKManager(config)

        session = _make_session("sid-sandbox-deny")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="deny outside",
                working_directory=tmp_path,
                user_id=1,
            )

        session_config = client.create_session.call_args[0][0]
        pre_tool_cb = session_config["hooks"]["on_pre_tool_use"]
        result = await pre_tool_cb(
            SimpleNamespace(
                toolName="Bash", toolArgs={"command": "cd /tmp && touch /tmp/x"}
            ),
            {},
        )
        assert result["permissionDecision"] == "deny"
        assert "outside approved directory" in result["permissionDecisionReason"]


# ── session lifecycle ─────────────────────────────────────────────────────────


class TestSessionLifecycle:
    async def test_second_call_resumes_session(self, manager, tmp_path):
        session = _make_session("session-xyz", "remembered!")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
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

        with patch("copilot.CopilotClient", return_value=client):
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

        with patch("copilot.CopilotClient", return_value=client):
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

        with patch("copilot.CopilotClient", return_value=client):
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

        with patch("copilot.CopilotClient", return_value=client):
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

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="hi", working_directory=tmp_path, user_id=1
            )
            await manager.shutdown()

        client.stop.assert_called_once()
        assert manager._client is None


class TestHookAndEventCoverage:
    async def test_event_streaming_and_image_attachment(self, manager, tmp_path):
        updates = []

        async def stream_callback(update):
            updates.append(update)

        event_data = MagicMock()
        event_data.content = ""
        result_event = MagicMock()
        result_event.data = event_data

        session = MagicMock()
        session.session_id = "sid-events"
        session.send_and_wait = AsyncMock(return_value=result_event)

        def on_side_effect(handler):
            handler(
                SimpleNamespace(
                    type="assistant.message_delta",
                    data=SimpleNamespace(delta_content="delta"),
                )
            )
            handler(
                SimpleNamespace(
                    type="assistant.reasoning_delta",
                    data=SimpleNamespace(delta_content="reason"),
                )
            )
            handler(
                SimpleNamespace(
                    type="tool_use",
                    data=SimpleNamespace(tool_name="Read", tool_args={"file": "a.py"}),
                )
            )
            handler(
                SimpleNamespace(
                    type="tool_result",
                    data=SimpleNamespace(tool_name="Read", tool_args={"ok": True}),
                )
            )
            handler(
                SimpleNamespace(
                    type="assistant_message",
                    data=SimpleNamespace(content="final-from-event"),
                )
            )
            return lambda: None

        session.on = MagicMock(side_effect=on_side_effect)
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            response = await manager.execute_command(
                prompt="hi",
                working_directory=tmp_path,
                user_id=1,
                stream_callback=stream_callback,
                image_path="/tmp/example.png",
            )

        await asyncio.sleep(0)
        assert response.content == "final-from-event"
        sent_options = session.send_and_wait.call_args[0][0]
        assert sent_options["attachments"][0]["path"] == "/tmp/example.png"
        assert any(u.type == "reasoning" and u.content == "reason" for u in updates)
        assert any(
            u.type == "tool" and (u.metadata or {}).get("action") == "post"
            for u in updates
        )

    async def test_hook_callbacks_cover_decisions_and_timeouts(self, manager, tmp_path):
        observed = []

        async def stream_callback(update):
            observed.append(update.type)
            metadata = update.metadata or {}
            if update.type == "permission_request":
                kind = metadata.get("kind")
                metadata["future"].set_result(kind != "write")
            elif update.type == "ask_user":
                metadata["future"].set_result("picked-choice")

        session = _make_session("sid-hooks", "ok")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="hooks",
                working_directory=tmp_path,
                user_id=1,
                stream_callback=stream_callback,
            )

        session_config = client.create_session.call_args[0][0]
        permission_cb = session_config["on_permission_request"]
        ask_user_cb = session_config["on_user_input_request"]
        error_cb = session_config["on_error_occurred"]
        pre_tool_cb = session_config["on_pre_tool_use"]

        approved = await permission_cb(
            SimpleNamespace(kind="shell", toolCallId="tc-1"), None
        )
        denied = await permission_cb(
            SimpleNamespace(kind="write", toolCallId="tc-2"), None
        )
        approved_from_dict = await permission_cb(
            {"kind": "read", "toolCallId": "tc-3"}, None
        )
        ask_user = await ask_user_cb(
            SimpleNamespace(question="Q?", choices=["A"], allowFreeform=True),
            {"session_id": "sid-hooks"},
        )
        ask_user_from_dict = await ask_user_cb(
            {"question": "Q2?", "choices": ["B"], "allowFreeform": False},
            {"session_id": "sid-hooks"},
        )
        retry = await error_cb(
            SimpleNamespace(
                error="rate limit exceeded", errorContext="sdk", recoverable=False
            ),
            None,
        )
        skip = await error_cb(
            SimpleNamespace(
                error="transient", errorContext="tool_execution", recoverable=True
            ),
            None,
        )
        abort = await error_cb(
            SimpleNamespace(error="fatal", errorContext="runtime", recoverable=False),
            None,
        )
        pre = await pre_tool_cb(
            SimpleNamespace(toolName="Read", toolArgs={"path": "a.txt"}), None
        )

        assert approved["kind"] == "approved"
        assert denied["kind"] == "denied-interactively-by-user"
        assert approved_from_dict["kind"] == "approved"
        assert ask_user == {"answer": "picked-choice", "wasFreeform": True}
        assert ask_user_from_dict == {"answer": "picked-choice", "wasFreeform": False}
        assert retry["errorHandling"] == "retry"
        assert skip["errorHandling"] == "skip"
        assert abort["errorHandling"] == "abort"
        assert "Copilot error (runtime): fatal" in abort["userNotification"]
        assert pre is None
        assert "permission_request" in observed
        assert "ask_user" in observed

        with patch(
            "src.claude.copilot_sdk_integration.asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ):
            denied_on_timeout = await permission_cb(
                SimpleNamespace(kind="shell", toolCallId="tc-timeout"), None
            )
        with patch(
            "src.claude.copilot_sdk_integration.asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ):
            empty_on_timeout = await ask_user_cb(
                SimpleNamespace(
                    question="timeout?", choices=["yes", "no"], allowFreeform=False
                ),
                {"session_id": "sid-hooks"},
            )

        assert denied_on_timeout["kind"] == "denied-interactively-by-user"
        assert empty_on_timeout == {"answer": "", "wasFreeform": False}

    async def test_permission_mode_auto_approve_skips_interactive_bridge(
        self, tmp_path
    ):
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            copilot_permission_mode="auto_approve",
        )
        bridge = MagicMock()
        bridge.create_permission_request = AsyncMock()
        bridge.wait_for_result = AsyncMock(return_value=False)
        bridge.ask_user_timeout_seconds = 300
        manager = CopilotSDKManager(config, interaction_bridge=bridge)

        updates = []

        async def stream_callback(update):
            updates.append(update.type)

        session = _make_session("sid-auto-approve", "ok")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="hooks",
                working_directory=tmp_path,
                user_id=1,
                chat_id=123,
                stream_callback=stream_callback,
            )

        session_config = client.create_session.call_args[0][0]
        permission_cb = session_config["on_permission_request"]
        result = await permission_cb(SimpleNamespace(kind="write", toolCallId="tc-1"), None)

        assert result["kind"] == "approved"
        bridge.create_permission_request.assert_not_called()
        bridge.wait_for_result.assert_not_called()
        assert "permission_request" not in updates

    async def test_permission_mode_auto_deny_returns_rules_deny(self, tmp_path):
        config = Settings(
            telegram_bot_token="test:token",
            telegram_bot_username="testbot",
            approved_directory=tmp_path,
            copilot_permission_mode="auto_deny",
        )
        manager = CopilotSDKManager(config)

        session = _make_session("sid-auto-deny", "ok")
        client = _make_client(session)
        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="hooks",
                working_directory=tmp_path,
                user_id=1,
            )

        session_config = client.create_session.call_args[0][0]
        permission_cb = session_config["on_permission_request"]
        result = await permission_cb(SimpleNamespace(kind="shell", toolCallId="tc-2"), None)
        assert result["kind"] == "denied-by-rules"


class TestMCPAndShutdownCoverage:
    async def test_mcp_servers_loaded_and_applied_to_session_config(
        self, config, tmp_path
    ):
        mcp_json = tmp_path / "mcp.json"
        mcp_json.write_text(
            '{"mcpServers":{"remote":{"url":"https://example.com/sse"},"local":{"command":"python","args":["-m","srv"],"env":{"TOKEN":"x"}}}}',
            encoding="utf-8",
        )
        config.enable_mcp = True
        config.mcp_config_path = str(mcp_json)
        manager = CopilotSDKManager(config)

        session = _make_session("sid-mcp", "ok")
        client = _make_client(session)

        with patch("copilot.CopilotClient", return_value=client):
            await manager.execute_command(
                prompt="mcp", working_directory=tmp_path, user_id=1
            )

        session_config = client.create_session.call_args[0][0]
        mcp_servers = session_config.get("mcp_servers", [])
        assert len(mcp_servers) == 2
        assert any(s.get("type") == "sse" for s in mcp_servers)
        assert any(s.get("type") == "stdio" for s in mcp_servers)
        assert session_config.get("infinite_sessions", {}).get("enabled") is True

    def test_load_mcp_servers_handles_missing_and_invalid_json(self, config, tmp_path):
        manager = CopilotSDKManager(config)
        assert manager._load_mcp_servers() == []

        config.enable_mcp = True
        config.mcp_config_path = str(tmp_path / "missing.json")
        assert manager._load_mcp_servers() == []

        invalid_json = tmp_path / "invalid.json"
        invalid_json.write_text("{broken", encoding="utf-8")
        config.mcp_config_path = str(invalid_json)
        assert manager._load_mcp_servers() == []

    def test_load_mcp_servers_env_value_mode_variants(self, config, tmp_path):
        mcp_json = tmp_path / "mcp-env.json"
        mcp_json.write_text(
            '{"mcpServers":{"local":{"command":"python","args":["-m","srv"],"env":{"TOKEN":"secret","K":"v"}}}}',
            encoding="utf-8",
        )
        config.enable_mcp = True
        config.mcp_config_path = str(mcp_json)
        manager = CopilotSDKManager(config)

        raw = manager._load_mcp_servers("raw")
        masked = manager._load_mcp_servers("masked")
        omitted = manager._load_mcp_servers("omit")

        assert raw[0]["env"] == {"TOKEN": "secret", "K": "v"}
        assert masked[0]["env"] == {"TOKEN": "***", "K": "***"}
        assert omitted[0]["env"] == {}

    async def test_shutdown_handles_stop_exception(self, manager):
        client = MagicMock()
        client.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        manager._client = client

        await manager.shutdown()
        assert manager._client is None


# helper for pytest.approx with ANY
class ANY:
    def __eq__(self, other):
        return True
