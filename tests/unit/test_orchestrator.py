"""Tests for the MessageOrchestrator."""

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.bot.orchestrator as orchestrator_module
from src.bot.orchestrator import MessageOrchestrator, _redact_secrets
from src.config import create_test_config
from src.memory import MemoryPreHookResult
from src.storage.models import MemoryRuntimeSettingsModel


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def agentic_settings(tmp_dir):
    return create_test_config(approved_directory=str(tmp_dir), agentic_mode=True)


@pytest.fixture
def classic_settings(tmp_dir):
    return create_test_config(approved_directory=str(tmp_dir), agentic_mode=False)


@pytest.fixture
def group_thread_settings(tmp_dir):
    project_dir = tmp_dir / "project_a"
    project_dir.mkdir()
    config_file = tmp_dir / "projects.yaml"
    config_file.write_text(
        "projects:\n"
        "  - slug: project_a\n"
        "    name: Project A\n"
        "    path: project_a\n",
        encoding="utf-8",
    )
    return create_test_config(
        approved_directory=str(tmp_dir),
        agentic_mode=False,
        enable_project_threads=True,
        project_threads_mode="group",
        project_threads_chat_id=-1001234567890,
        projects_config_path=str(config_file),
    )


@pytest.fixture
def private_thread_settings(tmp_dir):
    project_dir = tmp_dir / "project_a"
    project_dir.mkdir()
    config_file = tmp_dir / "projects.yaml"
    config_file.write_text(
        "projects:\n"
        "  - slug: project_a\n"
        "    name: Project A\n"
        "    path: project_a\n",
        encoding="utf-8",
    )
    return create_test_config(
        approved_directory=str(tmp_dir),
        agentic_mode=False,
        enable_project_threads=True,
        project_threads_mode="private",
        projects_config_path=str(config_file),
    )


@pytest.fixture
def deps():
    return {
        "claude_integration": MagicMock(),
        "storage": MagicMock(),
        "security_validator": MagicMock(),
        "rate_limiter": MagicMock(),
        "audit_logger": MagicMock(),
    }


def _memory_runtime_model() -> MemoryRuntimeSettingsModel:
    return MemoryRuntimeSettingsModel(
        scope_key="123:-100:0",
        user_id=123,
        chat_id=-100,
        message_thread_id=0,
        memory_system_plus_enabled=True,
        memory_hooks_enabled=True,
        memory_pre_hook_enabled=True,
        memory_post_hook_enabled=True,
        memory_ai_enhancement_enabled=True,
        memory_ai_extractor_enabled=True,
        memory_ai_reranker_enabled=True,
        memory_ai_conflict_detector_enabled=True,
        memory_ai_periodic_review_enabled=True,
        memory_profile="balanced",
        memory_ai_model="gpt-5-mini",
        memory_ai_timeout_seconds=20,
        memory_recall_limit=20,
        memory_injection_token_budget=800,
    )


def test_agentic_registers_10_commands(agentic_settings, deps):
    """Agentic mode registers start/new/status/session_name/verbose/memory/repo/provider/model/copilot."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()

    orchestrator.register_handlers(app)

    # Collect all CommandHandler registrations
    from telegram.ext import CommandHandler

    cmd_handlers = [
        call
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], CommandHandler)
    ]
    commands = [h[0][0].commands for h in cmd_handlers]

    assert len(cmd_handlers) == 10
    assert frozenset({"start"}) in commands
    assert frozenset({"new"}) in commands
    assert frozenset({"status"}) in commands
    assert frozenset({"session_name"}) in commands
    assert frozenset({"verbose"}) in commands
    assert frozenset({"memory"}) in commands
    assert frozenset({"repo"}) in commands
    assert frozenset({"provider"}) in commands
    assert frozenset({"model"}) in commands
    assert frozenset({"copilot"}) in commands


def test_classic_registers_18_commands(classic_settings, deps):
    """Classic mode registers all 18 commands."""
    orchestrator = MessageOrchestrator(classic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()

    orchestrator.register_handlers(app)

    from telegram.ext import CommandHandler

    cmd_handlers = [
        call
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], CommandHandler)
    ]

    assert len(cmd_handlers) == 18


def test_agentic_registers_text_document_photo_handlers(agentic_settings, deps):
    """Agentic mode registers text, document, and photo message handlers."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()

    orchestrator.register_handlers(app)

    from telegram.ext import CallbackQueryHandler, MessageHandler

    msg_handlers = [
        call
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], MessageHandler)
    ]
    cb_handlers = [
        call
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], CallbackQueryHandler)
    ]

    # 3 message handlers (text, document, photo)
    assert len(msg_handlers) == 3
    # 5 callback handlers (cd:, memory:, ask_user:, model:, perm:)
    assert len(cb_handlers) == 5


