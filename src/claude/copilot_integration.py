"""GitHub Copilot integration (SDK primary, CLI optional fallback)."""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog
import yaml

from ..config.settings import Settings
from .copilot_interaction_bridge import CopilotInteractionBridge
from .copilot_sdk_integration import CopilotSDKManager, CopilotStreamUpdate
from .exceptions import (
    ClaudeProcessError,
    ClaudeTimeoutError,
)
from .monitor import ToolMonitor

# Lazy import to avoid circular dependency; resolved at call time.
_ClaudeResponse = None


def _get_claude_response_class() -> type:
    global _ClaudeResponse
    if _ClaudeResponse is None:
        from .sdk_integration import ClaudeResponse  # noqa: PLC0415

        _ClaudeResponse = ClaudeResponse
    return _ClaudeResponse


logger = structlog.get_logger()


# Copilot CLI available models (matches `copilot --model --help`)
COPILOT_MODELS = [
    "claude-sonnet-4.5",
    "claude-haiku-4.5",
    "claude-opus-4.6",
    "claude-opus-4.6-fast",
    "claude-opus-4.5",
    "claude-sonnet-4",
    "gemini-3-pro-preview",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.2",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1",
    "gpt-5",
    "gpt-5.1-codex-mini",
    "gpt-5-mini",
    "gpt-4.1",
]

# Default session state directory
COPILOT_SESSION_DIR = Path.home() / ".copilot" / "session-state"


@dataclass
class CopilotResponse:
    """Response from Copilot CLI."""

    content: str
    session_id: str
    cost: float = 0.0
    duration_ms: int = 0
    num_turns: int = 1
    is_error: bool = False
    error_type: Optional[str] = None
    tools_used: List[Dict[str, Any]] = field(default_factory=list)


