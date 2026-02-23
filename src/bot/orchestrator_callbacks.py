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


class MessageOrchestratorCallbacksMixin:
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
