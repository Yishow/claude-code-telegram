"""Split mixin module for MessageOrchestrator."""

from __future__ import annotations

from . import orchestrator_base as base

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


class MessageOrchestratorCommandsMixin:
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
