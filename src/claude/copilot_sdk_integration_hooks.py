"""Split mixin module for CopilotSDKManager."""

from __future__ import annotations

from . import copilot_sdk_integration_base as base

Any = base.Any
AskUserRequest = base.AskUserRequest
AskUserResponse = base.AskUserResponse
Awaitable = base.Awaitable
Callable = base.Callable
ClaudeProcessError = base.ClaudeProcessError
ClaudeTimeoutError = base.ClaudeTimeoutError
CopilotAuthenticationError = base.CopilotAuthenticationError
CopilotResponse = base.CopilotResponse
CopilotStreamUpdate = base.CopilotStreamUpdate
Dict = base.Dict
List = base.List
Optional = base.Optional
Path = base.Path
SessionConfig = base.SessionConfig
Union = base.Union
_SEMVER_RE = base._SEMVER_RE
asyncio = base.asyncio
hashlib = base.hashlib
importlib = base.importlib
importlib_metadata = base.importlib_metadata
json = base.json
logger = base.logger
re = base.re


class CopilotSDKHooksMixin:
    async def _emit_update(
        self,
        stream_callback: Optional[
            Callable[[CopilotStreamUpdate], Union[None, Awaitable[None]]]
        ],
        update: CopilotStreamUpdate,
    ) -> None:
        """Emit one stream update to callback (awaiting async callbacks)."""
        if not stream_callback:
            return
        callback_result = stream_callback(update)
        if asyncio.iscoroutine(callback_result):
            await callback_result

    @staticmethod
    def _emit_event_update(
        stream_callback: Optional[
            Callable[[CopilotStreamUpdate], Union[None, Awaitable[None]]]
        ],
        update: CopilotStreamUpdate,
    ) -> None:
        """Emit updates from sync event handlers (schedule async callbacks)."""
        if not stream_callback:
            return
        callback_result = stream_callback(update)
        if asyncio.iscoroutine(callback_result):
            asyncio.create_task(callback_result)

    async def _handle_permission_request(
        self,
        *,
        request: Any,
        stream_callback: Optional[
            Callable[[CopilotStreamUpdate], Union[None, Awaitable[None]]]
        ],
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
    ) -> Dict[str, Any]:
        """Handle interactive or policy-driven permission request hook."""
        kind: str = str(self._read_field(request, "kind", default="unknown"))
        tool_call_id: str = str(
            self._read_field(request, "toolCallId", "tool_call_id", default="") or ""
        )
        permission_mode = str(
            getattr(self.config, "copilot_permission_mode", "interactive")
        ).lower()

        decision_kind = ""
        if permission_mode == "auto_approve":
            decision_kind = "approved"
        elif permission_mode == "auto_deny":
            decision_kind = "denied-by-rules"

        if not decision_kind:
            if stream_callback and chat_id:
                meta = await self.interaction_bridge.create_permission_request(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    kind=kind,
                    tool_call_id=tool_call_id,
                )
                await self._emit_update(
                    stream_callback,
                    CopilotStreamUpdate(
                        type="permission_request",
                        content=kind,
                        metadata=meta,
                    ),
                )
                approved = bool(
                    await self.interaction_bridge.wait_for_result(
                        meta["interaction_id"]
                    )
                )
            elif stream_callback:
                future: "asyncio.Future[bool]" = (
                    asyncio.get_event_loop().create_future()
                )
                await self._emit_update(
                    stream_callback,
                    CopilotStreamUpdate(
                        type="permission_request",
                        content=kind,
                        metadata={
                            "kind": kind,
                            "tool_call_id": tool_call_id,
                            "future": future,
                        },
                    ),
                )
                try:
                    approved = bool(
                        await asyncio.wait_for(asyncio.shield(future), timeout=120)
                    )
                except asyncio.TimeoutError:
                    approved = False
            else:
                approved = True

            decision_kind = "approved" if approved else "denied-interactively-by-user"

        logger.info(
            "Copilot permission decision",
            kind=kind,
            decision_kind=decision_kind,
            permission_mode=permission_mode,
            user_id=user_id,
        )

        return {"kind": decision_kind, "rules": []}

    async def _handle_user_input_request(
        self,
        *,
        request: Any,
        stream_callback: Optional[
            Callable[[CopilotStreamUpdate], Union[None, Awaitable[None]]]
        ],
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
    ) -> Dict[str, Any]:
        """Handle ask-user input request hook."""
        question: str = str(self._read_field(request, "question", default="") or "")
        choices: List[str] = list(
            self._read_field(request, "choices", default=[]) or []
        )
        allow_freeform: bool = bool(
            self._read_field(request, "allowFreeform", "allow_freeform", default=True)
        )

        if stream_callback and chat_id:
            meta = await self.interaction_bridge.create_ask_user(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                question=question,
                choices=choices,
                allow_freeform=allow_freeform,
            )
            await self._emit_update(
                stream_callback,
                CopilotStreamUpdate(
                    type="ask_user",
                    content=question,
                    metadata=meta,
                ),
            )
            answer = await self.interaction_bridge.wait_for_result(
                meta["interaction_id"]
            )
            if not isinstance(answer, str):
                answer = ""
        elif stream_callback:
            future: "asyncio.Future[str]" = asyncio.get_event_loop().create_future()
            await self._emit_update(
                stream_callback,
                CopilotStreamUpdate(
                    type="ask_user",
                    content=question,
                    metadata={
                        "question": question,
                        "choices": choices,
                        "allow_freeform": allow_freeform,
                        "future": future,
                    },
                ),
            )
            try:
                raw_answer = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=self.interaction_bridge.ask_user_timeout_seconds,
                )
            except asyncio.TimeoutError:
                raw_answer = ""
            answer = raw_answer if isinstance(raw_answer, str) else ""
        else:
            answer = ""

        logger.info(
            "Copilot ask_user resolved",
            user_id=user_id,
            has_answer=bool(answer),
            allow_freeform=allow_freeform,
        )

        return {"answer": answer, "wasFreeform": allow_freeform}

    async def _handle_error_occurred_hook(
        self,
        *,
        hook_input: Any,
    ) -> Optional[Dict[str, Any]]:
        """Handle Copilot error hook response policy."""
        error_msg: str = str(self._read_field(hook_input, "error", default="") or "")
        error_context: str = str(
            self._read_field(hook_input, "errorContext", "error_context", default="")
            or ""
        )
        recoverable: bool = bool(
            self._read_field(hook_input, "recoverable", default=False)
        )

        logger.warning(
            "Copilot error hook triggered",
            error=error_msg,
            error_context=error_context,
            recoverable=recoverable,
        )

        is_rate_limit = any(
            kw in error_msg.lower()
            for kw in ("rate limit", "rate_limit", "too many requests", "429")
        )
        if is_rate_limit:
            return {"errorHandling": "retry", "retryCount": 3}

        if recoverable and error_context == "tool_execution":
            return {"errorHandling": "skip"}

        return {
            "errorHandling": "abort",
            "userNotification": f"Copilot error ({error_context}): {error_msg}",
        }

    async def _handle_pre_tool_use_hook(
        self,
        *,
        hook_input: Any,
        stream_callback: Optional[
            Callable[[CopilotStreamUpdate], Union[None, Awaitable[None]]]
        ],
        working_directory: Path,
        user_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Handle pre-tool-use hook and policy gating."""
        tool_name: str = str(
            self._read_field(hook_input, "toolName", "tool_name", default="") or ""
        )
        tool_args: Dict[str, Any] = dict(
            self._read_field(hook_input, "toolArgs", "tool_args", default={}) or {}
        )

        logger.debug(
            "Copilot pre_tool_use hook",
            tool_name=tool_name,
            working_directory=str(working_directory),
            user_id=user_id,
        )

        await self._emit_update(
            stream_callback,
            CopilotStreamUpdate(
                type="tool",
                content=tool_name,
                metadata={
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "action": "pre",
                },
            ),
        )

        if not self.tool_monitor:
            return None

        valid, error = await self.tool_monitor.validate_tool_call(
            tool_name,
            tool_args,
            working_directory,
            user_id,
        )
        if valid:
            return None

        await self._emit_update(
            stream_callback,
            CopilotStreamUpdate(
                type="tool_denied",
                content=tool_name,
                metadata={
                    "tool_name": tool_name,
                    "reason": error or "policy_denied",
                },
            ),
        )
        logger.warning(
            "Copilot tool denied by policy",
            tool_name=tool_name,
            reason=error,
            user_id=user_id,
        )
        return {
            "permissionDecision": "deny",
            "denyReason": error or "Tool policy denied",
        }

    def _dispatch_stream_event(
        self,
        *,
        event: Any,
        stream_callback: Optional[
            Callable[[CopilotStreamUpdate], Union[None, Awaitable[None]]]
        ],
        content_parts: List[str],
    ) -> None:
        """Dispatch one SDK event to stream updates and content collection."""
        event_type = str(getattr(event, "type", ""))
        normalized_event_type = event_type.lower()
        data = getattr(event, "data", None)

        if normalized_event_type in {"assistant_message", "assistant.message"}:
            content = getattr(data, "content", None) or ""
            if content:
                content_parts.append(content)
                self._emit_event_update(
                    stream_callback,
                    CopilotStreamUpdate(type="result", content=content),
                )
            return

        if normalized_event_type == "assistant.message_delta":
            delta = getattr(data, "delta_content", None) or ""
            if delta:
                self._emit_event_update(
                    stream_callback,
                    CopilotStreamUpdate(type="result", content=delta),
                )
            return

        if normalized_event_type == "assistant.reasoning_delta":
            reasoning = getattr(data, "delta_content", None) or ""
            if reasoning:
                self._emit_event_update(
                    stream_callback,
                    CopilotStreamUpdate(type="reasoning", content=reasoning),
                )
            return

        if event_type in ("tool_use", "tool_result"):
            tool_name = getattr(data, "tool_name", None) or ""
            tool_args = getattr(data, "tool_args", None) or {}
            action = "pre" if event_type == "tool_use" else "post"
            if tool_name:
                self._emit_event_update(
                    stream_callback,
                    CopilotStreamUpdate(
                        type="tool",
                        content=tool_name,
                        metadata={
                            "tool_name": tool_name,
                            "tool_args": tool_args,
                            "action": action,
                        },
                    ),
                )
            return

        if event_type in ("context_changed", "session.context_changed"):
            self._emit_event_update(
                stream_callback,
                CopilotStreamUpdate(
                    type="context_changed",
                    content="Copilot context changed",
                    metadata={
                        "event_type": event_type,
                        "details": {
                            "session_id": getattr(data, "session_id", None),
                            "reason": getattr(data, "reason", None),
                        },
                    },
                ),
            )

    def _build_session_config(
        self,
        *,
        SessionConfig: Any,
        working_directory: Path,
        effective_model: Optional[str],
        runtime_controls: Dict[str, Any],
        mcp_servers: List[Dict[str, Any]],
        infinite_sessions_enabled: bool,
        compaction_threshold: float,
        on_user_input_request: Callable[..., Awaitable[Dict[str, Any]]],
        on_permission_request: Callable[..., Awaitable[Dict[str, Any]]],
        on_pre_tool_use: Callable[..., Awaitable[Optional[Dict[str, Any]]]],
        on_error_occurred: Callable[..., Awaitable[Optional[Dict[str, Any]]]],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Build SessionConfig payload from runtime controls and hooks."""
        config_kwargs: Dict[str, Any] = {
            "workspace_path": str(working_directory),
            "on_user_input_request": on_user_input_request,
            "on_permission_request": on_permission_request,
            "on_pre_tool_use": on_pre_tool_use,
            "on_error_occurred": on_error_occurred,
            "streaming": True,  # enables assistant.message_delta + reasoning_delta
        }
        if extra:
            config_kwargs.update(extra)
        if effective_model:
            config_kwargs["model"] = effective_model

        cfg = SessionConfig(**config_kwargs)
        if mcp_servers:
            cfg["mcp_servers"] = mcp_servers

        if infinite_sessions_enabled:
            cfg["infinite_sessions"] = {
                "enabled": True,
                "background_compaction_threshold": compaction_threshold,
                "buffer_exhaustion_threshold": min(compaction_threshold + 0.15, 0.99),
            }

        if runtime_controls.get("reasoning_effort"):
            cfg["reasoning_effort"] = runtime_controls["reasoning_effort"]

        if runtime_controls.get("skill_directories"):
            cfg["skill_directories"] = runtime_controls["skill_directories"]

        if runtime_controls.get("disabled_skills"):
            cfg["disabled_skills"] = runtime_controls["disabled_skills"]

        if runtime_controls.get("external_cli_server"):
            # Keep both keys for compatibility across SDK versions.
            cfg["external_cli_server"] = runtime_controls["external_cli_server"]
            cfg["server_endpoint"] = runtime_controls["external_cli_server"]

        config_dir = self._resolve_config_dir(working_directory)
        if config_dir:
            cfg["config_dir"] = config_dir

        return cfg
