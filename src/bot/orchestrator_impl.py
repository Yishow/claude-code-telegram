"""Message orchestrator — single entry point for all Telegram updates.

Routes messages based on agentic vs classic mode. In agentic mode, provides
a minimal conversational interface (3 commands, no inline keyboards). In
classic mode, delegates to existing full-featured handlers.
"""

import asyncio
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import structlog
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..claude.sdk_integration import StreamUpdate
from ..config.settings import Settings
from ..projects import PrivateTopicsUnavailableError
from .copilot_runtime import (
    ONCE_MODEL_KEY,
    ONCE_PROVIDER_KEY,
    SESSION_MODEL_KEY,
    SESSION_PROVIDER_KEY,
    consume_request_controls,
    get_runtime_snapshot,
)
from .utils.html_format import escape_html

logger = structlog.get_logger()

# Patterns that look like secrets/credentials in CLI arguments
_SECRET_PATTERNS: List[re.Pattern[str]] = [
    # API keys / tokens (sk-ant-..., sk-..., ghp_..., gho_..., github_pat_..., xoxb-...)
    re.compile(
        r"(sk-ant-api\d*-[A-Za-z0-9_-]{10})[A-Za-z0-9_-]*"
        r"|(sk-[A-Za-z0-9_-]{20})[A-Za-z0-9_-]*"
        r"|(ghp_[A-Za-z0-9]{5})[A-Za-z0-9]*"
        r"|(gho_[A-Za-z0-9]{5})[A-Za-z0-9]*"
        r"|(github_pat_[A-Za-z0-9_]{5})[A-Za-z0-9_]*"
        r"|(xoxb-[A-Za-z0-9]{5})[A-Za-z0-9-]*"
    ),
    # AWS access keys
    re.compile(r"(AKIA[0-9A-Z]{4})[0-9A-Z]{12}"),
    # Generic long hex/base64 tokens after common flags/env patterns
    re.compile(
        r"((?:--token|--secret|--password|--api-key|--apikey|--auth)"
        r"[= ]+)['\"]?[A-Za-z0-9+/_.:-]{8,}['\"]?"
    ),
    # Inline env assignments like KEY=value
    re.compile(
        r"((?:TOKEN|SECRET|PASSWORD|API_KEY|APIKEY|AUTH_TOKEN|PRIVATE_KEY"
        r"|ACCESS_KEY|CLIENT_SECRET|WEBHOOK_SECRET)"
        r"=)['\"]?[^\s'\"]{8,}['\"]?"
    ),
    # Bearer / Basic auth headers
    re.compile(r"(Bearer )[A-Za-z0-9+/_.:-]{8,}" r"|(Basic )[A-Za-z0-9+/=]{8,}"),
    # Connection strings with credentials  user:pass@host
    re.compile(r"://([^:]+:)[^@]{4,}(@)"),
]


