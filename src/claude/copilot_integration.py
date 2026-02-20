"""GitHub Copilot CLI integration.

Features:
- Async subprocess execution
- Session management
- Tool permission handling
- Output parsing
"""

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog

from ..config.settings import Settings
from .exceptions import (
    ClaudeMCPError,
    ClaudeParsingError,
    ClaudeProcessError,
    ClaudeTimeoutError,
)

logger = structlog.get_logger()


# Copilot CLI available models
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

    type: str  # 'thinking', 'tool', 'result', 'error'
    content: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[Dict] = None
    metadata: Optional[Dict] = None


class CopilotProcessManager:
    """Manage Copilot CLI subprocess execution."""

    def __init__(self, config: Settings):
        """Initialize process manager with configuration."""
        self.config = config
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}
        self.session_counter = 0

    def _get_copilot_binary(self) -> str:
        """Get Copilot CLI binary path."""
        # Check config first
        if hasattr(self.config, 'copilot_binary_path') and self.config.copilot_binary_path:
            return self.config.copilot_binary_path
        # Default to 'copilot'
        return "copilot"

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

        # Generate session ID if not provided
        if not session_id:
            session_id = str(uuid.uuid4())
            self.session_counter += 1

        # Build command
        cmd = self._build_command(
            prompt=prompt,
            session_id=session_id,
            continue_session=continue_session,
            model=model or getattr(self.config, 'copilot_model', 'gpt-5.3-codex'),
        )

        process_id = str(uuid.uuid4())

        logger.info(
            "Starting Copilot process",
            process_id=process_id,
            working_directory=str(working_directory),
            session_id=session_id,
            continue_session=continue_session,
            model=model,
        )

        try:
            # Start process
            process = await self._start_process(cmd, working_directory)
            self.active_processes[process_id] = process

            # Handle output
            result = await asyncio.wait_for(
                self._handle_process_output(process, stream_callback),
                timeout=getattr(self.config, 'claude_timeout_seconds', 300),
            )

            duration_ms = int((asyncio.get_event_loop().time() - start_time) * 1000)
            result.duration_ms = duration_ms

            logger.info(
                "Copilot process completed",
                process_id=process_id,
                duration_ms=duration_ms,
            )

            return result

        except asyncio.TimeoutError:
            if process_id in self.active_processes:
                self.active_processes[process_id].kill()
                await self.active_processes[process_id].wait()

            logger.error(
                "Copilot process timed out",
                process_id=process_id,
            )
            raise ClaudeTimeoutError(
                f"Copilot timed out after {getattr(self.config, 'claude_timeout_seconds', 300)}s"
            )

        except Exception as e:
            logger.error(
                "Copilot process failed",
                process_id=process_id,
                error=str(e),
            )
            raise

        finally:
            if process_id in self.active_processes:
                del self.active_processes[process_id]

    def _build_command(
        self,
        prompt: str,
        session_id: Optional[str],
        continue_session: bool,
        model: str,
    ) -> List[str]:
        """Build Copilot CLI command."""
        cmd = [self._get_copilot_binary()]

        # Continue existing session
        if continue_session and session_id:
            cmd.extend(["--resume", session_id])
            if prompt:
                cmd.extend(["-i", prompt])  # Interactive continue
        else:
            # New session with prompt
            cmd.extend(["-p", prompt])

        # Model selection
        if model:
            cmd.extend(["--model", model])

        # Permissions - allow all for bot usage
        cmd.extend(["--allow-all"])

        # Silent mode for easier parsing
        cmd.extend(["--silent"])

        # Disable streaming for simpler output parsing
        cmd.extend(["--stream", "off"])

        logger.debug("Built Copilot command", command=cmd)
        return cmd

    async def _start_process(
        self, cmd: List[str], cwd: Path
    ) -> asyncio.subprocess.Process:
        """Start Copilot subprocess."""
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
        )

    async def _handle_process_output(
        self,
        process: asyncio.subprocess.Process,
        stream_callback: Optional[Callable],
    ) -> CopilotResponse:
        """Handle process output with parsing."""
        output_lines = []
        error_lines = []

        # Read stdout
        stdout_task = asyncio.create_task(
            process.stdout.read() if hasattr(process.stdout, 'read') else 
            self._read_all(process.stdout)
        )
        
        # Read stderr
        stderr_task = asyncio.create_task(
            self._read_all(process.stderr)
        )

        # Wait for both
        try:
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            stdout_output = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_output = stderr.decode("utf-8", errors="replace") if stderr else ""
        except Exception as e:
            logger.warning("Error reading process output", error=str(e))
            stdout_output = ""
            stderr_output = ""

        # Parse output
        content = self._parse_output(stdout_output, stderr_output, stream_callback)

        # Wait for process to complete
        return_code = await process.wait()

        if return_code != 0 and not content:
            error_msg = stderr_output or f"Copilot exited with code {return_code}"
            logger.error("Copilot process failed", return_code=return_code, stderr=error_msg)
            raise ClaudeProcessError(f"Copilot error: {error_msg}")

        return CopilotResponse(
            content=content,
            session_id="",  # Copilot doesn't expose session ID in output
        )

    async def _read_all(self, stream) -> bytes:
        """Read all bytes from stream."""
        if hasattr(stream, 'read'):
            return await stream.read()
        return b""

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        stream_callback: Optional[Callable],
    ) -> str:
        """Parse Copilot CLI output.
        
        Copilot outputs in mixed format:
        - HTML-like tags: <p>...</p>
        - Tool invocations: ● <tool_name> ...
        - Plain text responses
        """
        lines = stdout.split('\n') if stdout else []
        
        content_parts = []
        tool_calls = []
        
        for line in lines:
            line = line.strip()
            
            if not line:
                continue
            
            # Handle HTML-like tags - extract text content
            if line.startswith('<') and line.endswith('>'):
                # Skip pure tags without content
                if '●' in line:
                    # Parse tool invocations
                    tool_match = re.search(r'●\s*<([^>]+)>(.*)', line)
                    if tool_match:
                        tool_name = tool_match.group(1).strip()
                        tool_args = tool_match.group(2).strip()
                        
                        tool_call = {
                            "name": tool_name,
                            "input": {"command": tool_args},
                        }
                        tool_calls.append(tool_call)
                        
                        if stream_callback:
                            try:
                                asyncio.create_task(stream_callback(CopilotStreamUpdate(
                                    type="tool",
                                    content=tool_args,
                                    tool_name=tool_name,
                                    tool_input={"command": tool_args},
                                )))
                            except Exception:
                                pass
                continue
            
            # Skip stats lines (contain /// or similar)
            if line.startswith('///') or line.startswith('...'):
                continue
            
            # Collect content
            if line and not line.startswith('$'):
                content_parts.append(line)

        # Send final result via callback
        if stream_callback:
            try:
                content = '\n'.join(content_parts)
                asyncio.create_task(stream_callback(CopilotStreamUpdate(
                    type="result",
                    content=content,
                    metadata={"tools_used": len(tool_calls)} if tool_calls else None,
                )))
            except Exception:
                pass

        # Clean HTML tags from content
        final_content = re.sub(r'<[^>]+>', '', '\n'.join(content_parts)).strip()
        
        return final_content

    async def kill_all_processes(self) -> None:
        """Kill all active processes."""
        logger.info("Killing all Copilot processes", count=len(self.active_processes))

        for process_id, process in self.active_processes.items():
            try:
                process.kill()
                await process.wait()
            except Exception as e:
                logger.warning("Failed to kill process", process_id=process_id, error=str(e))

        self.active_processes.clear()

    def get_active_process_count(self) -> int:
        """Get number of active processes."""
        return len(self.active_processes)