async def test_agentic_bot_commands(agentic_settings, deps):
    """Agentic mode returns 10 bot commands."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    commands = await orchestrator.get_bot_commands()

    assert len(commands) == 10
    cmd_names = [c.command for c in commands]
    assert cmd_names == [
        "start",
        "new",
        "status",
        "session_name",
        "verbose",
        "memory",
        "repo",
        "provider",
        "model",
        "copilot",
    ]


async def test_classic_bot_commands(classic_settings, deps):
    """Classic mode returns 18 bot commands."""
    orchestrator = MessageOrchestrator(classic_settings, deps)
    commands = await orchestrator.get_bot_commands()

    assert len(commands) == 18
    cmd_names = [c.command for c in commands]
    assert "start" in cmd_names
    assert "help" in cmd_names
    assert "session_name" in cmd_names
    assert "git" in cmd_names


async def test_agentic_start_no_keyboard(agentic_settings, deps):
    """Agentic /start sends brief message without inline keyboard."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.effective_user.first_name = "Alice"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {"settings": agentic_settings}
    for k, v in deps.items():
        context.bot_data[k] = v

    await orchestrator.agentic_start(update, context)

    update.message.reply_text.assert_called_once()
    call_kwargs = update.message.reply_text.call_args
    # No reply_markup argument (no keyboard)
    assert (
        "reply_markup" not in call_kwargs.kwargs
        or call_kwargs.kwargs.get("reply_markup") is None
    )
    # Contains user name
    assert "Alice" in call_kwargs.args[0]


async def test_agentic_new_resets_session(agentic_settings, deps):
    """Agentic /new clears session and sends brief confirmation."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {"claude_session_id": "old-session-123"}

    await orchestrator.agentic_new(update, context)

    assert context.user_data["claude_session_id"] is None
    update.message.reply_text.assert_called_once_with("Session reset. What's next?")


async def test_agentic_status_compact(agentic_settings, deps):
    """Agentic /status returns compact one-line status."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {"rate_limiter": None}

    await orchestrator.agentic_status(update, context)

    call_args = update.message.reply_text.call_args
    text = call_args.args[0]
    assert "Session: none" in text


