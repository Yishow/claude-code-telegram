"""GitHub Copilot CLI integration.

Features:
- Async subprocess execution
- Session management (reads session ID from ~/.copilot/session-state/)
- Tool permission handling
- Output parsing
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog
import yaml

from ..config.settings import Settings
from .exceptions import (
    ClaudeProcessError,
    ClaudeTimeoutError,
)

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


@dataclass
class CopilotStreamUpdate:
    """Streaming update from Copilot CLI."""

    type: str  # 'result', 'error'
    content: Optional[str] = None
    metadata: Optional[Dict] = None


class CopilotProcessManager:
    """Manage Copilot CLI subprocess execution."""

    def __init__(self, config: Settings):
        """Initialize process manager with configuration."""
        self.config = config
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}

    def _get_copilot_binary(self) -> str:
        """Get Copilot CLI binary path."""
        if (
            hasattr(self.config, "copilot_binary_path")
            and self.config.copilot_binary_path
        ):
            return self.config.copilot_binary_path
        return "copilot"

    def _find_session_id_for_directory(self, working_directory: Path) -> Optional[str]:
        """Find the most recent Copilot session ID for a given working directory.

        Reads workspace.yaml from ~/.copilot/session-state/<uuid>/ to find
        sessions matching the working directory, sorted by updated_at.
        """
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
                with open(workspace_file) as f:
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

        # Resolve session ID: use provided, or look up from filesystem
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
            "Starting Copilot process",
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

            timeout = getattr(self.config, "claude_timeout_seconds", 300)
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
                    "Copilot process failed", return_code=return_code, stderr=error_msg
                )
                raise ClaudeProcessError(f"Copilot error: {error_msg}")

            content = stdout.strip()

            logger.info(
                "Copilot process completed",
                process_id=process_id,
                duration_ms=duration_ms,
            )

            if stream_callback:
                try:
                    asyncio.create_task(
                        stream_callback(
                            CopilotStreamUpdate(type="result", content=content)
                        )
                    )
                except Exception:
                    pass

            # Try to find the new session ID from filesystem after execution
            new_session_id = (
                self._find_session_id_for_directory(working_directory) or ""
            )

            return CopilotResponse(
                content=content,
                session_id=new_session_id,
                duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            if process_id in self.active_processes:
                self.active_processes[process_id].kill()
                await self.active_processes[process_id].wait()
            timeout = getattr(self.config, "claude_timeout_seconds", 300)
            logger.error("Copilot process timed out", process_id=process_id)
            raise ClaudeTimeoutError(f"Copilot timed out after {timeout}s")

        except ClaudeProcessError:
            raise

        except Exception as e:
            logger.error("Copilot process failed", process_id=process_id, error=str(e))
            raise

        finally:
            self.active_processes.pop(process_id, None)

    def _build_command(
        self,
        prompt: str,
        session_id: Optional[str],
        continue_session: bool,
        model: str,
    ) -> List[str]:
        """Build Copilot CLI command.

        Uses -p for new sessions and --resume <id> -p for continuations.
        -s (silent) outputs only the agent response with no stats.
        """
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

    async def kill_all_processes(self) -> None:
        """Kill all active processes."""
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
        """Get number of active processes."""
        return len(self.active_processes)
