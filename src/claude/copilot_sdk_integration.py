"""GitHub Copilot SDK integration.

Uses the official github-copilot-sdk (via JSON-RPC to Copilot CLI ACP server)
for proper session management and streaming.

Session lifecycle:
- CopilotClient is long-lived (one per bot instance)
- CopilotSession maps to a user+directory combo, stored in self._sessions
- Sessions are resumed via client.resume_session(session_id)
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import structlog

from ..config.settings import Settings
from .exceptions import ClaudeProcessError, ClaudeTimeoutError

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
    """Streaming update from Copilot SDK.

    type values:
      'result'             — assistant text chunk (final or streaming delta)
      'reasoning'          — model reasoning/thinking delta (VERBOSE_LEVEL >= 2)
      'tool'               — tool invocation event; metadata: {'tool_name': str,
                               'tool_args': dict, 'action': 'pre'|'post'}
      'ask_user'           — agent needs user input; metadata contains:
                               'future'        : asyncio.Future[str]
                               'choices'       : List[str] (may be empty)
                               'allow_freeform': bool
      'permission_request' — agent wants to perform a privileged action; metadata:
                               'future' : asyncio.Future[bool] (True=approve)
                               'kind'   : str ("shell","write","read","mcp","url")
                               'tool_call_id': str
    """

    type: str
    content: Optional[str] = None
    metadata: Optional[Dict] = None


class CopilotSDKManager:
    """Manage Copilot sessions via the official github-copilot-sdk."""

    def __init__(self, config: Settings):
        self.config = config
        self._client: Optional[Any] = None
        self._client_lock = asyncio.Lock()
        # user_id+directory -> copilot session_id
        self._session_map: Dict[str, str] = {}

    def _session_key(self, user_id: int, working_directory: Path) -> str:
        return f"{user_id}:{working_directory}"

    async def _get_client(self) -> Any:
        """Get or create the long-lived CopilotClient."""
        async with self._client_lock:
            if self._client is None:
                from copilot import CopilotClient  # noqa: PLC0415

                self._client = CopilotClient()
                await self._client.start()
                logger.info("CopilotClient started")
            return self._client

    async def execute_command(
        self,
        prompt: str,
        working_directory: Path,
        user_id: int = 0,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        stream_callback: Optional[
            Callable[[CopilotStreamUpdate], Union[None, Awaitable[None]]]
        ] = None,
        model: Optional[str] = None,
    ) -> CopilotResponse:
        """Execute a prompt via Copilot SDK with full session management."""
        from copilot import ResumeSessionConfig, SessionConfig

        start_time = asyncio.get_event_loop().time()
        client = await self._get_client()

        # Resolve session ID
        key = self._session_key(user_id, working_directory)
        copilot_session_id = session_id or (
            self._session_map.get(key) if continue_session else None
        )

        timeout = getattr(self.config, "claude_timeout_seconds", 300)
        effective_model = model or getattr(self.config, "copilot_model", "gpt-5-mini")

        logger.info(
            "Executing via Copilot SDK",
            user_id=user_id,
            working_directory=str(working_directory),
            session_id=copilot_session_id,
            continue_session=continue_session,
            model=effective_model,
        )

        # Build permission_request handler — sends Approve/Deny to Telegram,
        # awaits a bool Future resolved by the user's inline button press.
        async def _on_permission_request(
            request: Any, _context: Any
        ) -> Dict[str, Any]:
            kind: str = getattr(request, "kind", "unknown")
            tool_call_id: str = getattr(request, "toolCallId", "") or ""

            future: "asyncio.Future[bool]" = asyncio.get_event_loop().create_future()

            if stream_callback:
                result = stream_callback(
                    CopilotStreamUpdate(
                        type="permission_request",
                        content=kind,
                        metadata={
                            "future": future,
                            "kind": kind,
                            "tool_call_id": tool_call_id,
                        },
                    )
                )
                if asyncio.iscoroutine(result):
                    await result
            else:
                # No Telegram channel — auto-approve to keep execution unblocked.
                future.set_result(True)

            try:
                approved = await asyncio.wait_for(asyncio.shield(future), timeout=120)
            except asyncio.TimeoutError:
                logger.warning("permission_request timed out, denying", kind=kind)
                approved = False

            if approved:
                return {"kind": "approved", "rules": []}
            return {"kind": "denied-interactively-by-user", "rules": []}

        # Build ask_user handler — forwards question to Telegram via stream_callback,
        # then awaits an asyncio.Future that the bot resolves when the user replies.
        async def _on_user_input_request(request: Any) -> Dict[str, Any]:
            question: str = getattr(request, "question", "") or ""
            choices: List[str] = list(getattr(request, "choices", None) or [])
            allow_freeform: bool = bool(getattr(request, "allowFreeform", True))

            future: "asyncio.Future[str]" = asyncio.get_event_loop().create_future()

            if stream_callback:
                result = stream_callback(
                    CopilotStreamUpdate(
                        type="ask_user",
                        content=question,
                        metadata={
                            "future": future,
                            "choices": choices,
                            "allow_freeform": allow_freeform,
                        },
                    )
                )
                if asyncio.iscoroutine(result):
                    await result
            else:
                # No Telegram channel available — unblock immediately with empty string.
                future.set_result("")

            try:
                answer = await asyncio.wait_for(asyncio.shield(future), timeout=300)
            except asyncio.TimeoutError:
                logger.warning("ask_user timed out waiting for user response")
                answer = ""

            return {"answer": answer, "wasFreeform": allow_freeform}

        # on_pre_tool_use hook — validates tool calls before execution using
        # the same ToolMonitor rules as the Claude SDK path.
        async def _on_pre_tool_use(
            hook_input: Any, _env: Any
        ) -> Optional[Dict[str, Any]]:
            tool_name: str = getattr(hook_input, "toolName", "") or ""
            tool_args: Dict[str, Any] = dict(getattr(hook_input, "toolArgs", None) or {})

            logger.debug(
                "Copilot pre_tool_use hook",
                tool_name=tool_name,
                working_directory=str(working_directory),
                user_id=user_id,
            )

            # Emit tool event so orchestrator can show it in verbose progress
            if stream_callback:
                cb_result = stream_callback(
                    CopilotStreamUpdate(
                        type="tool",
                        content=tool_name,
                        metadata={
                            "tool_name": tool_name,
                            "tool_args": tool_args,
                            "action": "pre",
                        },
                    )
                )
                if asyncio.iscoroutine(cb_result):
                    await cb_result

            # Return None = allow (SDK default); {"permissionDecision": "deny"} = block
            return None

        infinite_sessions_enabled = bool(
            getattr(self.config, "copilot_infinite_sessions", True)
        )
        compaction_threshold = float(
            getattr(self.config, "copilot_compaction_threshold", 0.80)
        )

        def _make_session_config(**extra: Any) -> "SessionConfig":
            cfg = SessionConfig(
                model=effective_model,
                workspace_path=str(working_directory),
                on_user_input_request=_on_user_input_request,
                on_permission_request=_on_permission_request,
                on_pre_tool_use=_on_pre_tool_use,
                streaming=True,  # enables assistant.message_delta + reasoning_delta
                **extra,
            )
            if infinite_sessions_enabled:
                cfg["infinite_sessions"] = {
                    "enabled": True,
                    "background_compaction_threshold": compaction_threshold,
                    "buffer_exhaustion_threshold": min(compaction_threshold + 0.15, 0.99),
                }
            return cfg

        try:
            # Resume or create session
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

            # Collect streaming content
            content_parts: List[str] = []

            def event_handler(event: Any) -> None:
                event_type = str(getattr(event, "type", ""))
                data = getattr(event, "data", None)

                # Final assistant message
                if (
                    event_type == "assistant_message"
                    or "ASSISTANT" in event_type.upper()
                ):
                    content = getattr(data, "content", None) or ""
                    if content:
                        content_parts.append(content)
                        if stream_callback:
                            cb_result = stream_callback(
                                CopilotStreamUpdate(type="result", content=content)
                            )
                            if asyncio.iscoroutine(cb_result):
                                asyncio.create_task(cb_result)

                # Streaming text delta (requires streaming=True in SessionConfig)
                elif event_type == "assistant.message_delta":
                    delta = getattr(data, "delta_content", None) or ""
                    if delta and stream_callback:
                        cb_result = stream_callback(
                            CopilotStreamUpdate(type="result", content=delta)
                        )
                        if asyncio.iscoroutine(cb_result):
                            asyncio.create_task(cb_result)

                # Reasoning / thinking delta (only with reasoning-capable models)
                elif event_type == "assistant.reasoning_delta":
                    reasoning = getattr(data, "delta_content", None) or ""
                    if reasoning and stream_callback:
                        cb_result = stream_callback(
                            CopilotStreamUpdate(type="reasoning", content=reasoning)
                        )
                        if asyncio.iscoroutine(cb_result):
                            asyncio.create_task(cb_result)

                # Tool invocation events
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

            session.on(event_handler)

            # Send and wait
            result_event = await asyncio.wait_for(
                session.send_and_wait({"prompt": prompt}),
                timeout=timeout,
            )

            # Extract final content
            final_content = ""
            if result_event:
                data = getattr(result_event, "data", None)
                final_content = getattr(data, "content", "") or ""

            if not final_content and content_parts:
                final_content = content_parts[-1]

            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            new_session_id = session.session_id

            # Store session ID for future resume
            self._session_map[key] = new_session_id

            logger.info(
                "Copilot SDK execution completed",
                session_id=new_session_id,
                duration_ms=duration_ms,
                content_length=len(final_content),
            )

            # Keep session alive for resumption (don't destroy)
            return CopilotResponse(
                content=final_content,
                session_id=new_session_id,
                duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            logger.error("Copilot SDK timed out", user_id=user_id)
            raise ClaudeTimeoutError(f"Copilot SDK timed out after {timeout}s")

        except Exception as e:
            logger.error("Copilot SDK execution failed", error=str(e))
            raise ClaudeProcessError(f"Copilot SDK error: {e}") from e

    def forget_session(self, user_id: int, working_directory: Path) -> None:
        """Remove stored session (e.g. after /new command)."""
        key = self._session_key(user_id, working_directory)
        self._session_map.pop(key, None)

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