async def test_agentic_copilot_uses_effective_message(agentic_settings, deps, monkeypatch):
    """/copilot replies through effective_message when update.message is missing."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    run_mock = AsyncMock(return_value=("ok", "HTML"))
    monkeypatch.setattr(orchestrator_module, "run_copilot_control_command", run_mock)

    update = MagicMock()
    update.message = None
    update.effective_user.id = 123
    update.effective_message.reply_text = AsyncMock()

    context = MagicMock()
    context.args = ["status"]
    context.user_data = {}
    context.bot_data = {"claude_integration": MagicMock()}

    await orchestrator.agentic_copilot(update, context)

    update.effective_message.reply_text.assert_called_once_with("ok", parse_mode="HTML")
    run_mock.assert_awaited_once()


async def test_agentic_text_calls_claude(agentic_settings, deps):
    """Agentic text handler calls Claude and returns response without keyboard."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    # Mock Claude response
    mock_response = MagicMock()
    mock_response.session_id = "session-abc"
    mock_response.content = "Hello, I can help with that!"
    mock_response.tools_used = []

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock(return_value=mock_response)

    update = MagicMock()
    update.effective_user.id = 123
    update.message.text = "Help me with this code"
    update.message.message_id = 1
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()

    # Progress message mock
    progress_msg = AsyncMock()
    progress_msg.delete = AsyncMock()
    update.message.reply_text.return_value = progress_msg

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "settings": agentic_settings,
        "claude_integration": claude_integration,
        "storage": None,
        "rate_limiter": None,
        "audit_logger": None,
    }

    await orchestrator.agentic_text(update, context)

    # Claude was called
    claude_integration.run_command.assert_called_once()

    # Session ID updated
    assert context.user_data["claude_session_id"] == "session-abc"

    # Progress message deleted
    progress_msg.delete.assert_called_once()

    # Response sent without keyboard (reply_markup=None)
    response_calls = [
        c
        for c in update.message.reply_text.call_args_list
        if c != update.message.reply_text.call_args_list[0]
    ]
    for call in response_calls:
        assert call.kwargs.get("reply_markup") is None


async def test_agentic_text_memory_pre_hook_fallback(agentic_settings, deps):
    """Pre-hook failure should not block Claude execution."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    mock_response = MagicMock()
    mock_response.session_id = "session-pre-fallback"
    mock_response.content = "ok"
    mock_response.tools_used = []

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock(return_value=mock_response)

    memory_service = AsyncMock()
    memory_service.apply_pre_hook = AsyncMock(side_effect=RuntimeError("boom"))
    memory_service.apply_post_hook = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 123
    update.effective_chat.id = -100
    update.message.text = "original prompt"
    update.message.message_id = 1
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()

    progress_msg = AsyncMock()
    progress_msg.delete = AsyncMock()
    update.message.reply_text.return_value = progress_msg

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "settings": agentic_settings,
        "claude_integration": claude_integration,
        "memory_service": memory_service,
        "storage": None,
        "rate_limiter": None,
        "audit_logger": None,
    }

    await orchestrator.agentic_text(update, context)

    assert (
        claude_integration.run_command.call_args.kwargs["prompt"] == "original prompt"
    )
    memory_service.apply_post_hook.assert_called_once()


async def test_agentic_text_memory_hook_pipeline(agentic_settings, deps):
    """Memory pre/post hooks should wrap agentic text execution."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    mock_response = MagicMock()
    mock_response.session_id = "session-memory"
    mock_response.content = "assistant output"
    mock_response.tools_used = []

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock(return_value=mock_response)

    memory_service = AsyncMock()
    memory_service.apply_pre_hook = AsyncMock(
        return_value=MemoryPreHookResult(
            prompt="[Memory Context]\n1. [fact] x\n\n---\noriginal prompt",
            controls={
                "provider": "copilot",
                "copilot_model": "gpt-5-mini",
                "claude_model": "claude-sonnet-4-6",
                "reasoning_effort": "high",
                "skill_directories": [],
                "disabled_skills": [],
                "mcp_env_value_mode": "raw",
                "external_cli_server": None,
            },
            runtime_settings=_memory_runtime_model(),
        )
    )
    memory_service.apply_post_hook = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 123
    update.effective_chat.id = -100
    update.message.text = "original prompt"
    update.message.message_id = 1
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()

    progress_msg = AsyncMock()
    progress_msg.delete = AsyncMock()
    update.message.reply_text.return_value = progress_msg

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "settings": agentic_settings,
        "claude_integration": claude_integration,
        "memory_service": memory_service,
        "storage": None,
        "rate_limiter": None,
        "audit_logger": None,
    }

    await orchestrator.agentic_text(update, context)

    assert claude_integration.run_command.call_args.kwargs["prompt"].startswith(
        "[Memory Context]"
    )
    post_kwargs = memory_service.apply_post_hook.call_args.kwargs
    assert post_kwargs["prompt"] == "original prompt"
    assert post_kwargs["success"] is True


