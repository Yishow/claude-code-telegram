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


class MessageOrchestratorMediaMixin:
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
