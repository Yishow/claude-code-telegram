"""Split mixin module for CopilotSDKManager."""

from __future__ import annotations

import shlex

from . import copilot_sdk_integration_base as base
from .monitor import check_bash_directory_boundary

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

    @staticmethod
    def _extract_shell_command(tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Return shell command string when the tool call is shell-like."""
        if str(tool_name).strip().lower() not in {"bash", "shell"}:
            return ""

        for key in ("command", "cmd", "input", "script", "bash_command"):
            value = tool_args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        return ""

    @staticmethod
    def _extract_base_command(command: str) -> str:
        """Extract the first executable token from a shell command."""
        try:
            tokens = shlex.split(command)
        except ValueError:
            return ""

        if not tokens:
            return ""

        return CopilotSDKHooksMixin._extract_base_command_from_tokens(tokens)

    @staticmethod
    def _extract_segment_commands(command: str) -> List[str]:
        """Extract one executable command per shell segment."""
        try:
            tokens = shlex.split(command)
        except ValueError:
            return []

        if not tokens:
            return []

        separators = {"&&", "||", ";", "|", "&"}
        segments: List[List[str]] = []
        current: List[str] = []
        for token in tokens:
            if token in separators:
                if current:
                    segments.append(current)
                current = []
            else:
                current.append(token)
        if current:
            segments.append(current)

        commands: List[str] = []
        for segment in segments:
            base = CopilotSDKHooksMixin._extract_base_command_from_tokens(segment)
            if base:
                commands.append(base)
        return commands

    @staticmethod
    def _extract_base_command_from_tokens(tokens: List[str]) -> str:
        """Extract executable from one shell segment token list."""
        separators = {"&&", "||", ";", "|", "&"}
        wrappers = {"sudo", "command", "nohup", "time"}
        python_binaries = {
            "python",
            "python3",
            "python3.10",
            "python3.11",
            "python3.12",
            "python3.13",
            "py",
        }

        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in separators:
                return ""

            base = Path(token).name.lower()
            if base in wrappers:
                index += 1
                continue

            if base == "env":
                index += 1
                while index < len(tokens):
                    env_token = tokens[index]
                    if env_token in separators:
                        return ""
                    if "=" in env_token and not env_token.startswith("-"):
                        index += 1
                        continue
                    break
                continue

            if base in python_binaries:
                if index + 2 < len(tokens) and tokens[index + 1] == "-m":
                    module = tokens[index + 2].split(".", 1)[0].strip().lower()
                    if module:
                        return module
                return base

            if "=" in token and not token.startswith("/"):
                maybe_key = token.split("=", 1)[0]
                if maybe_key.isidentifier():
                    index += 1
                    continue

            return base

        return ""

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

        shell_command = self._extract_shell_command(tool_name, tool_args)
        if shell_command and bool(getattr(self.config, "sandbox_enabled", True)):
            excluded_commands = {
                str(cmd).strip().lower()
                for cmd in (getattr(self.config, "sandbox_excluded_commands", []) or [])
                if str(cmd).strip()
            }
            segment_commands = self._extract_segment_commands(shell_command)
            should_auto_allow_excluded = (
                bool(segment_commands)
                and any(cmd in excluded_commands for cmd in segment_commands)
                and all(
                    cmd in excluded_commands or cmd == "cd" for cmd in segment_commands
                )
            )
            if should_auto_allow_excluded:
                matched = ",".join(
                    sorted({cmd for cmd in segment_commands if cmd in excluded_commands})
                )
                reason = f"Sandbox excluded command auto-approved: {matched}"
                logger.info(
                    "Copilot shell command auto-approved by sandbox exclusion",
                    tool_name=tool_name,
                    excluded_commands=matched,
                    user_id=user_id,
                )
                return {
                    "permissionDecision": "allow",
                    "permissionDecisionReason": reason,
                }

            valid_boundary, boundary_error = check_bash_directory_boundary(
                shell_command,
                working_directory,
                Path(self.config.approved_directory),
            )
            if not valid_boundary:
                reason = boundary_error or "Bash directory boundary violation"
                await self._emit_update(
                    stream_callback,
                    CopilotStreamUpdate(
                        type="tool_denied",
                        content=tool_name,
                        metadata={
                            "tool_name": tool_name,
                            "reason": reason,
                        },
                    ),
                )
                logger.warning(
                    "Copilot shell command denied by directory boundary",
                    tool_name=tool_name,
                    reason=reason,
                    user_id=user_id,
                )
                return {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                    "denyReason": reason,
                }

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
        reason = error or "Tool policy denied"
        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
            "denyReason": reason,
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
            "working_directory": str(working_directory),
            "on_user_input_request": on_user_input_request,
            "on_permission_request": on_permission_request,
            "hooks": {
                "on_pre_tool_use": on_pre_tool_use,
                "on_error_occurred": on_error_occurred,
            },
            # Keep direct keys for backward compatibility with SDK variants that
            # still accept hook handlers as top-level fields.
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