async def test_agentic_callback_scoped_to_cd_pattern(agentic_settings, deps):
    """Agentic callback handlers include one scoped to cd: pattern."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)
    app = MagicMock()
    app.add_handler = MagicMock()

    orchestrator.register_handlers(app)

    from telegram.ext import CallbackQueryHandler

    cb_handlers = [
        call[0][0]
        for call in app.add_handler.call_args_list
        if isinstance(call[0][0], CallbackQueryHandler)
    ]

    assert len(cb_handlers) == 5
    cd_handlers = [h for h in cb_handlers if h.pattern and h.pattern.pattern == "^cd:"]
    assert len(cd_handlers) == 1
    memory_handlers = [
        h for h in cb_handlers if h.pattern and h.pattern.pattern == "^memory:"
    ]
    assert len(memory_handlers) == 1
    model_handlers = [
        h for h in cb_handlers if h.pattern and h.pattern.pattern == "^model:"
    ]
    assert len(model_handlers) == 1
    # The cd: handler pattern should match cd: prefixed data
    assert cd_handlers[0].pattern is not None
    assert cd_handlers[0].pattern.match("cd:my_project")
    assert memory_handlers[0].pattern is not None
    assert memory_handlers[0].pattern.match("memory:panel")


async def test_agentic_repo_lists_from_current_directory(
    agentic_settings, deps, tmp_dir
):
    """Agentic /repo should list subdirectories under current_directory."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    current_dir = tmp_dir / "workspace"
    current_dir.mkdir()
    (current_dir / "proj-a").mkdir()
    (current_dir / "proj-b").mkdir()

    update = MagicMock()
    update.message.text = "/repo"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {"current_directory": current_dir}
    context.bot_data = {"claude_integration": None}

    await orchestrator.agentic_repo(update, context)

    call = update.message.reply_text.call_args
    text = call.args[0]
    assert "Current: <code>workspace/</code>" in text
    assert "<code>proj-a/</code>" in text
    assert "<code>proj-b/</code>" in text

    keyboard = call.kwargs["reply_markup"].inline_keyboard
    callback_data = [button.callback_data for row in keyboard for button in row]
    assert "cd:browse:proj-a" in callback_data
    assert "cd:browse:proj-b" in callback_data
    assert "cd:browse:.." in callback_data
    assert "cd:browse:/" in callback_data
    assert "cd:confirm" in callback_data


async def test_agentic_repo_switches_relative_to_current_directory(
    agentic_settings, deps, tmp_dir
):
    """Agentic /repo <name> resolves from current_directory (not always root)."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    parent = tmp_dir / "team"
    target = parent / "service"
    target.mkdir(parents=True)

    claude_integration = AsyncMock()
    claude_integration._find_resumable_session = AsyncMock(return_value=None)

    update = MagicMock()
    update.effective_user.id = 7
    update.message.text = "/repo service"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {"current_directory": parent}
    context.bot_data = {"claude_integration": claude_integration}

    await orchestrator.agentic_repo(update, context)

    assert context.user_data["current_directory"] == target.resolve()
    call = update.message.reply_text.call_args
    assert "Switched to <code>team/service/</code>" in call.args[0]


async def test_agentic_repo_blocks_path_outside_approved_root(
    agentic_settings, deps, tmp_dir
):
    """Agentic /repo should block traversal outside approved root."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    current_dir = tmp_dir / "workspace"
    current_dir.mkdir()

    update = MagicMock()
    update.message.text = "/repo ../../"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {"current_directory": current_dir}
    context.bot_data = {"claude_integration": None}

    await orchestrator.agentic_repo(update, context)

    call = update.message.reply_text.call_args
    assert "Access denied" in call.args[0]
    assert context.user_data["current_directory"] == current_dir


