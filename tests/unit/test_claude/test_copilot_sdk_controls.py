"""Focused tests for CopilotSDKManager control-plane helpers."""

import asyncio
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.claude.copilot_sdk_integration import CopilotSDKManager
from src.config.settings import Settings


def _make_config(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="test:token",
        telegram_bot_username="testbot",
        approved_directory=tmp_path,
    )


async def test_switch_session_pins_user_project_mapping(tmp_path: Path):
    manager = CopilotSDKManager(_make_config(tmp_path))
    manager._persist_session_map = MagicMock()

    result = manager.switch_session(
        user_id=7,
        working_directory=tmp_path,
        session_id="sid-new",
    )

    key = manager._session_key(7, tmp_path)
    assert manager._session_map[key] == "sid-new"
    assert result["previous_session_id"] is None
    assert result["current_session_id"] == "sid-new"
    manager._persist_session_map.assert_called_once()


async def test_list_sessions_merges_sdk_and_local_map(tmp_path: Path):
    manager = CopilotSDKManager(_make_config(tmp_path))
    manager._session_map[manager._session_key(11, tmp_path)] = "local-sid"

    client = MagicMock()
    client.list_sessions = AsyncMock(
        return_value=[
            {"session_id": "sdk-sid", "workspacePath": str(tmp_path), "userId": 99},
            {"session_id": "local-sid", "workspacePath": str(tmp_path), "userId": 11},
        ]
    )
    manager._get_client = AsyncMock(return_value=client)

    rows = await manager.list_sessions()

    assert any(r["session_id"] == "sdk-sid" and r["source"] == "sdk" for r in rows)
    assert any(
        r["session_id"] == "local-sid" and r["source"] == "local_map" for r in rows
    )


async def test_delete_session_lazily_initializes_client(tmp_path: Path):
    manager = CopilotSDKManager(_make_config(tmp_path))
    manager._session_map[manager._session_key(1, tmp_path)] = "sid-del"
    manager._persist_session_map = MagicMock()

    client = MagicMock()
    client.delete_session = AsyncMock(return_value=None)
    manager._get_client = AsyncMock(return_value=client)

    result = await manager.delete_session("sid-del")

    assert result["removed_local"] is True
    assert result["removed_sdk"] is True
    manager._get_client.assert_awaited_once()
    client.delete_session.assert_awaited_once_with("sid-del")


async def test_reasoning_levels_infers_xhigh_from_distribution_version(tmp_path: Path):
    manager = CopilotSDKManager(_make_config(tmp_path))

    with patch.object(
        manager,
        "_detect_sdk_package",
        return_value={"distribution": "github-copilot-sdk", "version": "0.1.25"},
    ):
        with patch.object(
            manager, "get_status", AsyncMock(return_value={"health": "healthy"})
        ):
            levels = await manager.get_reasoning_levels()

    assert levels == ["low", "medium", "high", "xhigh"]


async def test_reasoning_levels_preview_package_requires_opt_in(tmp_path: Path):
    config = _make_config(tmp_path)
    config.copilot_enable_prerelease_features = False
    manager = CopilotSDKManager(config)

    with patch.object(
        manager,
        "_detect_sdk_package",
        return_value={
            "distribution": "github-copilot-sdk",
            "version": "0.1.26-preview.0",
        },
    ):
        with patch.object(
            manager,
            "get_status",
            AsyncMock(return_value={"model": {"reasoning_effort": "xhigh"}}),
        ):
            levels = await manager.get_reasoning_levels()

    assert levels == ["low", "medium", "high"]


async def test_reasoning_levels_preview_package_with_opt_in(tmp_path: Path):
    config = _make_config(tmp_path)
    config.copilot_enable_prerelease_features = True
    manager = CopilotSDKManager(config)

    with patch.object(
        manager,
        "_detect_sdk_package",
        return_value={
            "distribution": "github-copilot-sdk",
            "version": "0.1.26-preview.0",
        },
    ):
        with patch.object(
            manager,
            "get_status",
            AsyncMock(return_value={"model": {"reasoning_effort": "xhigh"}}),
        ):
            levels = await manager.get_reasoning_levels()

    assert levels == ["low", "medium", "high", "xhigh"]


