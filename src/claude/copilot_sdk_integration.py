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
from typing import Any, Callable, Dict, List, Optional

import structlog
from copilot import CopilotClient, ResumeSessionConfig, SessionConfig

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
    """Streaming update from Copilot SDK."""

    type: str  # 'result', 'error'
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
        stream_callback: Optional[Callable[[CopilotStreamUpdate], None]] = None,
        model: Optional[str] = None,
    ) -> CopilotResponse:
        """Execute a prompt via Copilot SDK with full session management."""
        from copilot import SessionConfig, ResumeSessionConfig

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
                    session = await client.create_session(
                        SessionConfig(
                            model=effective_model, workspace_path=str(working_directory)
                        )
                    )
            else:
                session = await client.create_session(
                    SessionConfig(
                        model=effective_model, workspace_path=str(working_directory)
                    )
                )

            # Collect streaming content
            content_parts: List[str] = []

            def event_handler(event: Any) -> None:
                event_type = getattr(event, "type", "")
                if (
                    str(event_type) == "assistant_message"
                    or "ASSISTANT" in str(event_type).upper()
                ):
                    content = (
                        getattr(getattr(event, "data", None), "content", None) or ""
                    )
                    if content:
                        content_parts.append(content)
                        if stream_callback:
                            asyncio.create_task(
                                stream_callback(
                                    CopilotStreamUpdate(type="result", content=content)
                                )
                            )

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