async def test_agentic_callback_cd_parent(agentic_settings, deps, tmp_dir):
    """cd parent switch uses browse+confirm flow within approved root."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    parent = tmp_dir / "team"
    current = parent / "service"
    current.mkdir(parents=True)

    claude_integration = AsyncMock()
    claude_integration._find_resumable_session = AsyncMock(return_value=None)
    audit_logger = AsyncMock()
    audit_logger.log_command = AsyncMock()

    query = MagicMock()
    query.data = "cd:browse:.."
    query.from_user.id = 88
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query

    context = MagicMock()
    context.user_data = {"current_directory": current}
    context.bot_data = {
        "claude_integration": claude_integration,
        "audit_logger": audit_logger,
    }

    await orchestrator._agentic_callback(update, context)

    # Browse stage updates pending_directory only.
    assert context.user_data["current_directory"] == current
    assert context.user_data["pending_directory"] == parent.resolve()

    query.data = "cd:confirm"
    await orchestrator._agentic_callback(update, context)

    assert context.user_data["current_directory"] == parent.resolve()
    call = query.edit_message_text.call_args
    assert "Switched to <code>team/</code>" in call.args[0]
    audit_logger.log_command.assert_called_once()


async def test_agentic_callback_browse_uses_pending_directory_base(
    agentic_settings, deps, tmp_dir
):
    """Nested browse callbacks should resolve from pending_directory."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    first = tmp_dir / "github"
    second = first / "claude-code-telegram"
    second.mkdir(parents=True)

    query = MagicMock()
    query.from_user.id = 99
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query

    context = MagicMock()
    context.user_data = {"current_directory": tmp_dir}
    context.bot_data = {"claude_integration": None}

    query.data = "cd:browse:github"
    await orchestrator._agentic_callback(update, context)
    assert context.user_data["pending_directory"] == first.resolve()
    assert context.user_data["current_directory"] == tmp_dir

    query.data = "cd:browse:claude-code-telegram"
    await orchestrator._agentic_callback(update, context)
    assert context.user_data["pending_directory"] == second.resolve()
    call = query.edit_message_text.call_args
    assert "Current: <code>github/claude-code-telegram/</code>" in call.args[0]
    assert "No subdirectories here." in call.args[0]


async def test_agentic_callback_browse_supports_colon_in_directory_name(
    agentic_settings, deps, tmp_dir
):
    """Browse callback payload parsing should preserve ':' in directory names."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    target = tmp_dir / "team:alpha"
    target.mkdir()

    query = MagicMock()
    query.data = "cd:browse:team:alpha"
    query.from_user.id = 77
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query

    context = MagicMock()
    context.user_data = {"current_directory": tmp_dir}
    context.bot_data = {"claude_integration": None}

    await orchestrator._agentic_callback(update, context)

    assert context.user_data["pending_directory"] == target.resolve()
    call = query.edit_message_text.call_args
    assert "Current: <code>team:alpha/</code>" in call.args[0]


async def test_agentic_document_rejects_large_files(agentic_settings, deps):
    """Agentic document handler rejects files over 10MB."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.effective_user.id = 123
    update.message.document.file_name = "big.bin"
    update.message.document.file_size = 20 * 1024 * 1024  # 20MB
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot_data = {"security_validator": None}

    await orchestrator.agentic_document(update, context)

    call_args = update.message.reply_text.call_args
    assert "too large" in call_args.args[0].lower()


