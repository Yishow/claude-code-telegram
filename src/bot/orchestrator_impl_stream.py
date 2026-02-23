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


class MessageOrchestratorStreamMixin:
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
