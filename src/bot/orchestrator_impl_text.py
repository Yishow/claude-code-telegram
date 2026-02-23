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


class MessageOrchestratorTextMixin:
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