class CopilotProcessManager:
    """Manage Copilot SDK execution with optional CLI fallback."""

    def __init__(
        self,
        config: Settings,
        *,
        tool_monitor: Optional[ToolMonitor] = None,
        interaction_bridge: Optional[CopilotInteractionBridge] = None,
    ):
        self.config = config
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}
        self.sdk_manager = CopilotSDKManager(config)

    def _get_copilot_binary(self) -> str:
        if getattr(self.config, "copilot_binary_path", None):
            return self.config.copilot_binary_path
        return "copilot"

    def _find_session_id_for_directory(self, working_directory: Path) -> Optional[str]:
        """Find the most recent Copilot session ID for a given working directory."""
        if not COPILOT_SESSION_DIR.exists():
            return None

        best_session_id: Optional[str] = None
        best_updated_at: Optional[str] = None

        for session_dir in COPILOT_SESSION_DIR.iterdir():
            if not session_dir.is_dir():
                continue
            workspace_file = session_dir / "workspace.yaml"
            if not workspace_file.exists():
                continue
            try:
                with open(workspace_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                cwd = data.get("cwd", "")
                updated_at = data.get("updated_at", "")
                session_id = data.get("id", "")
                if Path(cwd) == working_directory and session_id:
                    if best_updated_at is None or updated_at > best_updated_at:
                        best_updated_at = updated_at
                        best_session_id = session_id
            except Exception:
                continue

        return best_session_id

    async def execute_command(
        self,
        prompt: str,
        working_directory: Path,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        stream_callback: Optional[Callable[[CopilotStreamUpdate], None]] = None,
        model: Optional[str] = None,
    ) -> CopilotResponse:
        """Execute Copilot CLI command."""
        start_time = asyncio.get_event_loop().time()

        resolved_session_id = session_id
        if continue_session and not resolved_session_id:
            resolved_session_id = self._find_session_id_for_directory(working_directory)

        cmd = self._build_command(
            prompt=prompt,
            session_id=resolved_session_id,
            continue_session=continue_session,
            model=model or getattr(self.config, "copilot_model", "gpt-5-mini"),
        )

        process_id = str(uuid.uuid4())
        logger.info(
            "Starting Copilot CLI process",
            process_id=process_id,
            working_directory=str(working_directory),
            session_id=resolved_session_id,
            continue_session=continue_session,
            model=model,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_directory),
            )
            self.active_processes[process_id] = process

            timeout = int(getattr(self.config, "claude_timeout_seconds", 300))
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            return_code = process.returncode

            stdout = (
                stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            )
            stderr = (
                stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            )

            if return_code != 0 and not stdout.strip():
                error_msg = stderr.strip() or f"Copilot exited with code {return_code}"
                logger.error(
                    "Copilot CLI process failed",
                    return_code=return_code,
                    stderr=error_msg,
                )
                raise ClaudeProcessError(f"Copilot error: {error_msg}")

            content = stdout.strip()
            if stream_callback:
                try:
                    asyncio.create_task(
                        stream_callback(
                            CopilotStreamUpdate(type="result", content=content)
                        )
                    )
                except Exception:
                    pass

            new_session_id = (
                self._find_session_id_for_directory(working_directory) or ""
            )
            return CopilotResponse(
                content=content, session_id=new_session_id, duration_ms=duration_ms
            )

        except asyncio.TimeoutError:
            if process_id in self.active_processes:
                self.active_processes[process_id].kill()
                await self.active_processes[process_id].wait()
            timeout = int(getattr(self.config, "claude_timeout_seconds", 300))
            logger.error("Copilot CLI process timed out", process_id=process_id)
            raise ClaudeTimeoutError(f"Copilot timed out after {timeout}s")

        finally:
            self.active_processes.pop(process_id, None)

    def _build_command(
        self,
        prompt: str,
        session_id: Optional[str],
        continue_session: bool,
        model: str,
    ) -> List[str]:
        """Build Copilot CLI command."""
        cmd = [self._get_copilot_binary()]

        if continue_session and session_id:
            cmd.extend(["--resume", session_id])

        cmd.extend(["-p", prompt])
        cmd.extend(["--allow-all"])
        cmd.extend(["-s"])

        if model:
            cmd.extend(["--model", model])

        logger.debug("Built Copilot command", command=cmd)
        return cmd

    async def execute_full(
        self,
        prompt: str,
        working_directory: Path,
        user_id: int = 0,
        chat_id: int = 0,
        message_thread_id: Optional[int] = None,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        stream_callback: Optional[Callable] = None,
        model: Optional[str] = None,
        image_path: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        skill_directories: Optional[List[str]] = None,
        disabled_skills: Optional[List[str]] = None,
        mcp_env_value_mode: Optional[str] = None,
        external_cli_server: Optional[str] = None,
    ) -> Any:
        """Execute command using Copilot SDK (with CLI fallback). Returns ClaudeResponse."""
        from .sdk_integration import StreamUpdate  # noqa: PLC0415

        ClaudeResponse = _get_claude_response_class()
        fallback_mode = getattr(self.config, "copilot_fallback_mode", "sdk_then_cli")

        logger.info(
            "Executing with Copilot SDK",
            working_directory=str(working_directory),
            session_id=session_id,
            continue_session=continue_session,
            fallback_mode=fallback_mode,
        )

        wrapped_callback: Optional[Callable] = None
        if stream_callback:

            async def _wrapped_callback(update: CopilotStreamUpdate) -> None:
                await stream_callback(
                    StreamUpdate(
                        type=update.type,
                        content=update.content,
                        metadata=update.metadata,
                    )
                )

            wrapped_callback = _wrapped_callback

        async def _execute_via_sdk(sdk_model: Optional[str]) -> Any:
            return await self.sdk_manager.execute_command(
                prompt=prompt,
                working_directory=working_directory,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                session_id=session_id,
                continue_session=continue_session,
                stream_callback=wrapped_callback,
                model=sdk_model,
                image_path=image_path,
                reasoning_effort=reasoning_effort,
                skill_directories=skill_directories,
                disabled_skills=disabled_skills,
                mcp_env_value_mode=mcp_env_value_mode,
                external_cli_server=external_cli_server,
            )

        try:
            copilot_response = await _execute_via_sdk(model)
        except Exception as sdk_error:
            sdk_error_text = str(sdk_error)
            if "failed to list models" in sdk_error_text.lower():
                logger.warning(
                    "Copilot SDK model listing failed, retrying without model",
                    error=sdk_error_text,
                )
                try:
                    copilot_response = await _execute_via_sdk("")
                except Exception as retry_error:
                    logger.warning(
                        "Copilot SDK retry without model failed",
                        error=str(retry_error),
                    )
                else:
                    return ClaudeResponse(
                        content=copilot_response.content,
                        session_id=copilot_response.session_id,
                        cost=copilot_response.cost,
                        duration_ms=copilot_response.duration_ms,
                        num_turns=copilot_response.num_turns,
                        is_error=copilot_response.is_error,
                        error_type=copilot_response.error_type,
                        tools_used=copilot_response.tools_used,
                    )

            logger.warning(
                "Copilot SDK failed, falling back to CLI",
                error=sdk_error_text,
            )

            if fallback_mode == "sdk_only":
                raise ClaudeProcessError(
                    f"Copilot SDK failed (sdk_only mode): {sdk_error}"
                )

            logger.info("Attempting Copilot CLI fallback", user_id=user_id)
            try:
                copilot_response = await self.execute_command(
                    prompt=prompt,
                    working_directory=working_directory,
                    session_id=session_id,
                    continue_session=continue_session,
                    stream_callback=wrapped_callback,
                    model=model,
                )
                logger.info("Copilot CLI fallback succeeded", user_id=user_id)
            except Exception as cli_error:
                logger.error(
                    "Copilot CLI fallback failed",
                    user_id=user_id,
                    error=str(cli_error),
                )
                raise

        return ClaudeResponse(
            content=copilot_response.content,
            session_id=copilot_response.session_id,
            cost=copilot_response.cost,
            duration_ms=copilot_response.duration_ms,
            num_turns=copilot_response.num_turns,
            is_error=copilot_response.is_error,
            error_type=copilot_response.error_type,
            tools_used=copilot_response.tools_used,
        )

    async def get_status(self) -> Dict[str, Any]:
        return await self.sdk_manager.get_status()

    async def list_sessions(self) -> List[Dict[str, Any]]:
        return await self.sdk_manager.list_sessions()

    async def delete_session(self, session_id: str) -> Dict[str, Any]:
        return await self.sdk_manager.delete_session(session_id)

    def switch_session(
        self, *, user_id: int, working_directory: Path, session_id: str
    ) -> Dict[str, Any]:
        return self.sdk_manager.switch_session(
            user_id=user_id,
            working_directory=working_directory,
            session_id=session_id,
        )

    def get_runtime_controls(self) -> Dict[str, Any]:
        return self.sdk_manager.get_runtime_controls()

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
        return self.sdk_manager.update_runtime_controls(
            reasoning_effort=reasoning_effort,
            skill_directories=skill_directories,
            disabled_skills=disabled_skills,
            mcp_env_value_mode=mcp_env_value_mode,
            external_cli_server=external_cli_server,
            external_cli_server_set=external_cli_server_set,
        )

    async def get_reasoning_levels(self) -> List[str]:
        return await self.sdk_manager.get_reasoning_levels()

    async def get_capabilities(self) -> Dict[str, Any]:
        return await self.sdk_manager.get_capabilities()

    async def get_doctor_report(self) -> Dict[str, Any]:
        return await self.sdk_manager.get_doctor_report()

    async def kill_all_processes(self) -> None:
        logger.info("Killing all Copilot processes", count=len(self.active_processes))
        for process_id, process in list(self.active_processes.items()):
            try:
                process.kill()
                await process.wait()
            except Exception as e:
                logger.warning(
                    "Failed to kill process", process_id=process_id, error=str(e)
                )
        self.active_processes.clear()

    def get_active_process_count(self) -> int:
        return len(self.active_processes)

    async def shutdown(self) -> None:
        await self.kill_all_processes()
        await self.sdk_manager.shutdown()
