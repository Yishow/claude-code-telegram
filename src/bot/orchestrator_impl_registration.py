"""Split mixin module for MessageOrchestrator."""

from __future__ import annotations

from . import orchestrator_impl_base as base

Any = base.Any
Application = base.Application
BotCommand = base.BotCommand
Callable = base.Callable
CallbackQueryHandler = base.CallbackQueryHandler
CommandHandler = base.CommandHandler
ContextTypes = base.ContextTypes
Dict = base.Dict
InlineKeyboardButton = base.InlineKeyboardButton
InlineKeyboardMarkup = base.InlineKeyboardMarkup
List = base.List
MessageHandler = base.MessageHandler
ONCE_MODEL_KEY = base.ONCE_MODEL_KEY
ONCE_PROVIDER_KEY = base.ONCE_PROVIDER_KEY
Optional = base.Optional
Path = base.Path
PrivateTopicsUnavailableError = base.PrivateTopicsUnavailableError
SESSION_MODEL_KEY = base.SESSION_MODEL_KEY
SESSION_PROVIDER_KEY = base.SESSION_PROVIDER_KEY
StreamUpdate = base.StreamUpdate
Tuple = base.Tuple
Update = base.Update
_tool_icon = base._tool_icon
_redact_secrets = base._redact_secrets
asyncio = base.asyncio
consume_request_controls = base.consume_request_controls
escape_html = base.escape_html
filters = base.filters
get_runtime_snapshot = base.get_runtime_snapshot
logger = base.logger
time = base.time


class MessageOrchestratorRegistrationMixin:
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