async def test_agentic_start_escapes_html_in_name(agentic_settings, deps):
    """Names with HTML-special characters are escaped safely."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    update = MagicMock()
    update.effective_user.first_name = "A<B>&C"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {}

    await orchestrator.agentic_start(update, context)

    call_kwargs = update.message.reply_text.call_args
    text = call_kwargs.args[0]
    # HTML-special characters should be escaped
    assert "A&lt;B&gt;&amp;C" in text
    # parse_mode is HTML
    assert call_kwargs.kwargs.get("parse_mode") == "HTML"


async def test_agentic_text_logs_failure_on_error(agentic_settings, deps):
    """Failed Claude runs are logged with success=False."""
    orchestrator = MessageOrchestrator(agentic_settings, deps)

    claude_integration = AsyncMock()
    claude_integration.run_command = AsyncMock(side_effect=Exception("Claude broke"))

    audit_logger = AsyncMock()
    audit_logger.log_command = AsyncMock()

    update = MagicMock()
    update.effective_user.id = 123
    update.message.text = "do something"
    update.message.message_id = 1
    update.message.chat.send_action = AsyncMock()
    update.message.reply_text = AsyncMock()

    progress_msg = AsyncMock()
    progress_msg.delete = AsyncMock()
    update.message.reply_text.return_value = progress_msg

    context = MagicMock()
    context.user_data = {}
    context.bot_data = {
        "settings": agentic_settings,
        "claude_integration": claude_integration,
        "storage": None,
        "rate_limiter": None,
        "audit_logger": audit_logger,
    }

    await orchestrator.agentic_text(update, context)

    # Audit logged with success=False
    audit_logger.log_command.assert_called_once()
    call_kwargs = audit_logger.log_command.call_args
    assert call_kwargs.kwargs["success"] is False


# --- _redact_secrets / _summarize_tool_input tests ---


class TestRedactSecrets:
    """Ensure sensitive substrings are redacted from Bash command summaries."""

    def test_safe_command_unchanged(self):
        assert (
            _redact_secrets("poetry run pytest tests/ -v")
            == "poetry run pytest tests/ -v"
        )

    def test_anthropic_api_key_redacted(self):
        key = "sk-ant-api03-abc123def456ghi789jkl012mno345"
        cmd = f"ANTHROPIC_API_KEY={key}"
        result = _redact_secrets(cmd)
        assert key not in result
        assert "***" in result

    def test_sk_key_redacted(self):
        cmd = "curl -H 'Authorization: Bearer sk-1234567890abcdefghijklmnop'"
        result = _redact_secrets(cmd)
        assert "sk-1234567890abcdefghijklmnop" not in result
        assert "***" in result

    def test_github_pat_redacted(self):
        cmd = "git clone https://ghp_abcdefghijklmnop1234@github.com/user/repo"
        result = _redact_secrets(cmd)
        assert "ghp_abcdefghijklmnop1234" not in result
        assert "***" in result

    def test_aws_key_redacted(self):
        cmd = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        result = _redact_secrets(cmd)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "***" in result

    def test_flag_token_redacted(self):
        cmd = "mycli --token=supersecretvalue123"
        result = _redact_secrets(cmd)
        assert "supersecretvalue123" not in result
        assert "--token=" in result or "--token" in result

    def test_password_env_redacted(self):
        cmd = "PASSWORD=MyS3cretP@ss! ./run.sh"
        result = _redact_secrets(cmd)
        assert "MyS3cretP@ss!" not in result
        assert "***" in result

    def test_bearer_token_redacted(self):
        cmd = "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig'"
        result = _redact_secrets(cmd)
        assert "eyJhbGciOiJIUzI1NiJ9.payload.sig" not in result

    def test_connection_string_redacted(self):
        cmd = "psql postgresql://admin:secret_password@db.host:5432/mydb"
        result = _redact_secrets(cmd)
        assert "secret_password" not in result

    def test_summarize_tool_input_bash_redacts(self, agentic_settings, deps):
        """_summarize_tool_input applies redaction to Bash commands."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input(
            "Bash",
            {"command": "curl --token=mysupersecrettoken123 https://api.example.com"},
        )
        assert "mysupersecrettoken123" not in result
        assert "***" in result

    def test_summarize_tool_input_non_bash_unchanged(self, agentic_settings, deps):
        """Non-Bash tools don't go through redaction."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        result = orchestrator._summarize_tool_input(
            "Read", {"file_path": "/home/user/.env"}
        )
        assert result == ".env"


# --- Typing heartbeat tests ---


class TestTypingHeartbeat:
    """Verify typing indicator stays alive independently of stream events."""

    async def test_heartbeat_sends_typing_action(self, agentic_settings, deps):
        """Heartbeat sends typing actions at the configured interval."""
        chat = AsyncMock()
        chat.send_action = AsyncMock()

        orchestrator = MessageOrchestrator(agentic_settings, deps)
        heartbeat = orchestrator._start_typing_heartbeat(chat, interval=0.05)

        # Let the heartbeat fire a few times
        await asyncio.sleep(0.2)
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        # Should have been called multiple times
        assert chat.send_action.call_count >= 2
        chat.send_action.assert_called_with("typing")

    async def test_heartbeat_cancels_cleanly(self, agentic_settings, deps):
        """Cancelling the heartbeat task does not raise."""
        chat = AsyncMock()
        orchestrator = MessageOrchestrator(agentic_settings, deps)
        heartbeat = orchestrator._start_typing_heartbeat(chat, interval=0.05)

        heartbeat.cancel()
        # Should not raise
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        assert heartbeat.cancelled() or heartbeat.done()

    async def test_heartbeat_survives_send_action_errors(self, agentic_settings, deps):
        """Heartbeat keeps running even if send_action raises."""
        chat = AsyncMock()
        call_count = [0]

        async def flaky_send_action(action: str) -> None:
            call_count[0] += 1
            if call_count[0] <= 2:
                raise Exception("Network error")

        chat.send_action = flaky_send_action

        orchestrator = MessageOrchestrator(agentic_settings, deps)
        heartbeat = orchestrator._start_typing_heartbeat(chat, interval=0.05)

        await asyncio.sleep(0.3)
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass

        # Should have called send_action more than 2 times (survived errors)
        assert call_count[0] >= 3

    async def test_stream_callback_independent_of_typing(self, agentic_settings, deps):
        """Stream callback no longer sends typing — that's the heartbeat's job."""
        orchestrator = MessageOrchestrator(agentic_settings, deps)

        progress_msg = AsyncMock()
        tool_log: list = []  # type: ignore[type-arg]
        callback = orchestrator._make_stream_callback(
            verbose_level=1,
            progress_msg=progress_msg,
            tool_log=tool_log,
            start_time=0.0,
        )
        assert callback is not None

        # Verify the callback signature doesn't accept a 'chat' parameter
        # (typing is no longer handled by the stream callback)
        import inspect

        sig = inspect.signature(orchestrator._make_stream_callback)
        assert "chat" not in sig.parameters


async def test_group_thread_mode_rejects_non_forum_chat(group_thread_settings, deps):
    """Strict thread mode rejects updates outside configured forum chat."""
    orchestrator = MessageOrchestrator(group_thread_settings, deps)

    project_threads_manager = MagicMock()
    project_threads_manager.guidance_message.return_value = "Use project thread"
    deps["project_threads_manager"] = project_threads_manager

    called = {"value": False}

    async def dummy_handler(update, context):
        called["value"] = True

    wrapped = orchestrator._inject_deps(dummy_handler)

    update = MagicMock()
    update.effective_chat.id = -1002222222
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {}

    await wrapped(update, context)

    assert called["value"] is False
    update.effective_message.reply_text.assert_called_once()


async def test_thread_mode_loads_and_persists_thread_state(group_thread_settings, deps):
    """Thread mode loads per-thread context and writes updates back."""
    orchestrator = MessageOrchestrator(group_thread_settings, deps)

    project_path = group_thread_settings.approved_directory / "project_a"
    project = SimpleNamespace(
        slug="project_a",
        name="Project A",
        absolute_path=project_path,
    )

    project_threads_manager = MagicMock()
    project_threads_manager.resolve_project = AsyncMock(return_value=project)
    project_threads_manager.guidance_message.return_value = "Use project thread"
    deps["project_threads_manager"] = project_threads_manager

    async def dummy_handler(update, context):
        assert context.user_data["claude_session_id"] == "old-session"
        context.user_data["claude_session_id"] = "new-session"

    wrapped = orchestrator._inject_deps(dummy_handler)

    update = MagicMock()
    update.effective_chat.id = -1001234567890
    update.effective_message.message_thread_id = 777
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {
        "thread_state": {
            "-1001234567890:777": {
                "current_directory": str(project_path),
                "claude_session_id": "old-session",
            }
        }
    }

    await wrapped(update, context)

    assert (
        context.user_data["thread_state"]["-1001234567890:777"]["claude_session_id"]
        == "new-session"
    )


async def test_sync_threads_bypasses_thread_gate(group_thread_settings, deps):
    """sync_threads command bypasses strict thread routing gate."""
    orchestrator = MessageOrchestrator(group_thread_settings, deps)

    called = {"value": False}

    async def sync_threads(update, context):
        called["value"] = True

    project_threads_manager = MagicMock()
    project_threads_manager.guidance_message.return_value = "Use project thread"
    deps["project_threads_manager"] = project_threads_manager

    wrapped = orchestrator._inject_deps(sync_threads)

    update = MagicMock()
    update.effective_chat.id = -1002222222
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {}

    await wrapped(update, context)

    assert called["value"] is True


async def test_private_mode_start_bypasses_thread_gate(private_thread_settings, deps):
    """Private mode allows /start outside topics."""
    orchestrator = MessageOrchestrator(private_thread_settings, deps)
    called = {"value": False}

    async def start_command(update, context):
        called["value"] = True

    project_threads_manager = MagicMock()
    project_threads_manager.guidance_message.return_value = "Use project topic"
    deps["project_threads_manager"] = project_threads_manager

    wrapped = orchestrator._inject_deps(start_command)

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 12345
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {}

    await wrapped(update, context)

    assert called["value"] is True
    project_threads_manager.resolve_project.assert_not_called()


async def test_private_mode_start_inside_topic_uses_thread_context(
    private_thread_settings, deps
):
    """/start in private topic should load mapped thread context."""
    orchestrator = MessageOrchestrator(private_thread_settings, deps)
    project_path = private_thread_settings.approved_directory / "project_a"
    project = SimpleNamespace(
        slug="project_a",
        name="Project A",
        absolute_path=project_path,
    )
    project_threads_manager = MagicMock()
    project_threads_manager.resolve_project = AsyncMock(return_value=project)
    project_threads_manager.guidance_message.return_value = "Use project topic"
    deps["project_threads_manager"] = project_threads_manager

    captured = {"dir": None}

    async def start_command(update, context):
        captured["dir"] = context.user_data.get("current_directory")

    wrapped = orchestrator._inject_deps(start_command)

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 12345
    update.effective_message.message_thread_id = 777
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {
        "thread_state": {
            "12345:777": {
                "current_directory": str(project_path),
                "claude_session_id": "old",
            }
        }
    }

    await wrapped(update, context)

    project_threads_manager.resolve_project.assert_awaited_once_with(12345, 777)
    assert captured["dir"] == project_path


async def test_private_mode_rejects_help_outside_topics(private_thread_settings, deps):
    """Private mode rejects non-allowed commands outside mapped topics."""
    orchestrator = MessageOrchestrator(private_thread_settings, deps)
    called = {"value": False}

    async def help_command(update, context):
        called["value"] = True

    project_threads_manager = MagicMock()
    project_threads_manager.guidance_message.return_value = "Use project topic"
    deps["project_threads_manager"] = project_threads_manager

    wrapped = orchestrator._inject_deps(help_command)

    update = MagicMock()
    update.effective_chat.type = "private"
    update.effective_chat.id = 12345
    update.effective_message.message_thread_id = None
    update.effective_message.direct_messages_topic = None
    update.effective_message.reply_text = AsyncMock()
    update.callback_query = None

    context = MagicMock()
    context.bot_data = {}
    context.user_data = {}

    await wrapped(update, context)

    assert called["value"] is False
    update.effective_message.reply_text.assert_called_once()