async def test_doctor_report_flags_legacy_package_and_import_error(tmp_path: Path):
    manager = CopilotSDKManager(_make_config(tmp_path))

    with patch.object(
        manager,
        "get_status",
        AsyncMock(return_value={"health": "degraded", "reason": "init"}),
    ):
        with patch.object(
            manager,
            "get_capabilities",
            AsyncMock(
                return_value={
                    "sdk_importable": False,
                    "package": {"distribution": "copilot", "version": "0.1.9"},
                }
            ),
        ):
            report = await manager.get_doctor_report()

    warnings = "\n".join(report.get("warnings", []))
    assert "legacy 'copilot'" in warnings
    assert "not importable" in warnings


async def test_doctor_report_warns_preview_opt_in_disabled(tmp_path: Path):
    config = _make_config(tmp_path)
    config.copilot_enable_prerelease_features = False
    manager = CopilotSDKManager(config)

    with patch.object(
        manager,
        "get_status",
        AsyncMock(return_value={"health": "healthy", "reason": None}),
    ):
        with patch.object(
            manager,
            "get_capabilities",
            AsyncMock(
                return_value={
                    "sdk_importable": True,
                    "package": {
                        "distribution": "github-copilot-sdk",
                        "version": "0.1.26-preview.0",
                    },
                }
            ),
        ):
            report = await manager.get_doctor_report()

    warnings = "\n".join(report.get("warnings", []))
    assert "Preview SDK detected" in warnings


def _fake_copilot_module():
    class SessionConfig(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    class ResumeSessionConfig(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    return types.SimpleNamespace(
        SessionConfig=SessionConfig,
        ResumeSessionConfig=ResumeSessionConfig,
    )


async def test_execute_command_high_event_load_still_unsubscribes(tmp_path: Path):
    manager = CopilotSDKManager(_make_config(tmp_path))

    unsubscribe = MagicMock()
    callback_updates = []
    event_handler = None

    class Session:
        session_id = "sid-load"

        def on(self, handler):
            nonlocal event_handler
            event_handler = handler
            return unsubscribe

        async def send_and_wait(self, _message_options):
            for i in range(500):
                event_handler(
                    types.SimpleNamespace(
                        type=f"unknown_{i}", data=types.SimpleNamespace()
                    )
                )
            event_handler(
                types.SimpleNamespace(
                    type="session.context_changed",
                    data=types.SimpleNamespace(
                        session_id="sid-load", reason="compaction"
                    ),
                )
            )
            return types.SimpleNamespace(
                data=types.SimpleNamespace(content="final result")
            )

    client = MagicMock()
    client.create_session = AsyncMock(return_value=Session())
    manager._get_client = AsyncMock(return_value=client)

    async def stream_callback(update):
        callback_updates.append(update.type)

    with patch.dict("sys.modules", {"copilot": _fake_copilot_module()}):
        response = await manager.execute_command(
            prompt="stress test",
            working_directory=tmp_path,
            user_id=88,
            stream_callback=stream_callback,
        )
    await asyncio.sleep(0)

    assert response.content == "final result"
    unsubscribe.assert_called_once()
    assert "context_changed" in callback_updates


async def test_error_hook_retry_skip_abort_mapping(tmp_path: Path):
    manager = CopilotSDKManager(_make_config(tmp_path))

    class Session:
        session_id = "sid-hooks"

        def on(self, _handler):
            return lambda: None

        async def send_and_wait(self, _message_options):
            return types.SimpleNamespace(data=types.SimpleNamespace(content="ok"))

    client = MagicMock()
    client.create_session = AsyncMock(return_value=Session())
    manager._get_client = AsyncMock(return_value=client)

    with patch.dict("sys.modules", {"copilot": _fake_copilot_module()}):
        await manager.execute_command(
            prompt="hooks",
            working_directory=tmp_path,
            user_id=42,
        )

    cfg = client.create_session.call_args.args[0]
    hook = cfg["on_error_occurred"]

    retry_result = await hook(
        types.SimpleNamespace(
            error="429 rate limit",
            errorContext="model_call",
            recoverable=False,
        ),
        None,
    )
    assert retry_result == {"errorHandling": "retry", "retryCount": 3}

    skip_result = await hook(
        types.SimpleNamespace(
            error="tool failed",
            errorContext="tool_execution",
            recoverable=True,
        ),
        None,
    )
    assert skip_result == {"errorHandling": "skip"}

    abort_result = await hook(
        types.SimpleNamespace(
            error="fatal error",
            errorContext="runtime",
            recoverable=False,
        ),
        None,
    )
    assert abort_result["errorHandling"] == "abort"
    assert "fatal error" in abort_result["userNotification"]
