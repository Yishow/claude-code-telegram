"""GitHub Copilot SDK integration.

Uses github-copilot-sdk via JSON-RPC and exposes a bot-friendly runtime
surface (interactive bridge, status/introspection, session operations,
policy-aware runtime controls, and reliability guardrails).
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import structlog

from ..config.settings import Settings
from .copilot_interaction_bridge import CopilotInteractionBridge
from .exceptions import (
    ClaudeProcessError,
    ClaudeTimeoutError,
    CopilotAuthenticationError,
)
from .monitor import ToolMonitor

logger = structlog.get_logger()


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
        self.interaction_bridge = interaction_bridge or CopilotInteractionBridge()

        self._client: Optional[Any] = None
        self._client_lock = asyncio.Lock()
        self._session_map: Dict[str, str] = {}

        self._runtime_controls: Dict[str, Any] = {
            "reasoning_effort": getattr(config, "copilot_reasoning_default", "medium"),
            "skill_directories": list(getattr(config, "copilot_skill_directories", []) or []),
            "disabled_skills": list(getattr(config, "copilot_disabled_skills", []) or []),
            "mcp_env_value_mode": getattr(config, "mcp_env_value_mode", "raw"),
            "external_cli_server": getattr(config, "copilot_external_cli_server", None),
        }

        raw_store = getattr(config, "copilot_session_store_path", Path("data/copilot-session-map.json"))
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
            "reasoning_effort": self._runtime_controls.get("reasoning_effort", "medium"),
            "skill_directories": list(self._runtime_controls.get("skill_directories", []) or []),
            "disabled_skills": list(self._runtime_controls.get("disabled_skills", []) or []),
            "mcp_env_value_mode": self._runtime_controls.get("mcp_env_value_mode", "raw"),
            "external_cli_server": self._runtime_controls.get("external_cli_server"),
            "config_dir_policy": getattr(self.config, "copilot_config_dir_policy", "global"),
        }

    def update_runtime_controls(
        self,
        *,
        reasoning_effort: Optional[str] = None,
        skill_directories: Optional[List[str]] = None,
        disabled_skills: Optional[List[str]] = None,
        mcp_env_value_mode: Optional[str] = None,
        external_cli_server: Optional[str] = None,
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
        if external_cli_server is not None:
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

        project_hash = hashlib.sha1(str(working_directory.resolve()).encode("utf-8")).hexdigest()[:12]
        base = Path(self.config.approved_directory) / ".copilot-config"
        target = base / project_hash
        target.mkdir(parents=True, exist_ok=True)
        return str(target)

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
        copilot_session_id = session_id or (self._session_map.get(key) if continue_session else None)

        timeout = int(getattr(self.config, "claude_timeout_seconds", 300))
        effective_model = model or getattr(self.config, "copilot_model", "gpt-5-mini")
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

        async def _emit(update: CopilotStreamUpdate) -> None:
            if not stream_callback:
                return
            result = stream_callback(update)
            if asyncio.iscoroutine(result):
                await result

        async def _on_permission_request(request: Any, _context: Any) -> Dict[str, Any]:
            kind: str = getattr(request, "kind", "unknown")
            tool_call_id: str = getattr(request, "toolCallId", "") or ""

            if stream_callback and chat_id:
                meta = await self.interaction_bridge.create_permission_request(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    kind=kind,
                    tool_call_id=tool_call_id,
                )
                await _emit(
                    CopilotStreamUpdate(
                        type="permission_request",
                        content=kind,
                        metadata=meta,
                    )
                )
                approved = bool(
                    await self.interaction_bridge.wait_for_result(meta["interaction_id"])
                )
            else:
                approved = True

            logger.info(
                "Copilot permission decision",
                kind=kind,
                approved=approved,
                user_id=user_id,
            )

            if approved:
                return {"kind": "approved", "rules": []}
            return {"kind": "denied-interactively-by-user", "rules": []}

        async def _on_user_input_request(request: Any) -> Dict[str, Any]:
            question: str = getattr(request, "question", "") or ""
            choices: List[str] = list(getattr(request, "choices", None) or [])
            allow_freeform: bool = bool(getattr(request, "allowFreeform", True))

            if stream_callback and chat_id:
                meta = await self.interaction_bridge.create_ask_user(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    question=question,
                    choices=choices,
                    allow_freeform=allow_freeform,
                )
                await _emit(
                    CopilotStreamUpdate(
                        type="ask_user",
                        content=question,
                        metadata=meta,
                    )
                )
                answer = await self.interaction_bridge.wait_for_result(meta["interaction_id"])
                if not isinstance(answer, str):
                    answer = ""
            else:
                answer = ""

            logger.info(
                "Copilot ask_user resolved",
                user_id=user_id,
                has_answer=bool(answer),
                allow_freeform=allow_freeform,
            )

            return {"answer": answer, "wasFreeform": allow_freeform}

        async def _on_error_occurred(hook_input: Any, _env: Any) -> Optional[Dict[str, Any]]:
            error_msg: str = getattr(hook_input, "error", "") or ""
            error_context: str = getattr(hook_input, "errorContext", "") or ""
            recoverable: bool = bool(getattr(hook_input, "recoverable", False))

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

        async def _on_pre_tool_use(hook_input: Any, _env: Any) -> Optional[Dict[str, Any]]:
            tool_name: str = getattr(hook_input, "toolName", "") or ""
            tool_args: Dict[str, Any] = dict(getattr(hook_input, "toolArgs", None) or {})

            await _emit(
                CopilotStreamUpdate(
                    type="tool",
                    content=tool_name,
                    metadata={"tool_name": tool_name, "tool_args": tool_args, "action": "pre"},
                )
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

            await _emit(
                CopilotStreamUpdate(
                    type="tool_denied",
                    content=tool_name,
                    metadata={"tool_name": tool_name, "reason": error or "policy_denied"},
                )
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

        infinite_sessions_enabled = bool(getattr(self.config, "copilot_infinite_sessions", True))
        compaction_threshold = float(getattr(self.config, "copilot_compaction_threshold", 0.80))
        mcp_servers = self._load_mcp_servers(runtime_controls.get("mcp_env_value_mode", "raw"))

        def _make_session_config(**extra: Any) -> "SessionConfig":
            cfg = SessionConfig(
                model=effective_model,
                workspace_path=str(working_directory),
                on_user_input_request=_on_user_input_request,
                on_permission_request=_on_permission_request,
                on_pre_tool_use=_on_pre_tool_use,
                on_error_occurred=_on_error_occurred,
                streaming=True,
                **extra,
            )

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

        unsubscribe: Any = None
        try:
            if copilot_session_id and continue_session:
                try:
                    session = await client.resume_session(
                        copilot_session_id,
                        ResumeSessionConfig(workspace_path=str(working_directory)),
                    )
                    logger.info("Resumed Copilot session", session_id=copilot_session_id)
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
                event_type = str(getattr(event, "type", ""))
                data = getattr(event, "data", None)

                if event_type == "assistant_message" or "ASSISTANT" in event_type.upper():
                    content = getattr(data, "content", None) or ""
                    if content:
                        content_parts.append(content)
                        if stream_callback:
                            cb_result = stream_callback(CopilotStreamUpdate(type="result", content=content))
                            if asyncio.iscoroutine(cb_result):
                                asyncio.create_task(cb_result)

                elif event_type == "assistant.message_delta":
                    delta = getattr(data, "delta_content", None) or ""
                    if delta and stream_callback:
                        cb_result = stream_callback(CopilotStreamUpdate(type="result", content=delta))
                        if asyncio.iscoroutine(cb_result):
                            asyncio.create_task(cb_result)

                elif event_type == "assistant.reasoning_delta":
                    reasoning = getattr(data, "delta_content", None) or ""
                    if reasoning and stream_callback:
                        cb_result = stream_callback(CopilotStreamUpdate(type="reasoning", content=reasoning))
                        if asyncio.iscoroutine(cb_result):
                            asyncio.create_task(cb_result)

                elif event_type in ("tool_use", "tool_result"):
                    tool_name = getattr(data, "tool_name", None) or ""
                    tool_args = getattr(data, "tool_args", None) or {}
                    action = "pre" if event_type == "tool_use" else "post"
                    if tool_name and stream_callback:
                        cb_result = stream_callback(
                            CopilotStreamUpdate(
                                type="tool",
                                content=tool_name,
                                metadata={"tool_name": tool_name, "tool_args": tool_args, "action": action},
                            )
                        )
                        if asyncio.iscoroutine(cb_result):
                            asyncio.create_task(cb_result)

                elif event_type in ("context_changed", "session.context_changed"):
                    if stream_callback:
                        cb_result = stream_callback(
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
                            )
                        )
                        if asyncio.iscoroutine(cb_result):
                            asyncio.create_task(cb_result)

            unsubscribe = session.on(event_handler)

            message_options: Dict[str, Any] = {"prompt": prompt}
            _tmp_image_path: Optional[str] = None
            if image_path:
                message_options["attachments"] = [{"type": "file", "path": image_path}]

            payload_size = len(prompt)
            if image_path:
                payload_size += 50000
            logger.info(
                "Copilot payload telemetry",
                payload_size_bucket=self._payload_bucket(payload_size),
                has_attachments=bool(image_path),
            )

            result_event = await asyncio.wait_for(
                session.send_and_wait(message_options),
                timeout=timeout,
            )

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
            logger.error("Copilot watchdog timeout", user_id=user_id, timeout_seconds=timeout)
            raise ClaudeTimeoutError(f"Copilot SDK timed out after {timeout}s")

        except Exception as e:
            text = str(e)
            lowered = text.lower()
            if "auth" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
                logger.error("Copilot authentication failed", error=text)
                raise CopilotAuthenticationError(f"Copilot authentication failed: {text}") from e

            logger.error("Copilot SDK execution failed", error=text)
            raise ClaudeProcessError(f"Copilot SDK error: {text}") from e

        finally:
            self._safe_unsubscribe(unsubscribe)

    async def get_status(self) -> Dict[str, Any]:
        """Collect Copilot runtime/introspection status."""
        status: Dict[str, Any] = {
            "runtime": {
                "client_started": self._client is not None,
                "fallback_mode": getattr(self.config, "copilot_fallback_mode", "sdk_then_cli"),
                "external_cli_server": self._runtime_controls.get("external_cli_server"),
                "config_dir_policy": getattr(self.config, "copilot_config_dir_policy", "global"),
            },
            "session": {
                "tracked_sessions": len(self._session_map),
                "store_path": str(self._session_store_path),
            },
            "model": {
                "default_model": getattr(self.config, "copilot_model", "gpt-5-mini"),
                "reasoning_effort": self._runtime_controls.get("reasoning_effort", "medium"),
            },
            "skills": {
                "skill_directories": list(self._runtime_controls.get("skill_directories", []) or []),
                "disabled_skills": list(self._runtime_controls.get("disabled_skills", []) or []),
            },
            "mcp": {
                "enabled": bool(getattr(self.config, "enable_mcp", False)),
                "env_value_mode": self._runtime_controls.get("mcp_env_value_mode", "raw"),
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
        """List known Copilot sessions."""
        rows: List[Dict[str, Any]] = []
        for key, sid in sorted(self._session_map.items()):
            user_str, project = key.split(":", 1)
            rows.append(
                {
                    "session_id": sid,
                    "user_id": int(user_str),
                    "project_path": project,
                    "source": "local_map",
                }
            )
        return rows

    async def delete_session(self, session_id: str) -> Dict[str, Any]:
        """Delete session from local map and SDK backend when available."""
        removed_keys = [k for k, v in self._session_map.items() if v == session_id]
        for k in removed_keys:
            self._session_map.pop(k, None)
        if removed_keys:
            self._persist_session_map()

        sdk_deleted = False
        client = self._client
        if client:
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

    def forget_session(self, user_id: int, working_directory: Path) -> None:
        """Remove stored session (e.g. after /new command)."""
        key = self._session_key(user_id, working_directory)
        if key in self._session_map:
            self._session_map.pop(key, None)
            self._persist_session_map()

    def _load_mcp_servers(self, env_value_mode: str) -> List[Dict[str, Any]]:
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
                servers.append({
                    "type": srv_type,
                    "url": url,
                    "tools": cfg.get("tools", ["*"]),
                })
            else:
                env_values = dict(cfg.get("env", {}))
                if env_value_mode == "omit":
                    env_values = {}
                elif env_value_mode == "masked":
                    env_values = {k: "***" for k in env_values.keys()}

                servers.append(
                    {
                        "type": "stdio",
                        "command": cfg.get("command", ""),
                        "args": cfg.get("args", []),
                        "env": env_values,
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

    def _redact_sensitive(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            redacted: Dict[str, Any] = {}
            for k, v in payload.items():
                key = str(k).lower()
                if any(token in key for token in ("token", "secret", "password", "key", "authorization")):
                    redacted[k] = "***"
                else:
                    redacted[k] = self._redact_sensitive(v)
            return redacted
        if isinstance(payload, list):
            return [self._redact_sensitive(x) for x in payload]
        return payload