def _redact_secrets(text: str) -> str:
    """Replace likely secrets/credentials with redacted placeholders."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(
            lambda m: next((g + "***" for g in m.groups() if g is not None), "***"),
            result,
        )
    return result


# Tool name -> friendly emoji mapping for verbose output
_TOOL_ICONS: Dict[str, str] = {
    "Read": "\U0001f4d6",
    "Write": "\u270f\ufe0f",
    "Edit": "\u270f\ufe0f",
    "MultiEdit": "\u270f\ufe0f",
    "Bash": "\U0001f4bb",
    "Glob": "\U0001f50d",
    "Grep": "\U0001f50d",
    "LS": "\U0001f4c2",
    "Task": "\U0001f9e0",
    "TaskOutput": "\U0001f9e0",
    "WebFetch": "\U0001f310",
    "WebSearch": "\U0001f310",
    "NotebookRead": "\U0001f4d3",
    "NotebookEdit": "\U0001f4d3",
    "TodoRead": "\u2611\ufe0f",
    "TodoWrite": "\u2611\ufe0f",
}


def _tool_icon(name: str) -> str:
    """Return emoji for a tool, with a default wrench."""
    return _TOOL_ICONS.get(name, "\U0001f527")


class MessageOrchestrator:
    """Routes messages based on mode. Single entry point for all Telegram updates."""

    def __init__(self, settings: Settings, deps: Dict[str, Any]):
        self.settings = settings
        self.deps = deps

    def _inject_deps(self, handler: Callable) -> Callable:  # type: ignore[type-arg]
        """Wrap handler to inject dependencies into context.bot_data."""

        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            for key, value in self.deps.items():
                context.bot_data[key] = value
            context.bot_data["settings"] = self.settings
            context.user_data.pop("_thread_context", None)

            is_sync_bypass = handler.__name__ == "sync_threads"
            is_start_bypass = handler.__name__ in {"start_command", "agentic_start"}
            message_thread_id = self._extract_message_thread_id(update)
            should_enforce = self.settings.enable_project_threads

            if should_enforce:
                if self.settings.project_threads_mode == "private":
                    should_enforce = not is_sync_bypass and not (
                        is_start_bypass and message_thread_id is None
                    )
                else:
                    should_enforce = not is_sync_bypass

            if should_enforce:
                allowed = await self._apply_thread_routing_context(update, context)
                if not allowed:
                    return

            try:
                await handler(update, context)
            finally:
                if should_enforce:
                    self._persist_thread_state(context)

        return wrapped

    def _is_within_repo_root(self, path: Path) -> bool:
        """Return whether path stays inside approved directory root."""
        root = self.settings.approved_directory.resolve()
        try:
            path.resolve().relative_to(root)
            return True
        except ValueError:
            return False

    def _repo_relative_display(self, path: Path) -> str:
        """Render path relative to approved root for Telegram output."""
        root = self.settings.approved_directory.resolve()
        relative = path.resolve().relative_to(root)
        return "/" if str(relative) == "." else f"{relative}/"

    def _resolve_repo_target(self, selector: str, current_dir: Path) -> Optional[Path]:
        """Resolve /repo target from current directory while enforcing root boundary."""
        root = self.settings.approved_directory.resolve()
        safe_current = current_dir.resolve()
        if not self._is_within_repo_root(safe_current):
            safe_current = root

        selector = selector.strip()
        if selector == "/":
            candidate = root
        elif selector == "..":
            candidate = safe_current.parent
        elif selector.startswith("/"):
            candidate = root / selector.lstrip("/")
        else:
            candidate = safe_current / selector

        resolved = candidate.resolve()
        if not self._is_within_repo_root(resolved):
            return None
        return resolved

    async def _set_current_directory(
        self, context: ContextTypes.DEFAULT_TYPE, user_id: int, target_path: Path
    ) -> Optional[str]:
        """Switch current directory and auto-resume session if available."""
        context.user_data["current_directory"] = target_path

        claude_integration = context.bot_data.get("claude_integration")
        session_id = None
        if claude_integration:
            existing = await claude_integration._find_resumable_session(
                user_id, target_path
            )
            if existing:
                session_id = existing.session_id
        context.user_data["claude_session_id"] = session_id
        return session_id

    async def _apply_thread_routing_context(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """Enforce strict project-thread routing and load thread-local state."""
        manager = context.bot_data.get("project_threads_manager")
        if manager is None:
            await self._reject_for_thread_mode(
                update,
                "❌ <b>Project Thread Mode Misconfigured</b>\n\n"
                "Thread manager is not initialized.",
            )
            return False

        chat = update.effective_chat
        message = update.effective_message
        if not chat or not message:
            return False

        if self.settings.project_threads_mode == "group":
            if chat.id != self.settings.project_threads_chat_id:
                await self._reject_for_thread_mode(
                    update,
                    manager.guidance_message(mode=self.settings.project_threads_mode),
                )
                return False
        else:
            if getattr(chat, "type", "") != "private":
                await self._reject_for_thread_mode(
                    update,
                    manager.guidance_message(mode=self.settings.project_threads_mode),
                )
                return False

        message_thread_id = self._extract_message_thread_id(update)
        if not message_thread_id:
            await self._reject_for_thread_mode(
                update,
                manager.guidance_message(mode=self.settings.project_threads_mode),
            )
            return False

        project = await manager.resolve_project(chat.id, message_thread_id)
        if not project:
            await self._reject_for_thread_mode(
                update,
                manager.guidance_message(mode=self.settings.project_threads_mode),
            )
            return False

        state_key = f"{chat.id}:{message_thread_id}"
        thread_states = context.user_data.setdefault("thread_state", {})
        state = thread_states.get(state_key, {})

        project_root = project.absolute_path
        current_dir_raw = state.get("current_directory")
        current_dir = (
            Path(current_dir_raw).resolve() if current_dir_raw else project_root
        )
        if not self._is_within(current_dir, project_root) or not current_dir.is_dir():
            current_dir = project_root

        context.user_data["current_directory"] = current_dir
        context.user_data["claude_session_id"] = state.get("claude_session_id")
        context.user_data["_thread_context"] = {
            "chat_id": chat.id,
            "message_thread_id": message_thread_id,
            "state_key": state_key,
            "project_slug": project.slug,
            "project_root": str(project_root),
            "project_name": project.name,
        }
        return True

    def _persist_thread_state(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Persist compatibility keys back into per-thread state."""
        thread_context = context.user_data.get("_thread_context")
        if not thread_context:
            return

        project_root = Path(thread_context["project_root"])
        current_dir = context.user_data.get("current_directory", project_root)
        if not isinstance(current_dir, Path):
            current_dir = Path(str(current_dir))
        current_dir = current_dir.resolve()
        if not self._is_within(current_dir, project_root) or not current_dir.is_dir():
            current_dir = project_root

        thread_states = context.user_data.setdefault("thread_state", {})
        thread_states[thread_context["state_key"]] = {
            "current_directory": str(current_dir),
            "claude_session_id": context.user_data.get("claude_session_id"),
            "project_slug": thread_context["project_slug"],
        }

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        """Return True if path is within root."""
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _extract_message_thread_id(update: Update) -> Optional[int]:
        """Extract topic/thread id from update message for forum/direct topics."""
        message = update.effective_message
        if not message:
            return None
        message_thread_id = getattr(message, "message_thread_id", None)
        if isinstance(message_thread_id, int) and message_thread_id > 0:
            return message_thread_id
        dm_topic = getattr(message, "direct_messages_topic", None)
        topic_id = getattr(dm_topic, "topic_id", None) if dm_topic else None
        if isinstance(topic_id, int) and topic_id > 0:
            return topic_id
        return None

    def _interaction_scope(
        self,
        *,
        update: Optional[Update] = None,
        context: Optional[ContextTypes.DEFAULT_TYPE] = None,
    ) -> Tuple[int, int, Optional[int]]:
        """Return (user_id, chat_id, message_thread_id) for interaction scoping."""
        user_id = 0
        chat_id = 0
        message_thread_id: Optional[int] = None

        if update and update.effective_user:
            user_id = update.effective_user.id
        if update and update.effective_chat:
            chat_id = update.effective_chat.id
        if update:
            message_thread_id = self._extract_message_thread_id(update)

        if context and not message_thread_id:
            thread_context = context.user_data.get("_thread_context", {})
            thread_id = thread_context.get("message_thread_id")
            if isinstance(thread_id, int):
                message_thread_id = thread_id

        return user_id, chat_id, message_thread_id

    @staticmethod
    def _get_claude_integration(context: ContextTypes.DEFAULT_TYPE) -> Any:
        return context.bot_data.get("claude_integration")

    def _get_interaction_bridge(self, context: ContextTypes.DEFAULT_TYPE) -> Any:
        integration = self._get_claude_integration(context)
        if not integration:
            return None
        manager = getattr(integration, "copilot_manager", None)
        return getattr(manager, "interaction_bridge", None)

    async def _reject_for_thread_mode(self, update: Update, message: str) -> None:
        """Send a guidance response when strict thread routing rejects an update."""
        query = update.callback_query
        if query:
            try:
                await query.answer()
            except Exception:
                pass
            if query.message:
                await query.message.reply_text(message, parse_mode="HTML")
            return

        if update.effective_message:
            await update.effective_message.reply_text(message, parse_mode="HTML")

    def register_handlers(self, app: Application) -> None:
        """Register handlers based on mode."""
        if self.settings.agentic_mode:
            self._register_agentic_handlers(app)
        else:
            self._register_classic_handlers(app)

    def _register_agentic_handlers(self, app: Application) -> None:
        """Register agentic handlers: commands + text/file/photo."""
        from .handlers import command

        # Commands
        handlers = [
            ("start", self.agentic_start),
            ("new", self.agentic_new),
            ("status", self.agentic_status),
            ("session_name", command.session_name_command),
            ("verbose", self.agentic_verbose),
            ("memory", self.agentic_memory),
            ("repo", self.agentic_repo),
            ("provider", self.agentic_provider),
            ("model", self.agentic_model),
            ("copilot", self.agentic_copilot),
        ]
        if self.settings.enable_project_threads:
            handlers.append(("sync_threads", command.sync_threads))

        for cmd, handler in handlers:
            app.add_handler(CommandHandler(cmd, self._inject_deps(handler)))

        # Text messages -> Claude
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._inject_deps(self.agentic_text),
            ),
            group=10,
        )

        # File uploads -> Claude
        app.add_handler(
            MessageHandler(
                filters.Document.ALL, self._inject_deps(self.agentic_document)
            ),
            group=10,
        )

        # Photo uploads -> Claude
        app.add_handler(
            MessageHandler(filters.PHOTO, self._inject_deps(self.agentic_photo)),
            group=10,
        )

        # Only cd: callbacks (for project selection), scoped by pattern
        app.add_handler(
            CallbackQueryHandler(
                self._inject_deps(self._agentic_callback),
                pattern=r"^cd:",
            )
        )
        app.add_handler(
            CallbackQueryHandler(
                self._inject_deps(self._agentic_memory_callback),
                pattern=r"^memory:",
            )
        )

        # ask_user: inline button choices from Copilot mid-execution questions
        app.add_handler(
            CallbackQueryHandler(
                self._inject_deps(self._ask_user_callback),
                pattern=r"^ask_user:",
            )
        )

        # perm: Approve/Deny for Copilot permission requests
        app.add_handler(
            CallbackQueryHandler(
                self._inject_deps(self._permission_callback),
                pattern=r"^perm:",
            )
        )

        logger.info("Agentic handlers registered")

    def _register_classic_handlers(self, app: Application) -> None:
        """Register full classic handler set (moved from core.py)."""
        from .handlers import callback, command, message

        handlers = [
            ("start", command.start_command),
            ("help", command.help_command),
            ("new", command.new_session),
            ("continue", command.continue_session),
            ("end", command.end_session),
            ("ls", command.list_files),
            ("cd", command.change_directory),
            ("pwd", command.print_working_directory),
            ("projects", command.show_projects),
            ("status", command.session_status),
            ("session_name", command.session_name_command),
            ("memory", command.memory_command),
            ("provider", command.provider_command),
            ("model", command.model_command),
            ("copilot", command.copilot_command),
            ("export", command.export_session),
            ("actions", command.quick_actions),
            ("git", command.git_command),
        ]
        if self.settings.enable_project_threads:
            handlers.append(("sync_threads", command.sync_threads))

        for cmd, handler in handlers:
            app.add_handler(CommandHandler(cmd, self._inject_deps(handler)))

        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._inject_deps(message.handle_text_message),
            ),
            group=10,
        )
        app.add_handler(
            MessageHandler(
                filters.Document.ALL, self._inject_deps(message.handle_document)
            ),
            group=10,
        )
        app.add_handler(
            MessageHandler(filters.PHOTO, self._inject_deps(message.handle_photo)),
            group=10,
        )
        app.add_handler(
            CallbackQueryHandler(self._inject_deps(callback.handle_callback_query))
        )

        logger.info("Classic handlers registered (18 commands + full handler set)")

    async def get_bot_commands(self) -> list:  # type: ignore[type-arg]
        """Return bot commands appropriate for current mode."""
        if self.settings.agentic_mode:
            commands = [
                BotCommand("start", "Start the bot"),
                BotCommand("new", "Start a fresh session"),
                BotCommand("status", "Show session status"),
                BotCommand("session_name", "Name/reset current session"),
                BotCommand("verbose", "Set output verbosity (0/1/2)"),
                BotCommand("memory", "Memory system controls"),
                BotCommand("repo", "List repos / switch workspace"),
                BotCommand("provider", "Switch provider (claude/copilot)"),
                BotCommand("model", "Switch AI model (Copilot provider)"),
                BotCommand("copilot", "Copilot status/control commands"),
            ]
            if self.settings.enable_project_threads:
                commands.append(BotCommand("sync_threads", "Sync project topics"))
            return commands
        else:
            commands = [
                BotCommand("start", "Start bot and show help"),
                BotCommand("help", "Show available commands"),
                BotCommand("new", "Clear context and start fresh session"),
                BotCommand("continue", "Explicitly continue last session"),
                BotCommand("end", "End current session and clear context"),
                BotCommand("ls", "List files in current directory"),
                BotCommand("cd", "Change directory (resumes project session)"),
                BotCommand("pwd", "Show current directory"),
                BotCommand("projects", "Show all projects"),
                BotCommand("status", "Show session status"),
                BotCommand("session_name", "Name/reset current session"),
                BotCommand("memory", "Memory system controls"),
                BotCommand("provider", "Switch provider (claude/copilot)"),
                BotCommand("model", "Switch Copilot model"),
                BotCommand("copilot", "Copilot status/control commands"),
                BotCommand("export", "Export current session"),
                BotCommand("actions", "Show quick actions"),
                BotCommand("git", "Git repository commands"),
            ]
            if self.settings.enable_project_threads:
                commands.append(BotCommand("sync_threads", "Sync project topics"))
            return commands

    # --- Agentic handlers ---

    async def agentic_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Brief welcome, no buttons."""
        user = update.effective_user
        sync_line = ""
        if (
            self.settings.enable_project_threads
            and self.settings.project_threads_mode == "private"
        ):
            if (
                not update.effective_chat
                or getattr(update.effective_chat, "type", "") != "private"
            ):
                await update.message.reply_text(
                    "🚫 <b>Private Topics Mode</b>\n\n"
                    "Use this bot in a private chat and run <code>/start</code> there.",
                    parse_mode="HTML",
                )
                return
            manager = context.bot_data.get("project_threads_manager")
            if manager:
                try:
                    result = await manager.sync_topics(
                        context.bot,
                        chat_id=update.effective_chat.id,
                    )
                    sync_line = (
                        "\n\n🧵 Topics synced"
                        f" (created {result.created}, reused {result.reused})."
                    )
                except PrivateTopicsUnavailableError:
                    await update.message.reply_text(
                        manager.private_topics_unavailable_message(),
                        parse_mode="HTML",
                    )
                    return
                except Exception:
                    sync_line = "\n\n🧵 Topic sync failed. Run /sync_threads to retry."
        current_dir = context.user_data.get(
            "current_directory", self.settings.approved_directory
        )
        dir_display = f"<code>{current_dir}/</code>"

        safe_name = escape_html(user.first_name)
        await update.message.reply_text(
            f"Hi {safe_name}! I'm your AI coding assistant.\n"
            f"Just tell me what you need — I can read, write, and run code.\n\n"
            f"Working in: {dir_display}\n"
            f"Commands: /new (reset) · /status · /session_name · /memory · /provider · /copilot"
            f"{sync_line}",
            parse_mode="HTML",
        )

    async def agentic_new(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Reset session, one-line confirmation."""
        context.user_data["claude_session_id"] = None
        context.user_data["session_started"] = True
        context.user_data["force_new_session"] = True

        await update.message.reply_text("Session reset. What's next?")

    async def agentic_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Compact one-line status, no buttons."""
        current_dir = context.user_data.get(
            "current_directory", self.settings.approved_directory
        )
        dir_display = str(current_dir)

        session_id = context.user_data.get("claude_session_id")
        session_status = "active" if session_id else "none"
        snapshot = get_runtime_snapshot(self.settings, context.user_data)

        # Cost info
        cost_str = ""
        rate_limiter = context.bot_data.get("rate_limiter")
        if rate_limiter:
            try:
                user_status = rate_limiter.get_user_status(update.effective_user.id)
                cost_usage = user_status.get("cost_usage", {})
                current_cost = cost_usage.get("current", 0.0)
                cost_str = f" · Cost: ${current_cost:.2f}"
            except Exception:
                pass

        await update.message.reply_text(
            f"📂 {dir_display} · Session: {session_status}"
            f" · Provider: {snapshot['provider']}"
            f" · Model: {snapshot['model']}"
            f" · Fallback: {snapshot['fallback_mode']}"
            f"{cost_str}"
        )

    def _get_verbose_level(self, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Return effective verbose level: per-user override or global default."""
        user_override = context.user_data.get("verbose_level")
        if user_override is not None:
            return int(user_override)
        return self.settings.verbose_level

    async def agentic_verbose(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Set output verbosity: /verbose [0|1|2]."""
        args = update.message.text.split()[1:] if update.message.text else []
        if not args:
            current = self._get_verbose_level(context)
            labels = {0: "quiet", 1: "normal", 2: "detailed"}
            await update.message.reply_text(
                f"Verbosity: <b>{current}</b> ({labels.get(current, '?')})\n\n"
                "Usage: <code>/verbose 0|1|2</code>\n"
                "  0 = quiet (final response only)\n"
                "  1 = normal (tools + reasoning)\n"
                "  2 = detailed (tools with inputs + reasoning)",
                parse_mode="HTML",
            )
            return

        try:
            level = int(args[0])
            if level not in (0, 1, 2):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Please use: /verbose 0, /verbose 1, or /verbose 2"
            )
            return

        context.user_data["verbose_level"] = level
        labels = {0: "quiet", 1: "normal", 2: "detailed"}
        await update.message.reply_text(
            f"Verbosity set to <b>{level}</b> ({labels[level]})",
            parse_mode="HTML",
        )

    async def agentic_memory(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Memory runtime controls command (shared with classic handler)."""
        from .handlers import command  # noqa: PLC0415

        await command.memory_command(update, context)

    async def agentic_model(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Switch Copilot model: /model <model_name> [once]."""
        from ..claude.copilot_integration import COPILOT_MODELS  # noqa: PLC0415

        args = context.args or []

        if not args:
            current = context.user_data.get(
                SESSION_MODEL_KEY, self.settings.copilot_model
            )
            model_list = "\n".join(f"  <code>{m}</code>" for m in COPILOT_MODELS)
            await update.message.reply_text(
                f"Current model: <code>{escape_html(current)}</code>\n\n"
                f"<b>Available models:</b>\n{model_list}\n\n"
                "Usage: <code>/model &lt;model_name&gt; [once]</code>",
                parse_mode="HTML",
            )
            return

        requested = args[0].strip()
        if requested not in COPILOT_MODELS:
            await update.message.reply_text(
                f"Unknown model: <code>{escape_html(requested)}</code>\n"
                "Use <code>/model</code> to list available models.",
                parse_mode="HTML",
            )
            return

        once = self._is_once_override(args)
        self._set_session_or_once_override(
            context,
            once=once,
            once_key=ONCE_MODEL_KEY,
            session_key=SESSION_MODEL_KEY,
            value=requested,
        )
        await update.message.reply_text(
            (
                f"Model one-shot override set to <code>{escape_html(requested)}</code>"
                if once
                else f"Model switched to <code>{escape_html(requested)}</code>"
            ),
            parse_mode="HTML",
        )

    async def agentic_provider(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Switch provider: /provider <claude|copilot> [once]."""
        args = context.args or []
        current = context.user_data.get(
            SESSION_PROVIDER_KEY, self.settings.default_provider
        )

        if not args:
            await update.message.reply_text(
                f"Current provider: <code>{escape_html(current)}</code>\n\n"
                "Usage: <code>/provider claude|copilot [once]</code>",
                parse_mode="HTML",
            )
            return

        requested = args[0].strip().lower()
        if requested not in {"claude", "copilot"}:
            await update.message.reply_text(
                "Provider must be <code>claude</code> or <code>copilot</code>.",
                parse_mode="HTML",
            )
            return

        once = self._is_once_override(args)
        self._set_session_or_once_override(
            context,
            once=once,
            once_key=ONCE_PROVIDER_KEY,
            session_key=SESSION_PROVIDER_KEY,
            value=requested,
        )
        text = (
            f"Provider one-shot override set to <code>{requested}</code>"
            if once
            else f"Provider switched to <code>{requested}</code>"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def agentic_copilot(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Copilot control-plane command."""
        message = update.effective_message
        if message is None:
            logger.warning("Missing effective message for /copilot command")
            return

        claude_integration = self._get_claude_integration(context)
        if not claude_integration:
            await message.reply_text("Claude integration not available.")
            return

        from . import orchestrator as orchestrator_module

        text, parse_mode = await orchestrator_module.run_copilot_control_command(
            args=context.args or [],
            settings=self.settings,
            user_data=context.user_data,
            claude_integration=claude_integration,
            user_id=update.effective_user.id if update.effective_user else 0,
            working_directory=Path(
                context.user_data.get(
                    "current_directory",
                    self.settings.approved_directory,
                )
            ),
        )
        if parse_mode:
            await message.reply_text(text, parse_mode=parse_mode)
        else:
            await message.reply_text(text)

    @staticmethod
    def _is_once_override(args: List[str]) -> bool:
        """Return whether command args request a one-shot override."""
        return len(args) > 1 and args[1].strip().lower() in {"once", "--once", "-o"}

    @staticmethod
    def _set_session_or_once_override(
        context: ContextTypes.DEFAULT_TYPE,
        *,
        once: bool,
        once_key: str,
        session_key: str,
        value: str,
    ) -> None:
        """Persist an override to session scope or one-shot scope."""
        if once:
            context.user_data[once_key] = value
            return
        context.user_data[session_key] = value

    async def _run_command_with_controls(
        self,
        claude_integration: Any,
        *,
        prompt: str,
        working_directory: Path,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        session_id: Optional[str],
        on_stream: Optional[Callable[[StreamUpdate], Any]],
        force_new: bool,
        effective_controls: Dict[str, Any],
        image_path: Optional[str] = None,
    ) -> Any:
        """Run provider request using normalized control payload."""
        kwargs: Dict[str, Any] = {
            "prompt": prompt,
            "working_directory": working_directory,
            "user_id": user_id,
            "chat_id": chat_id,
            "message_thread_id": message_thread_id,
            "session_id": session_id,
            "on_stream": on_stream,
            "force_new": force_new,
            "provider": effective_controls["provider"],
            "copilot_model": effective_controls["copilot_model"],
            "reasoning_effort": effective_controls["reasoning_effort"],
            "skill_directories": effective_controls["skill_directories"],
            "disabled_skills": effective_controls["disabled_skills"],
            "mcp_env_value_mode": effective_controls["mcp_env_value_mode"],
            "external_cli_server": effective_controls["external_cli_server"],
        }
        if image_path:
            kwargs["image_path"] = image_path
        return await claude_integration.run_command(**kwargs)

    def _format_verbose_progress(
        self,
        activity_log: List[Dict[str, Any]],
        verbose_level: int,
        start_time: float,
    ) -> str:
        """Build the progress message text based on activity so far."""
        if not activity_log:
            return "Working..."

        elapsed = time.time() - start_time
        lines: List[str] = [f"Working... ({elapsed:.0f}s)\n"]

        for entry in activity_log[-15:]:  # Show last 15 entries max
            kind = entry.get("kind", "tool")
            if kind == "text":
                # Claude's intermediate reasoning/commentary
                snippet = entry.get("detail", "")
                if verbose_level >= 2:
                    lines.append(f"\U0001f4ac {snippet}")
                else:
                    # Level 1: one short line
                    lines.append(f"\U0001f4ac {snippet[:80]}")
            else:
                # Tool call
                icon = _tool_icon(entry["name"])
                if verbose_level >= 2 and entry.get("detail"):
                    lines.append(f"{icon} {entry['name']}: {entry['detail']}")
                else:
                    lines.append(f"{icon} {entry['name']}")

        if len(activity_log) > 15:
            lines.insert(1, f"... ({len(activity_log) - 15} earlier entries)\n")

        return "\n".join(lines)

    @staticmethod
    def _summarize_tool_input(tool_name: str, tool_input: Dict[str, Any]) -> str:
        """Return a short summary of tool input for verbose level 2."""
        if not tool_input:
            return ""
        if tool_name in ("Read", "Write", "Edit", "MultiEdit"):
            path = tool_input.get("file_path") or tool_input.get("path", "")
            if path:
                # Show just the filename, not the full path
                return path.rsplit("/", 1)[-1]
        if tool_name in ("Glob", "Grep"):
            pattern = tool_input.get("pattern", "")
            if pattern:
                return pattern[:60]
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            if cmd:
                return _redact_secrets(cmd[:100])[:80]
        if tool_name in ("WebFetch", "WebSearch"):
            return (tool_input.get("url", "") or tool_input.get("query", ""))[:60]
        if tool_name == "Task":
            desc = tool_input.get("description", "")
            if desc:
                return desc[:60]
        # Generic: show first key's value
        for v in tool_input.values():
            if isinstance(v, str) and v:
                return v[:60]
        return ""

    @staticmethod
    def _start_typing_heartbeat(
        chat: Any,
        interval: float = 2.0,
    ) -> "asyncio.Task[None]":
        """Start a background typing indicator task.

        Sends typing every *interval* seconds, independently of
        stream events. Cancel the returned task in a ``finally``
        block.
        """

        async def _heartbeat() -> None:
            try:
                while True:
                    await asyncio.sleep(interval)
                    try:
                        await chat.send_action("typing")
                    except Exception:
                        pass
            except asyncio.CancelledError:
                pass

        return asyncio.create_task(_heartbeat())

    def _make_stream_callback(
        self,
        verbose_level: int,
        progress_msg: Any,
        tool_log: List[Dict[str, Any]],
        start_time: float,
        context: Optional[ContextTypes.DEFAULT_TYPE] = None,
    ) -> Optional[Callable[[StreamUpdate], Any]]:
        """Create a stream callback for verbose progress updates.

        Returns None when verbose_level is 0 (nothing to display).
        Typing indicators are handled by a separate heartbeat task.
        ``context`` is required for ask_user support regardless of verbose_level.
        """
        # If verbose_level is 0 but context is provided, we still need a callback
        # to handle ask_user requests from the Copilot provider.
        if verbose_level == 0 and context is None:
            return None

        last_edit_time = [0.0]  # mutable container for closure

        async def _on_stream(update_obj: StreamUpdate) -> None:
            # --- ask_user: Copilot needs input from the user mid-execution ---
            if update_obj.type == "ask_user" and context is not None:
                meta = update_obj.metadata or {}
                interaction_id = str(meta.get("interaction_id") or "")
                choices: List[str] = meta.get("choices") or []
                allow_freeform: bool = bool(meta.get("allow_freeform", True))
                question = update_obj.content or "Copilot needs more information:"

                if interaction_id:
                    context.user_data["pending_ask_user_interaction_id"] = (
                        interaction_id
                    )

                reply_markup = None
                if choices and interaction_id:
                    keyboard = [
                        [
                            InlineKeyboardButton(
                                c, callback_data=f"ask_user:{interaction_id}:{idx}"
                            )
                        ]
                        for idx, c in enumerate(choices)
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                suffix = ""
                if allow_freeform:
                    suffix = "\n\nReply with text to answer, or use a choice button."

                try:
                    await progress_msg.edit_text(
                        f"❓ <b>Copilot asks:</b>\n{escape_html(question)}{suffix}",
                        parse_mode="HTML",
                        reply_markup=reply_markup,
                    )
                except Exception:
                    pass
                return

            # --- permission_request: Copilot wants to perform a privileged action ---
            if update_obj.type == "permission_request" and context is not None:
                meta = update_obj.metadata or {}
                interaction_id = str(meta.get("interaction_id") or "")
                kind = meta.get("kind") or update_obj.content or "unknown"

                _PERM_ICONS = {
                    "shell": "💻",
                    "write": "✏️",
                    "read": "📖",
                    "mcp": "🔌",
                    "url": "🌐",
                }
                icon = _PERM_ICONS.get(kind, "🔐")

                if interaction_id:
                    context.user_data["pending_permission_interaction_id"] = (
                        interaction_id
                    )

                keyboard = [
                    [
                        InlineKeyboardButton(
                            "✅ Approve", callback_data=f"perm:{interaction_id}:approve"
                        ),
                        InlineKeyboardButton(
                            "❌ Deny", callback_data=f"perm:{interaction_id}:deny"
                        ),
                    ]
                ]
                try:
                    await progress_msg.edit_text(
                        f"{icon} <b>Permission request:</b> <code>{escape_html(kind)}</code>\n"
                        "Allow Copilot to perform this action?",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                except Exception:
                    pass
                return

            if update_obj.type == "context_changed":
                tool_log.append(
                    {
                        "kind": "text",
                        "detail": "⚠️ Copilot context changed; check /copilot status.",
                    }
                )
                return

            if verbose_level == 0:
                return

            # Copilot tool event (type="tool", from event_handler in sdk_integration)
            if update_obj.type == "tool":
                meta = update_obj.metadata or {}
                name = meta.get("tool_name") or update_obj.content or "unknown"
                if meta.get("action") == "pre" and verbose_level >= 1:
                    detail = self._summarize_tool_input(
                        name, meta.get("tool_args") or {}
                    )
                    tool_log.append({"kind": "tool", "name": name, "detail": detail})

            # Reasoning delta (VERBOSE_LEVEL >= 2 only)
            elif update_obj.type == "reasoning" and update_obj.content:
                if verbose_level >= 2:
                    text = update_obj.content.strip()
                    first_line = text.split("\n", 1)[0].strip()
                    if first_line:
                        tool_log.append(
                            {"kind": "text", "detail": f"🤔 {first_line[:120]}"}
                        )

            # Capture tool calls (Claude SDK path, tool_calls field)
            if update_obj.tool_calls:
                for tc in update_obj.tool_calls:
                    name = tc.get("name", "unknown")
                    detail = self._summarize_tool_input(name, tc.get("input", {}))
                    tool_log.append({"kind": "tool", "name": name, "detail": detail})

            # Capture assistant text (reasoning / commentary, Claude SDK path)
            if update_obj.type == "assistant" and update_obj.content:
                text = update_obj.content.strip()
                if text and verbose_level >= 1:
                    # Collapse to first meaningful line, cap length
                    first_line = text.split("\n", 1)[0].strip()
                    if first_line:
                        tool_log.append({"kind": "text", "detail": first_line[:120]})

            # Throttle progress message edits to avoid Telegram rate limits
            now = time.time()
            if (now - last_edit_time[0]) >= 2.0 and tool_log:
                last_edit_time[0] = now
                new_text = self._format_verbose_progress(
                    tool_log, verbose_level, start_time
                )
                try:
                    await progress_msg.edit_text(new_text)
                except Exception:
                    pass

        return _on_stream

    async def agentic_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Direct Claude passthrough. Simple progress. No suggestions."""
        user_id = update.effective_user.id
        message_text = update.message.text
        raw_prompt = message_text
        chat_id = update.effective_chat.id if update.effective_chat else 0
        message_thread_id = self._extract_message_thread_id(update)

        # --- ask_user freeform: consume next message if interaction is pending ---
        bridge = self._get_interaction_bridge(context)
        if bridge:
            scope_user, scope_chat, scope_thread = self._interaction_scope(
                update=update, context=context
            )
            resolved_id = await bridge.resolve_pending_freeform(
                user_id=scope_user,
                chat_id=scope_chat,
                message_thread_id=scope_thread,
                value=message_text,
            )
            if isinstance(resolved_id, str) and resolved_id:
                context.user_data.pop("pending_ask_user_interaction_id", None)
                logger.info(
                    "Resolved pending ask_user via freeform reply",
                    user_id=user_id,
                    interaction_id=resolved_id,
                )
                await update.message.reply_text(
                    "✅ Answer recorded. Continuing execution..."
                )
                return

        logger.info(
            "Agentic text message",
            user_id=user_id,
            message_length=len(message_text),
        )

        # Rate limit check
        rate_limiter = context.bot_data.get("rate_limiter")
        if rate_limiter:
            allowed, limit_message = await rate_limiter.check_rate_limit(user_id, 0.001)
            if not allowed:
                await update.message.reply_text(f"⏱️ {limit_message}")
                return

        chat = update.message.chat
        await chat.send_action("typing")

        verbose_level = self._get_verbose_level(context)
        progress_msg = await update.message.reply_text("Working...")

        claude_integration = context.bot_data.get("claude_integration")
        if not claude_integration:
            await progress_msg.edit_text(
                "Claude integration not available. Check configuration."
            )
            return

        current_dir = context.user_data.get(
            "current_directory", self.settings.approved_directory
        )
        if not isinstance(current_dir, Path):
            current_dir = Path(current_dir)
        session_id = context.user_data.get("claude_session_id")

        # Check if /new was used — skip auto-resume for this first message.
        # Flag is only cleared after a successful run so retries keep the intent.
        force_new = bool(context.user_data.get("force_new_session"))

        # --- Verbose progress tracking via stream callback ---
        tool_log: List[Dict[str, Any]] = []
        start_time = time.time()
        on_stream = self._make_stream_callback(
            verbose_level, progress_msg, tool_log, start_time, context=context
        )

        controls = consume_request_controls(self.settings, context.user_data)
        effective_prompt = message_text
        effective_controls = controls
        memory_runtime_settings = None
        memory_service = context.bot_data.get("memory_service")
        if memory_service:
            try:
                pre_hook_result = await memory_service.apply_pre_hook(
                    prompt=message_text,
                    controls=controls,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    project_path=current_dir,
                )
                effective_prompt = pre_hook_result.prompt
                effective_controls = pre_hook_result.controls
                memory_runtime_settings = pre_hook_result.runtime_settings
            except Exception as memory_error:
                logger.warning(
                    "Memory pre-hook failed, using original prompt",
                    user_id=user_id,
                    error=str(memory_error),
                )

        # Independent typing heartbeat — stays alive even with no stream events
        heartbeat = self._start_typing_heartbeat(chat)

        success = True
        request_started_at = time.time()
        response_content = ""
        response_session_id = session_id
        try:
            claude_response = await self._run_command_with_controls(
                claude_integration,
                prompt=effective_prompt,
                working_directory=current_dir,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                session_id=session_id,
                on_stream=on_stream,
                force_new=force_new,
                effective_controls=effective_controls,
            )

            # New session created successfully — clear the one-shot flag
            if force_new:
                context.user_data["force_new_session"] = False

            context.user_data["claude_session_id"] = claude_response.session_id
            response_content = claude_response.content
            response_session_id = claude_response.session_id

            # Track directory changes
            from .handlers.message import _update_working_directory_from_claude_response

            _update_working_directory_from_claude_response(
                claude_response, context, self.settings, user_id
            )

            # Store interaction
            storage = context.bot_data.get("storage")
            if storage:
                try:
                    await storage.save_claude_interaction(
                        user_id=user_id,
                        session_id=claude_response.session_id,
                        prompt=message_text,
                        response=claude_response,
                        ip_address=None,
                    )
                except Exception as e:
                    logger.warning("Failed to log interaction", error=str(e))

            # Format response (no reply_markup — strip keyboards)
            from .utils.formatting import ResponseFormatter

            formatter = ResponseFormatter(self.settings)
            formatted_messages = formatter.format_claude_response(
                claude_response.content
            )

        except Exception as e:
            success = False
            logger.error("Claude integration failed", error=str(e), user_id=user_id)
            from .handlers.message import _format_error_message
            from .utils.formatting import FormattedMessage

            formatted_messages = [
                FormattedMessage(_format_error_message(e), parse_mode="HTML")
            ]
        finally:
            heartbeat.cancel()
            if memory_service:
                try:
                    await memory_service.apply_post_hook(
                        prompt=raw_prompt,
                        response=response_content,
                        user_id=user_id,
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        project_path=current_dir,
                        source_session_id=response_session_id,
                        source_message_id=(
                            update.message.message_id if update.message else None
                        ),
                        runtime_settings=memory_runtime_settings,
                        success=success,
                        elapsed_ms=int((time.time() - request_started_at) * 1000),
                    )
                except Exception as memory_error:
                    logger.warning(
                        "Memory post-hook failed",
                        user_id=user_id,
                        error=str(memory_error),
                    )

        await progress_msg.delete()

        for i, message in enumerate(formatted_messages):
            if not message.text or not message.text.strip():
                continue
            try:
                await update.message.reply_text(
                    message.text,
                    parse_mode=message.parse_mode,
                    reply_markup=None,  # No keyboards in agentic mode
                    reply_to_message_id=(update.message.message_id if i == 0 else None),
                )
                if i < len(formatted_messages) - 1:
                    await asyncio.sleep(0.5)
            except Exception as send_err:
                logger.warning(
                    "Failed to send HTML response, retrying as plain text",
                    error=str(send_err),
                    message_index=i,
                )
                try:
                    await update.message.reply_text(
                        message.text,
                        reply_markup=None,
                        reply_to_message_id=(
                            update.message.message_id if i == 0 else None
                        ),
                    )
                except Exception as plain_err:
                    await update.message.reply_text(
                        f"Failed to deliver response "
                        f"(Telegram error: {str(plain_err)[:150]}). "
                        f"Please try again.",
                        reply_to_message_id=(
                            update.message.message_id if i == 0 else None
                        ),
                    )

        # Audit log
        audit_logger = context.bot_data.get("audit_logger")
        if audit_logger:
            await audit_logger.log_command(
                user_id=user_id,
                command="text_message",
                args=[message_text[:100]],
                success=success,
            )

    async def agentic_document(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Process file upload -> Claude, minimal chrome."""
        user_id = update.effective_user.id
        document = update.message.document

        logger.info(
            "Agentic document upload",
            user_id=user_id,
            filename=document.file_name,
        )

        # Security validation
        security_validator = context.bot_data.get("security_validator")
        if security_validator:
            valid, error = security_validator.validate_filename(document.file_name)
            if not valid:
                await update.message.reply_text(f"File rejected: {error}")
                return

        # Size check
        max_size = 10 * 1024 * 1024
        if document.file_size > max_size:
            await update.message.reply_text(
                f"File too large ({document.file_size / 1024 / 1024:.1f}MB). Max: 10MB."
            )
            return

        chat = update.message.chat
        await chat.send_action("typing")
        progress_msg = await update.message.reply_text("Working...")

        # Try enhanced file handler, fall back to basic
        features = context.bot_data.get("features")
        file_handler = features.get_file_handler() if features else None
        prompt: Optional[str] = None

        if file_handler:
            try:
                processed_file = await file_handler.handle_document_upload(
                    document,
                    user_id,
                    update.message.caption or "Please review this file:",
                )
                prompt = processed_file.prompt
            except Exception:
                file_handler = None

        if not file_handler:
            file = await document.get_file()
            file_bytes = await file.download_as_bytearray()
            try:
                content = file_bytes.decode("utf-8")
                if len(content) > 50000:
                    content = content[:50000] + "\n... (truncated)"
                caption = update.message.caption or "Please review this file:"
                prompt = (
                    f"{caption}\n\n**File:** `{document.file_name}`\n\n"
                    f"```\n{content}\n```"
                )
            except UnicodeDecodeError:
                await progress_msg.edit_text(
                    "Unsupported file format. Must be text-based (UTF-8)."
                )
                return

        # Process with Claude
        claude_integration = context.bot_data.get("claude_integration")
        if not claude_integration:
            await progress_msg.edit_text(
                "Claude integration not available. Check configuration."
            )
            return

        current_dir = context.user_data.get(
            "current_directory", self.settings.approved_directory
        )
        if not isinstance(current_dir, Path):
            current_dir = Path(current_dir)
        session_id = context.user_data.get("claude_session_id")
        chat_id = update.effective_chat.id if update.effective_chat else 0
        message_thread_id = self._extract_message_thread_id(update)

        # Check if /new was used — skip auto-resume for this first message.
        # Flag is only cleared after a successful run so retries keep the intent.
        force_new = bool(context.user_data.get("force_new_session"))

        verbose_level = self._get_verbose_level(context)
        tool_log: List[Dict[str, Any]] = []
        on_stream = self._make_stream_callback(
            verbose_level, progress_msg, tool_log, time.time(), context=context
        )
        controls = consume_request_controls(self.settings, context.user_data)
        raw_prompt = prompt
        effective_prompt = prompt
        effective_controls = controls
        memory_runtime_settings = None
        memory_service = context.bot_data.get("memory_service")
        if memory_service:
            try:
                pre_hook_result = await memory_service.apply_pre_hook(
                    prompt=prompt,
                    controls=controls,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    project_path=current_dir,
                )
                effective_prompt = pre_hook_result.prompt
                effective_controls = pre_hook_result.controls
                memory_runtime_settings = pre_hook_result.runtime_settings
            except Exception as memory_error:
                logger.warning(
                    "Memory pre-hook failed for document",
                    user_id=user_id,
                    error=str(memory_error),
                )

        heartbeat = self._start_typing_heartbeat(chat)
        success = True
        request_started_at = time.time()
        response_content = ""
        response_session_id = session_id
        try:
            claude_response = await self._run_command_with_controls(
                claude_integration,
                prompt=effective_prompt,
                working_directory=current_dir,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                session_id=session_id,
                on_stream=on_stream,
                force_new=force_new,
                effective_controls=effective_controls,
            )

            if force_new:
                context.user_data["force_new_session"] = False

            context.user_data["claude_session_id"] = claude_response.session_id
            response_content = claude_response.content
            response_session_id = claude_response.session_id

            from .handlers.message import _update_working_directory_from_claude_response

            _update_working_directory_from_claude_response(
                claude_response, context, self.settings, user_id
            )

            from .utils.formatting import ResponseFormatter

            formatter = ResponseFormatter(self.settings)
            formatted_messages = formatter.format_claude_response(
                claude_response.content
            )

            await progress_msg.delete()

            for i, message in enumerate(formatted_messages):
                await update.message.reply_text(
                    message.text,
                    parse_mode=message.parse_mode,
                    reply_markup=None,
                    reply_to_message_id=(update.message.message_id if i == 0 else None),
                )
                if i < len(formatted_messages) - 1:
                    await asyncio.sleep(0.5)

        except Exception as e:
            success = False
            from .handlers.message import _format_error_message

            await progress_msg.edit_text(_format_error_message(e), parse_mode="HTML")
            logger.error("Claude file processing failed", error=str(e), user_id=user_id)
        finally:
            heartbeat.cancel()
            if memory_service:
                try:
                    await memory_service.apply_post_hook(
                        prompt=raw_prompt,
                        response=response_content,
                        user_id=user_id,
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        project_path=current_dir,
                        source_session_id=response_session_id,
                        source_message_id=(
                            update.message.message_id if update.message else None
                        ),
                        runtime_settings=memory_runtime_settings,
                        success=success,
                        elapsed_ms=int((time.time() - request_started_at) * 1000),
                    )
                except Exception as memory_error:
                    logger.warning(
                        "Memory post-hook failed for document",
                        user_id=user_id,
                        error=str(memory_error),
                    )

    async def agentic_photo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Process photo -> Claude/Copilot, minimal chrome."""
        import os  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        user_id = update.effective_user.id

        features = context.bot_data.get("features")
        image_handler = features.get_image_handler() if features else None

        if not image_handler:
            await update.message.reply_text("Photo processing is not available.")
            return

        chat = update.message.chat
        await chat.send_action("typing")
        progress_msg = await update.message.reply_text("Working...")

        tmp_path: Optional[str] = None
        try:
            photo = update.message.photo[-1]
            processed_image = await image_handler.process_image(
                photo, update.message.caption
            )

            claude_integration = context.bot_data.get("claude_integration")
            if not claude_integration:
                await progress_msg.edit_text(
                    "Claude integration not available. Check configuration."
                )
                return

            current_dir = context.user_data.get(
                "current_directory", self.settings.approved_directory
            )
            if not isinstance(current_dir, Path):
                current_dir = Path(current_dir)
            session_id = context.user_data.get("claude_session_id")
            chat_id = update.effective_chat.id if update.effective_chat else 0
            message_thread_id = self._extract_message_thread_id(update)

            # Check if /new was used — skip auto-resume for this first message.
            # Flag is only cleared after a successful run so retries keep the intent.
            force_new = bool(context.user_data.get("force_new_session"))
            controls = consume_request_controls(self.settings, context.user_data)
            raw_prompt = processed_image.prompt
            effective_prompt = raw_prompt
            effective_controls = controls
            memory_runtime_settings = None
            memory_service = context.bot_data.get("memory_service")
            if memory_service:
                try:
                    pre_hook_result = await memory_service.apply_pre_hook(
                        prompt=raw_prompt,
                        controls=controls,
                        user_id=user_id,
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        project_path=current_dir,
                    )
                    effective_prompt = pre_hook_result.prompt
                    effective_controls = pre_hook_result.controls
                    memory_runtime_settings = pre_hook_result.runtime_settings
                except Exception as memory_error:
                    logger.warning(
                        "Memory pre-hook failed for photo",
                        user_id=user_id,
                        error=str(memory_error),
                    )

            # For the Copilot provider, write image to a tmp file so it can be
            # passed as a file attachment in send_and_wait.
            image_path: Optional[str] = None
            if effective_controls["provider"] == "copilot":
                fmt = (
                    processed_image.metadata.get("format", "png")
                    if processed_image.metadata
                    else "png"
                )
                suffix = f".{fmt}" if fmt != "unknown" else ".png"
                import base64  # noqa: PLC0415

                img_bytes = base64.b64decode(processed_image.base64_data)
                fd, tmp_path = tempfile.mkstemp(suffix=suffix)
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(img_bytes)
                    image_path = tmp_path
                except Exception:
                    os.close(fd)

            verbose_level = self._get_verbose_level(context)
            tool_log: List[Dict[str, Any]] = []
            on_stream = self._make_stream_callback(
                verbose_level, progress_msg, tool_log, time.time(), context=context
            )

            heartbeat = self._start_typing_heartbeat(chat)
            success = True
            request_started_at = time.time()
            response_content = ""
            response_session_id = session_id
            try:
                claude_response = await self._run_command_with_controls(
                    claude_integration,
                    prompt=effective_prompt,
                    working_directory=current_dir,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    session_id=session_id,
                    on_stream=on_stream,
                    force_new=force_new,
                    effective_controls=effective_controls,
                    image_path=image_path,
                )
                response_content = claude_response.content
                response_session_id = claude_response.session_id
            except Exception:
                success = False
                raise
            finally:
                heartbeat.cancel()
                if memory_service:
                    try:
                        await memory_service.apply_post_hook(
                            prompt=raw_prompt,
                            response=response_content,
                            user_id=user_id,
                            chat_id=chat_id,
                            message_thread_id=message_thread_id,
                            project_path=current_dir,
                            source_session_id=response_session_id,
                            source_message_id=(
                                update.message.message_id if update.message else None
                            ),
                            runtime_settings=memory_runtime_settings,
                            success=success,
                            elapsed_ms=int((time.time() - request_started_at) * 1000),
                        )
                    except Exception as memory_error:
                        logger.warning(
                            "Memory post-hook failed for photo",
                            user_id=user_id,
                            error=str(memory_error),
                        )

            if force_new:
                context.user_data["force_new_session"] = False

            context.user_data["claude_session_id"] = claude_response.session_id

            from .utils.formatting import ResponseFormatter

            formatter = ResponseFormatter(self.settings)
            formatted_messages = formatter.format_claude_response(
                claude_response.content
            )

            await progress_msg.delete()

            for i, message in enumerate(formatted_messages):
                await update.message.reply_text(
                    message.text,
                    parse_mode=message.parse_mode,
                    reply_markup=None,
                    reply_to_message_id=(update.message.message_id if i == 0 else None),
                )
                if i < len(formatted_messages) - 1:
                    await asyncio.sleep(0.5)

        except Exception as e:
            success = False
            from .handlers.message import _format_error_message

            await progress_msg.edit_text(_format_error_message(e), parse_mode="HTML")
            logger.error(
                "Claude photo processing failed", error=str(e), user_id=user_id
            )
        finally:
            # Clean up temporary image file written for Copilot attachment
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    async def agentic_repo(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """List subdirectories in current workspace path or switch directory.

        /repo          — list subdirectories with git indicators
        /repo <path>   — switch relative to current directory, resume session
        """
        args = update.message.text.split()[1:] if update.message.text else []
        root = self.settings.approved_directory.resolve()
        current_dir = context.user_data.get("current_directory", root)
        if not isinstance(current_dir, Path):
            current_dir = root
        if not current_dir.is_dir() or not self._is_within_repo_root(current_dir):
            current_dir = root
            context.user_data["current_directory"] = root

        if args:
            target_name = " ".join(args).strip()
            target_path = self._resolve_repo_target(target_name, current_dir)
            if not target_path:
                await update.message.reply_text(
                    "Access denied: target directory is outside approved root.",
                    parse_mode="HTML",
                )
                return
            if not target_path.is_dir():
                await update.message.reply_text(
                    f"Directory not found: <code>{escape_html(target_name)}</code>",
                    parse_mode="HTML",
                )
                return

            session_id = await self._set_current_directory(
                context, update.effective_user.id, target_path
            )

            is_git = (target_path / ".git").is_dir()
            git_badge = " (git)" if is_git else ""
            session_badge = " · session resumed" if session_id else ""
            relative_display = self._repo_relative_display(target_path)

            await update.message.reply_text(
                f"Switched to <code>{escape_html(relative_display)}</code>"
                f"{git_badge}{session_badge}",
                parse_mode="HTML",
            )
            return

        # No args — list repos
        try:
            entries = sorted(
                [
                    d
                    for d in current_dir.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                ],
                key=lambda d: d.name,
            )
        except OSError as e:
            await update.message.reply_text(f"Error reading workspace: {e}")
            return

        current_display = self._repo_relative_display(current_dir)
        if not entries:
            await update.message.reply_text(
                f"<b>Repos</b>\n\n"
                f"Current: <code>{escape_html(current_display)}</code>\n"
                f"Root: <code>{escape_html(str(root))}</code>\n\n"
                "No subdirectories here.",
                parse_mode="HTML",
            )
            return

        lines: List[str] = []
        keyboard_rows: List[list] = []  # type: ignore[type-arg]

        for d in entries:
            is_git = (d / ".git").is_dir()
            icon = "\U0001f4e6" if is_git else "\U0001f4c1"
            lines.append(f"{icon} <code>{escape_html(d.name)}/</code>")

        # Build inline keyboard (2 per row)
        for i in range(0, len(entries), 2):
            row = []
            for j in range(2):
                if i + j < len(entries):
                    name = entries[i + j].name
                    row.append(InlineKeyboardButton(name, callback_data=f"cd:{name}"))
            keyboard_rows.append(row)

        nav_row = []
        if current_dir != root:
            nav_row.append(InlineKeyboardButton("⬆️ Up", callback_data="cd:.."))
        nav_row.append(InlineKeyboardButton("🏠 Root", callback_data="cd:/"))
        keyboard_rows.append(nav_row)

        reply_markup = InlineKeyboardMarkup(keyboard_rows)

        await update.message.reply_text(
            "<b>Repos</b>\n\n"
            f"Current: <code>{escape_html(current_display)}</code>\n"
            f"Root: <code>{escape_html(str(root))}</code>\n\n" + "\n".join(lines),
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

    async def _permission_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle perm: inline button — resolve pending Copilot permission request."""
        query = update.callback_query
        await query.answer()

        # data format: "perm:<interaction_id>:approve|deny"
        parts = (query.data or "").split(":", 2)
        interaction_id = parts[1] if len(parts) > 1 else ""
        decision = parts[2] if len(parts) > 2 else "deny"
        approved = decision == "approve"

        bridge = self._get_interaction_bridge(context)
        if not bridge or not interaction_id:
            await query.answer("Interaction bridge unavailable.")
            return

        meta = await bridge.get(interaction_id) or {}
        kind = meta.get("kind", "unknown")
        scope_user, scope_chat, scope_thread = self._interaction_scope(
            update=update, context=context
        )
        resolved = await bridge.resolve(
            interaction_id=interaction_id,
            value=approved,
            user_id=scope_user,
            chat_id=scope_chat,
            message_thread_id=scope_thread,
        )
        if resolved:
            context.user_data.pop("pending_permission_interaction_id", None)
            logger.info(
                "Resolved permission_request",
                user_id=scope_user,
                interaction_id=interaction_id,
                kind=kind,
                approved=approved,
            )
            label = "✅ Approved" if approved else "❌ Denied"
            try:
                await query.edit_message_text(
                    f"{label}: <code>{escape_html(str(kind))}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            await query.answer("This request is expired or not in your scope.")

    async def _ask_user_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle ask_user: inline button — resolve pending Copilot question."""
        query = update.callback_query
        await query.answer()

        # data format: "ask_user:<interaction_id>:<choice_index>"
        parts = (query.data or "").split(":", 2)
        interaction_id = parts[1] if len(parts) > 1 else ""
        choice_index = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1

        bridge = self._get_interaction_bridge(context)
        if not bridge or not interaction_id:
            await query.answer("Interaction bridge unavailable.")
            return

        meta = await bridge.get(interaction_id)
        if not meta:
            await query.answer("This question is no longer active.")
            return

        choices = list(meta.get("choices") or [])
        if choice_index < 0 or choice_index >= len(choices):
            await query.answer("Invalid choice.")
            return

        choice = choices[choice_index]
        scope_user, scope_chat, scope_thread = self._interaction_scope(
            update=update, context=context
        )
        resolved = await bridge.resolve(
            interaction_id=interaction_id,
            value=choice,
            user_id=scope_user,
            chat_id=scope_chat,
            message_thread_id=scope_thread,
        )

        if resolved:
            context.user_data.pop("pending_ask_user_interaction_id", None)
            logger.info(
                "Resolved ask_user via inline choice",
                user_id=scope_user,
                interaction_id=interaction_id,
                choice=choice,
            )
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        else:
            await query.answer("This question has already been answered.")

    async def _agentic_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle cd: callbacks — switch directory and resume session if available."""
        query = update.callback_query
        await query.answer()

        data = query.data
        _, project_name = data.split(":", 1)

        root = self.settings.approved_directory.resolve()
        current_dir = context.user_data.get("current_directory", root)
        if not isinstance(current_dir, Path):
            current_dir = root
        if not current_dir.is_dir() or not self._is_within_repo_root(current_dir):
            current_dir = root

        new_path = self._resolve_repo_target(project_name, current_dir)
        if not new_path:
            await query.edit_message_text(
                "Access denied: target directory is outside approved root.",
                parse_mode="HTML",
            )
            return

        if not new_path.is_dir():
            await query.edit_message_text(
                f"Directory not found: <code>{escape_html(project_name)}</code>",
                parse_mode="HTML",
            )
            return

        session_id = await self._set_current_directory(
            context, query.from_user.id, new_path
        )

        is_git = (new_path / ".git").is_dir()
        git_badge = " (git)" if is_git else ""
        session_badge = " · session resumed" if session_id else ""
        relative_display = self._repo_relative_display(new_path)

        await query.edit_message_text(
            f"Switched to <code>{escape_html(relative_display)}</code>"
            f"{git_badge}{session_badge}",
            parse_mode="HTML",
        )

        # Audit log
        audit_logger = context.bot_data.get("audit_logger")
        if audit_logger:
            await audit_logger.log_command(
                user_id=query.from_user.id,
                command="cd",
                args=[project_name],
                success=True,
            )

    async def _agentic_memory_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle memory:* callbacks in agentic mode."""
        from .handlers import callback  # noqa: PLC0415

        query = update.callback_query
        await query.answer()
        data = query.data or "memory:panel"
        payload = data.split(":", 1)[1] if ":" in data else "panel"
        await callback.handle_memory_callback(query, payload, context)
