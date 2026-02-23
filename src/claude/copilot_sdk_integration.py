"""GitHub Copilot SDK integration.

Uses github-copilot-sdk via JSON-RPC and exposes a bot-friendly runtime
surface (interactive bridge, status/introspection, session operations,
policy-aware runtime controls, and reliability guardrails).
"""

import asyncio
import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import structlog

from ..config.settings import Settings
from .copilot_interaction_bridge import CopilotInteractionBridge
from .exceptions import ClaudeProcessError, ClaudeTimeoutError

try:
    from .exceptions import CopilotAuthenticationError
except ImportError:  # pragma: no cover - mixed-version runtime fallback
    CopilotAuthenticationError = ClaudeProcessError
from .monitor import ToolMonitor

logger = structlog.get_logger()
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


@dataclass
class CopilotResponse:
    """Response from Copilot SDK."""

    content: str
    session_id: str
    cost: float = 0.0
    duration_ms: int = 0
    num_turns: int = 1
    is_error: bool = False
    error_type: Optional[str] = None
    tools_used: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class CopilotStreamUpdate:
    """Streaming update from Copilot SDK."""

    type: str
    content: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class CopilotSDKManager:
    """Manage Copilot sessions via the official github-copilot-sdk."""

    def __init__(
        self,
        config: Settings,
        *,
        interaction_bridge: Optional[CopilotInteractionBridge] = None,
        tool_monitor: Optional[ToolMonitor] = None,
    ):
        self.config = config
        self.tool_monitor = tool_monitor
        self.interaction_bridge = interaction_bridge or CopilotInteractionBridge(
            permission_timeout_seconds=int(
                getattr(config, "copilot_permission_timeout_seconds", 120)
            )
        )

        self._client: Optional[Any] = None
        self._client_lock = asyncio.Lock()
        self._session_map: Dict[str, str] = {}

        self._runtime_controls: Dict[str, Any] = {
            "reasoning_effort": getattr(config, "copilot_reasoning_default", "medium"),
            "skill_directories": list(
                getattr(config, "copilot_skill_directories", []) or []
            ),
            "disabled_skills": list(
                getattr(config, "copilot_disabled_skills", []) or []
            ),
            "mcp_env_value_mode": getattr(config, "mcp_env_value_mode", "raw"),
            "external_cli_server": getattr(config, "copilot_external_cli_server", None),
        }

        raw_store = getattr(
            config, "copilot_session_store_path", Path("data/copilot-session-map.json")
        )
        self._session_store_path = Path(raw_store).expanduser()
        self._load_session_map()

    def _session_key(self, user_id: int, working_directory: Path) -> str:
        return f"{user_id}:{working_directory.resolve()}"

    def _load_session_map(self) -> None:
        if not self._session_store_path.exists():
            return
        try:
            raw = json.loads(self._session_store_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._session_map = {str(k): str(v) for k, v in raw.items() if v}
                logger.info(
                    "Loaded persisted Copilot session map",
                    count=len(self._session_map),
                    path=str(self._session_store_path),
                )
        except Exception as e:
            logger.warning(
                "Failed to load Copilot session map",
                path=str(self._session_store_path),
                error=str(e),
            )

    def _persist_session_map(self) -> None:
        try:
            self._session_store_path.parent.mkdir(parents=True, exist_ok=True)
            self._session_store_path.write_text(
                json.dumps(self._session_map, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(
                "Failed to persist Copilot session map",
                path=str(self._session_store_path),
                error=str(e),
            )

    @staticmethod
    def _read_field(payload: Any, *names: str, default: Any = None) -> Any:
        """Read a field from dict-like or object payloads."""
        if isinstance(payload, dict):
            for name in names:
                value = payload.get(name)
                if value is not None:
                    return value
            return default

        for name in names:
            value = getattr(payload, name, None)
            if value is not None:
                return value

        return default

    async def _get_client(self) -> Any:
        """Get or create the long-lived CopilotClient."""
        async with self._client_lock:
            if self._client is None:
                from copilot import CopilotClient  # noqa: PLC0415

                self._client = CopilotClient()
                await self._client.start()
                logger.info("CopilotClient started")
            return self._client

    def get_runtime_controls(self) -> Dict[str, Any]:
        """Get current runtime controls."""
        return {
            "reasoning_effort": self._runtime_controls.get(
                "reasoning_effort", "medium"
            ),
            "skill_directories": list(
                self._runtime_controls.get("skill_directories", []) or []
            ),
            "disabled_skills": list(
                self._runtime_controls.get("disabled_skills", []) or []
            ),
            "mcp_env_value_mode": self._runtime_controls.get(
                "mcp_env_value_mode", "raw"
            ),
            "external_cli_server": self._runtime_controls.get("external_cli_server"),
            "config_dir_policy": getattr(
                self.config, "copilot_config_dir_policy", "global"
            ),
        }

    def update_runtime_controls(
        self,
        *,
        reasoning_effort: Optional[str] = None,
        skill_directories: Optional[List[str]] = None,
        disabled_skills: Optional[List[str]] = None,
        mcp_env_value_mode: Optional[str] = None,
        external_cli_server: Optional[str] = None,
        external_cli_server_set: bool = False,
    ) -> Dict[str, Any]:
        """Apply runtime control updates and return the effective state."""
        if reasoning_effort is not None:
            self._runtime_controls["reasoning_effort"] = reasoning_effort
        if skill_directories is not None:
            self._runtime_controls["skill_directories"] = list(skill_directories)
        if disabled_skills is not None:
            self._runtime_controls["disabled_skills"] = list(disabled_skills)
        if mcp_env_value_mode is not None:
            self._runtime_controls["mcp_env_value_mode"] = mcp_env_value_mode
        if external_cli_server_set or external_cli_server is not None:
            self._runtime_controls["external_cli_server"] = external_cli_server

        return self.get_runtime_controls()

    def _effective_controls(
        self,
        *,
        reasoning_effort: Optional[str],
        skill_directories: Optional[List[str]],
        disabled_skills: Optional[List[str]],
        mcp_env_value_mode: Optional[str],
        external_cli_server: Optional[str],
    ) -> Dict[str, Any]:
        base = self.get_runtime_controls()
        if reasoning_effort is not None:
            base["reasoning_effort"] = reasoning_effort
        if skill_directories is not None:
            base["skill_directories"] = list(skill_directories)
        if disabled_skills is not None:
            base["disabled_skills"] = list(disabled_skills)
        if mcp_env_value_mode is not None:
            base["mcp_env_value_mode"] = mcp_env_value_mode
        if external_cli_server is not None:
            base["external_cli_server"] = external_cli_server
        return base

    def _resolve_config_dir(self, working_directory: Path) -> Optional[str]:
        policy = getattr(self.config, "copilot_config_dir_policy", "global")
        if policy != "per_project":
            return None

        project_hash = hashlib.sha1(
            str(working_directory.resolve()).encode("utf-8")
        ).hexdigest()[:12]
        base = Path(self.config.approved_directory) / ".copilot-config"
        target = base / project_hash
        target.mkdir(parents=True, exist_ok=True)
        return str(target)

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

    async def execute_command(
        self,
        prompt: str,
        working_directory: Path,
        user_id: int = 0,
        chat_id: int = 0,
        message_thread_id: Optional[int] = None,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        stream_callback: Optional[
            Callable[[CopilotStreamUpdate], Union[None, Awaitable[None]]]
        ] = None,
        model: Optional[str] = None,
        image_path: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        skill_directories: Optional[List[str]] = None,
        disabled_skills: Optional[List[str]] = None,
        mcp_env_value_mode: Optional[str] = None,
        external_cli_server: Optional[str] = None,
    ) -> CopilotResponse:
        """Execute a prompt via Copilot SDK with full session management."""
        from copilot import ResumeSessionConfig, SessionConfig

        start_time = asyncio.get_event_loop().time()
        client = await self._get_client()

        key = self._session_key(user_id, working_directory)
        copilot_session_id = session_id or (
            self._session_map.get(key) if continue_session else None
        )

        timeout = float(getattr(self.config, "claude_timeout_seconds", 300))
        configured_model = getattr(self.config, "copilot_model", "gpt-5-mini")
        effective_model = configured_model if model is None else (model.strip() or None)
        runtime_controls = self._effective_controls(
            reasoning_effort=reasoning_effort,
            skill_directories=skill_directories,
            disabled_skills=disabled_skills,
            mcp_env_value_mode=mcp_env_value_mode,
            external_cli_server=external_cli_server,
        )

        logger.info(
            "Executing via Copilot SDK",
            user_id=user_id,
            chat_id=chat_id,
            working_directory=str(working_directory),
            session_id=copilot_session_id,
            continue_session=continue_session,
            model=effective_model,
            reasoning_effort=runtime_controls.get("reasoning_effort"),
        )

        # Build permission_request handler — sends Approve/Deny to Telegram,
        # awaits a bool Future resolved by the user's inline button press.
        async def _on_permission_request(request: Any, _context: Any) -> Dict[str, Any]:
            return await self._handle_permission_request(
                request=request,
                stream_callback=stream_callback,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
            )

        async def _on_user_input_request(request: Any) -> Dict[str, Any]:
            return await self._handle_user_input_request(
                request=request,
                stream_callback=stream_callback,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
            )

        async def _on_error_occurred(
            hook_input: Any, _env: Any
        ) -> Optional[Dict[str, Any]]:
            return await self._handle_error_occurred_hook(hook_input=hook_input)

        async def _on_pre_tool_use(
            hook_input: Any, _env: Any
        ) -> Optional[Dict[str, Any]]:
            return await self._handle_pre_tool_use_hook(
                hook_input=hook_input,
                stream_callback=stream_callback,
                working_directory=working_directory,
                user_id=user_id,
            )

        infinite_sessions_enabled = bool(
            getattr(self.config, "copilot_infinite_sessions", True)
        )
        compaction_threshold = float(
            getattr(self.config, "copilot_compaction_threshold", 0.80)
        )
        mcp_servers = self._load_mcp_servers(
            runtime_controls.get("mcp_env_value_mode", "raw")
        )

        def _make_session_config(**extra: Any) -> "SessionConfig":
            return self._build_session_config(
                SessionConfig=SessionConfig,
                working_directory=working_directory,
                effective_model=effective_model,
                runtime_controls=runtime_controls,
                mcp_servers=mcp_servers,
                infinite_sessions_enabled=infinite_sessions_enabled,
                compaction_threshold=compaction_threshold,
                on_user_input_request=_on_user_input_request,
                on_permission_request=_on_permission_request,
                on_pre_tool_use=_on_pre_tool_use,
                on_error_occurred=_on_error_occurred,
                extra=extra,
            )

        unsubscribe: Any = None
        try:
            if copilot_session_id and continue_session:
                try:
                    session = await client.resume_session(
                        copilot_session_id,
                        ResumeSessionConfig(workspace_path=str(working_directory)),
                    )
                    logger.info(
                        "Resumed Copilot session", session_id=copilot_session_id
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to resume session, creating new",
                        session_id=copilot_session_id,
                        error=str(e),
                    )
                    session = await client.create_session(_make_session_config())
            else:
                session = await client.create_session(_make_session_config())

            content_parts: List[str] = []

            def event_handler(event: Any) -> None:
                self._dispatch_stream_event(
                    event=event,
                    stream_callback=stream_callback,
                    content_parts=content_parts,
                )

            unsubscribe = session.on(event_handler)

            message_options: Dict[str, Any] = {"prompt": prompt}
            if image_path:
                message_options["attachments"] = [{"type": "file", "path": image_path}]
                logger.debug(
                    "Attaching image to Copilot message", image_path=image_path
                )

            # Send and wait
            # Copilot SDK defaults send_and_wait timeout to 60s if omitted.
            # Pass our configured timeout explicitly and keep a small outer guard.
            try:
                send_wait = session.send_and_wait(message_options, timeout=timeout)
            except TypeError as e:
                if "unexpected keyword argument 'timeout'" in str(e):
                    send_wait = session.send_and_wait(message_options)
                else:
                    raise
            result_event = await asyncio.wait_for(send_wait, timeout=timeout + 5)

            final_content = ""
            if result_event:
                data = getattr(result_event, "data", None)
                final_content = getattr(data, "content", "") or ""
            if not final_content and content_parts:
                final_content = content_parts[-1]

            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            new_session_id = session.session_id
            self._session_map[key] = new_session_id
            self._persist_session_map()

            logger.info(
                "Copilot SDK execution completed",
                session_id=new_session_id,
                duration_ms=duration_ms,
                content_length=len(final_content),
            )

            return CopilotResponse(
                content=final_content,
                session_id=new_session_id,
                duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            elapsed = int(asyncio.get_event_loop().time() - start_time)
            logger.error(
                "Copilot SDK timed out",
                user_id=user_id,
                configured_timeout=timeout,
                elapsed_seconds=elapsed,
            )
            raise ClaudeTimeoutError(
                f"Copilot SDK timed out after {elapsed}s (configured {int(timeout)}s)"
            )

        except Exception as e:
            text = str(e)
            lowered = text.lower()
            if "auth" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
                logger.error("Copilot authentication failed", error=text)
                raise CopilotAuthenticationError(
                    f"Copilot authentication failed: {text}"
                ) from e

            logger.error("Copilot SDK execution failed", error=text)
            raise ClaudeProcessError(f"Copilot SDK error: {text}") from e

        finally:
            self._safe_unsubscribe(unsubscribe)

    async def get_status(self) -> Dict[str, Any]:
        """Collect Copilot runtime/introspection status."""
        status: Dict[str, Any] = {
            "runtime": {
                "client_started": self._client is not None,
                "fallback_mode": getattr(
                    self.config, "copilot_fallback_mode", "sdk_then_cli"
                ),
                "external_cli_server": self._runtime_controls.get(
                    "external_cli_server"
                ),
                "config_dir_policy": getattr(
                    self.config, "copilot_config_dir_policy", "global"
                ),
                "permission_timeout_seconds": self.interaction_bridge.permission_timeout_seconds,
                "permission_mode": getattr(
                    self.config, "copilot_permission_mode", "interactive"
                ),
            },
            "session": {
                "tracked_sessions": len(self._session_map),
                "store_path": str(self._session_store_path),
            },
            "model": {
                "default_model": getattr(self.config, "copilot_model", "gpt-5-mini"),
                "reasoning_effort": self._runtime_controls.get(
                    "reasoning_effort", "medium"
                ),
            },
            "skills": {
                "skill_directories": list(
                    self._runtime_controls.get("skill_directories", []) or []
                ),
                "disabled_skills": list(
                    self._runtime_controls.get("disabled_skills", []) or []
                ),
            },
            "mcp": {
                "enabled": bool(getattr(self.config, "enable_mcp", False)),
                "env_value_mode": self._runtime_controls.get(
                    "mcp_env_value_mode", "raw"
                ),
            },
        }

        client = self._client
        if not client:
            status["health"] = "degraded"
            status["reason"] = "Copilot client not started yet"
            return status

        status["health"] = "healthy"

        sdk_checks = {
            "status": ("status", "get_status"),
            "auth": ("auth_status", "get_auth_status", "auth"),
            "models": ("models", "list_models", "get_models"),
        }
        for label, methods in sdk_checks.items():
            payload = None
            for method_name in methods:
                if not hasattr(client, method_name):
                    continue
                method = getattr(client, method_name)
                try:
                    maybe = method()
                    payload = await maybe if asyncio.iscoroutine(maybe) else maybe
                    break
                except Exception as e:
                    payload = {"error": str(e)}
                    break
            if payload is not None:
                status[label] = self._redact_sensitive(payload)

        return status

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List known Copilot sessions, preferring SDK-native session listing."""
        rows = self._local_session_rows()
        sdk_rows: List[Dict[str, Any]] = []
        client: Optional[Any] = self._client
        if client is None:
            try:
                client = await self._get_client()
            except Exception as e:
                logger.warning(
                    "Copilot client unavailable for list_sessions", error=str(e)
                )

        if client is not None:
            for method_name in ("list_sessions", "sessions", "get_sessions"):
                if not hasattr(client, method_name):
                    continue
                method = getattr(client, method_name)
                try:
                    maybe = method()
                    payload = await maybe if asyncio.iscoroutine(maybe) else maybe
                    sdk_rows = self._normalize_sdk_sessions_payload(payload)
                    logger.info(
                        "Listed Copilot SDK sessions",
                        method=method_name,
                        count=len(sdk_rows),
                    )
                    break
                except Exception as e:
                    logger.warning(
                        "Copilot SDK list_sessions failed",
                        method=method_name,
                        error=str(e),
                    )

        if not sdk_rows:
            return rows

        merged: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in sdk_rows + rows:
            session_id = str(row.get("session_id") or "")
            project_path = str(row.get("project_path") or "")
            if not session_id:
                continue
            merged[(session_id, project_path)] = row
        return list(merged.values())

    async def delete_session(self, session_id: str) -> Dict[str, Any]:
        """Delete session from local map and SDK backend when available."""
        removed_keys = [k for k, v in self._session_map.items() if v == session_id]
        for k in removed_keys:
            self._session_map.pop(k, None)
        if removed_keys:
            self._persist_session_map()

        sdk_deleted = False
        client: Optional[Any] = self._client
        if client is None:
            try:
                client = await self._get_client()
            except Exception as e:
                logger.warning(
                    "Copilot client unavailable for delete_session", error=str(e)
                )

        if client is not None:
            for method_name in ("delete_session", "remove_session"):
                if not hasattr(client, method_name):
                    continue
                method = getattr(client, method_name)
                try:
                    maybe = method(session_id)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                    sdk_deleted = True
                    break
                except Exception as e:
                    logger.warning(
                        "Copilot SDK session delete failed",
                        session_id=session_id,
                        error=str(e),
                    )

        return {
            "session_id": session_id,
            "removed_local": bool(removed_keys),
            "removed_sdk": sdk_deleted,
        }

    def switch_session(
        self, *, user_id: int, working_directory: Path, session_id: str
    ) -> Dict[str, Any]:
        """Pin current user/project mapping to an explicit Copilot session id."""
        key = self._session_key(user_id, working_directory)
        previous = self._session_map.get(key)
        self._session_map[key] = session_id
        self._persist_session_map()
        return {
            "user_id": user_id,
            "project_path": str(working_directory.resolve()),
            "previous_session_id": previous,
            "current_session_id": session_id,
        }

    async def get_reasoning_levels(self) -> List[str]:
        """Detect supported reasoning levels with SDK capability inference."""
        levels = ["low", "medium", "high"]
        package_info = self._detect_sdk_package()
        version = package_info.get("version")
        prerelease_opt_in = bool(
            getattr(self.config, "copilot_enable_prerelease_features", False)
        )
        version_is_preview = isinstance(version, str) and self._is_prerelease_version(
            version
        )
        allow_inferred_extras = prerelease_opt_in or not version_is_preview

        if (
            allow_inferred_extras
            and isinstance(version, str)
            and self._version_at_least(version, (0, 1, 25))
        ):
            levels.append("xhigh")

        if allow_inferred_extras:
            try:
                status = await self.get_status()
                status_blob = json.dumps(status, ensure_ascii=False).lower()
                if "xhigh" in status_blob and "xhigh" not in levels:
                    levels.append("xhigh")
            except Exception:
                pass

        return levels

    async def get_capabilities(self) -> Dict[str, Any]:
        """Return runtime capability probe for Copilot SDK surface."""
        package_info = self._detect_sdk_package()
        client_obj: Optional[Any] = self._client
        session_config_annotations: Dict[str, Any] = {}

        try:
            from copilot import CopilotClient, SessionConfig  # noqa: PLC0415

            if client_obj is None:
                client_obj = CopilotClient
            annotations = getattr(SessionConfig, "__annotations__", {})
            if isinstance(annotations, dict):
                session_config_annotations = annotations
        except Exception as e:
            return {
                "sdk_importable": False,
                "import_error": str(e),
                "package": package_info,
                "reasoning_levels": await self.get_reasoning_levels(),
            }

        method_support = {
            "status": self._has_any_method(client_obj, "status", "get_status"),
            "auth_status": self._has_any_method(
                client_obj, "auth_status", "get_auth_status", "auth"
            ),
            "models": self._has_any_method(
                client_obj, "models", "list_models", "get_models"
            ),
            "list_sessions": self._has_any_method(
                client_obj, "list_sessions", "sessions", "get_sessions"
            ),
            "delete_session": self._has_any_method(
                client_obj, "delete_session", "remove_session"
            ),
        }

        hooks_support = {
            "hooks_container": "hooks" in session_config_annotations,
            "direct_callback_keys": [
                key
                for key in (
                    "on_user_input_request",
                    "on_permission_request",
                    "on_pre_tool_use",
                    "on_error_occurred",
                )
                if key in session_config_annotations
            ],
        }

        return {
            "sdk_importable": True,
            "package": package_info,
            "prerelease_opt_in_enabled": bool(
                getattr(self.config, "copilot_enable_prerelease_features", False)
            ),
            "preview_distribution_detected": self._is_prerelease_version(
                str(package_info.get("version") or "")
            ),
            "method_support": method_support,
            "hooks_support": hooks_support,
            "reasoning_levels": await self.get_reasoning_levels(),
        }

    async def get_doctor_report(self) -> Dict[str, Any]:
        """Return an operational doctor report for Copilot provider diagnostics."""
        status = await self.get_status()
        capabilities = await self.get_capabilities()
        report: Dict[str, Any] = {
            "health": status.get("health", "unknown"),
            "reason": status.get("reason"),
            "runtime": status.get("runtime", {}),
            "package": capabilities.get("package", {}),
            "capabilities": capabilities,
            "status_probe": status,
            "warnings": [],
        }

        package_name = str(report["package"].get("distribution") or "")
        package_version = str(report["package"].get("version") or "")
        prerelease_opt_in = bool(
            getattr(self.config, "copilot_enable_prerelease_features", False)
        )
        if package_name == "copilot":
            report["warnings"].append(
                "Detected legacy 'copilot' package distribution; expected 'github-copilot-sdk'."
            )
        if not capabilities.get("sdk_importable", False):
            report["warnings"].append("Copilot SDK Python module is not importable.")
        if self._is_prerelease_version(package_version) and not prerelease_opt_in:
            report["warnings"].append(
                "Preview SDK detected but prerelease opt-in is disabled "
                "(COPILOT_ENABLE_PRERELEASE_FEATURES=false)."
            )

        return report

    def forget_session(self, user_id: int, working_directory: Path) -> None:
        """Remove stored session (e.g. after /new command)."""
        key = self._session_key(user_id, working_directory)
        if key in self._session_map:
            self._session_map.pop(key, None)
            self._persist_session_map()

    def _load_mcp_servers(self, env_value_mode: str = "raw") -> List[Dict[str, Any]]:
        """Convert Claude-format MCP config to Copilot SDK MCPServerConfig list."""
        enable_mcp: bool = bool(getattr(self.config, "enable_mcp", False))
        mcp_config_path = getattr(self.config, "mcp_config_path", None)

        if not enable_mcp or not mcp_config_path:
            return []

        try:
            with open(mcp_config_path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load MCP config for Copilot", error=str(e))
            return []

        servers: List[Dict[str, Any]] = []
        for _name, cfg in raw.get("mcpServers", {}).items():
            url: Optional[str] = cfg.get("url")
            if url:
                srv_type = "sse" if "sse" in url else "http"
                servers.append(
                    {
                        "type": srv_type,
                        "url": url,
                        "tools": cfg.get("tools", ["*"]),
                    }
                )
            else:
                # Local stdio server
                env_map = dict(cfg.get("env", {}) or {})
                if env_value_mode == "omit":
                    env_map = {}
                elif env_value_mode == "masked":
                    env_map = {k: "***" for k in env_map}

                servers.append(
                    {
                        "type": "stdio",
                        "command": cfg.get("command", ""),
                        "args": cfg.get("args", []),
                        "env": env_map,
                        "tools": cfg.get("tools", ["*"]),
                    }
                )

        logger.info(
            "Loaded MCP servers for Copilot",
            count=len(servers),
            config_path=str(mcp_config_path),
            env_value_mode=env_value_mode,
        )
        return servers

    async def shutdown(self) -> None:
        """Stop the CopilotClient."""
        if self._client:
            try:
                await self._client.stop()
                logger.info("CopilotClient stopped")
            except Exception as e:
                logger.warning("Error stopping CopilotClient", error=str(e))
            finally:
                self._client = None

    @staticmethod
    def _payload_bucket(size: int) -> str:
        if size < 4096:
            return "small"
        if size < 32768:
            return "medium"
        if size < 262144:
            return "large"
        return "xlarge"

    @staticmethod
    def _safe_unsubscribe(unsubscribe: Any) -> None:
        if not unsubscribe:
            return
        try:
            if callable(unsubscribe):
                unsubscribe()
        except Exception as e:
            logger.warning("Failed to unsubscribe Copilot stream", error=str(e))

    def _local_session_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for key, sid in sorted(self._session_map.items()):
            if ":" not in key:
                continue
            user_str, project = key.split(":", 1)
            rows.append(
                {
                    "session_id": sid,
                    "user_id": self._safe_int(user_str),
                    "project_path": project,
                    "source": "local_map",
                }
            )
        return rows

    def _normalize_sdk_sessions_payload(self, payload: Any) -> List[Dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, dict):
            for key in ("sessions", "items", "data"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    payload = nested
                    break

        if not isinstance(payload, list):
            return []

        rows: List[Dict[str, Any]] = []
        for item in payload:
            row = self._session_row_from_obj(item)
            if row:
                rows.append(row)
        return rows

    def _session_row_from_obj(self, item: Any) -> Optional[Dict[str, Any]]:
        if isinstance(item, dict):
            getter = item.get
        else:

            def getter(k: str, default: Any = None) -> Any:
                return getattr(item, k, default)

        session_id = (
            getter("session_id")
            or getter("sessionId")
            or getter("id")
            or getter("session")
        )
        if not session_id:
            return None

        project_path = (
            getter("project_path")
            or getter("projectPath")
            or getter("workspace_path")
            or getter("workspacePath")
            or getter("cwd")
            or ""
        )
        user_id = getter("user_id") or getter("userId")

        return {
            "session_id": str(session_id),
            "user_id": self._safe_int(user_id),
            "project_path": str(project_path),
            "source": "sdk",
        }

    def _detect_sdk_package(self) -> Dict[str, Any]:
        distribution = None
        version = None
        for name in ("github-copilot-sdk", "copilot"):
            try:
                version = importlib_metadata.version(name)
                distribution = name
                break
            except importlib_metadata.PackageNotFoundError:
                continue

        module_spec = importlib.util.find_spec("copilot")
        module_path = module_spec.origin if module_spec else None
        return {
            "distribution": distribution,
            "version": version,
            "module_found": bool(module_spec),
            "module_path": module_path,
        }

    @staticmethod
    def _version_at_least(version: str, minimum: tuple[int, int, int]) -> bool:
        match = _SEMVER_RE.match(version.strip())
        if not match:
            return False
        parsed = tuple(int(part) for part in match.groups())
        return parsed >= minimum

    @staticmethod
    def _is_prerelease_version(version: str) -> bool:
        lowered = version.lower()
        return any(tag in lowered for tag in ("preview", "alpha", "beta", "rc"))

    @staticmethod
    def _has_any_method(target: Any, *names: str) -> bool:
        return any(hasattr(target, name) for name in names)

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _redact_sensitive(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            redacted: Dict[str, Any] = {}
            for k, v in payload.items():
                key = str(k).lower()
                if any(
                    token in key
                    for token in ("token", "secret", "password", "key", "authorization")
                ):
                    redacted[k] = "***"
                else:
                    redacted[k] = self._redact_sensitive(v)
            return redacted
        if isinstance(payload, list):
            return [self._redact_sensitive(x) for x in payload]
        return payload
