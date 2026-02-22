"""High-level Claude Code integration facade.

Provides simple interface for bot handlers.
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import structlog

from ..config.settings import Settings
from .copilot_integration import CopilotProcessManager
from .sdk_integration import ClaudeResponse, ClaudeSDKManager, StreamUpdate
from .session import SessionManager

logger = structlog.get_logger()

# Provider type
ProviderType = Literal["claude", "copilot"]


class ClaudeIntegration:
    """Main integration point for Claude Code / Copilot."""

    def __init__(
        self,
        config: Settings,
        sdk_manager: Optional[ClaudeSDKManager] = None,
        copilot_manager: Optional[CopilotProcessManager] = None,
        session_manager: Optional[SessionManager] = None,
        tool_monitor: Optional[Any] = None,
        default_provider: Optional[ProviderType] = None,
    ):
        """Initialize Claude integration facade."""
        self.config = config
        if default_provider in ("claude", "copilot"):
            self.default_provider: ProviderType = default_provider
        else:
            configured_provider = getattr(config, "default_provider", "claude")
        self.default_provider = (
            "copilot" if configured_provider == "copilot" else "claude"
        )
        self.sdk_manager = sdk_manager or ClaudeSDKManager(config)
        self.session_manager = session_manager
        self.tool_monitor = tool_monitor
        self.copilot_manager = copilot_manager or CopilotProcessManager(
            config, tool_monitor=tool_monitor
        )

    async def run_command(
        self,
        prompt: str,
        working_directory: Path,
        user_id: int,
        chat_id: int = 0,
        message_thread_id: Optional[int] = None,
        session_id: Optional[str] = None,
        on_stream: Optional[Callable[[StreamUpdate], None]] = None,
        force_new: bool = False,
        provider: Optional[ProviderType] = None,
        copilot_model: Optional[str] = None,
        image_path: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        skill_directories: Optional[List[str]] = None,
        disabled_skills: Optional[List[str]] = None,
        mcp_env_value_mode: Optional[str] = None,
        external_cli_server: Optional[str] = None,
    ) -> ClaudeResponse:
        """Run Claude Code or Copilot command with full integration."""
        # Use specified provider or default
        actual_provider = provider or self.default_provider

        logger.info(
            f"Running {actual_provider} command",
            user_id=user_id,
            working_directory=str(working_directory),
            session_id=session_id,
            prompt_length=len(prompt),
            force_new=force_new,
            provider=actual_provider,
        )

        # If no session_id provided, try to find an existing session for this
        # user+directory combination (auto-resume).
        # Skip auto-resume when force_new is set (e.g. after /new command).
        if not session_id and not force_new:
            existing_session = await self._find_resumable_session(
                user_id, working_directory
            )
            if existing_session:
                session_id = existing_session.session_id
                logger.info(
                    "Auto-resuming existing session for project",
                    session_id=session_id,
                    project_path=str(working_directory),
                    user_id=user_id,
                )

        # Get or create session
        session = await self.session_manager.get_or_create_session(
            user_id, working_directory, session_id
        )

        # Execute command
        try:
            # Continue session if we have an existing session with a real ID
            is_new = getattr(session, "is_new_session", False)
            should_continue = not is_new and bool(session.session_id)

            # For new sessions, don't pass session_id to Claude Code
            claude_session_id = session.session_id if should_continue else None

            try:
                response = await self._execute(
                    prompt=prompt,
                    working_directory=working_directory,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    session_id=claude_session_id,
                    continue_session=should_continue,
                    stream_callback=on_stream,
                    provider=actual_provider,
                    copilot_model=copilot_model,
                    image_path=image_path,
                    reasoning_effort=reasoning_effort,
                    skill_directories=skill_directories,
                    disabled_skills=disabled_skills,
                    mcp_env_value_mode=mcp_env_value_mode,
                    external_cli_server=external_cli_server,
                )
            except Exception as resume_error:
                # If resume failed (e.g., session expired/missing on Claude's side),
                # retry as a fresh session.  The CLI returns a generic exit-code-1
                # when the session is gone, so we catch *any* error during resume.
                if should_continue:
                    logger.warning(
                        "Session resume failed, starting fresh session",
                        failed_session_id=claude_session_id,
                        error=str(resume_error),
                    )
                    # Clean up the stale session
                    await self.session_manager.remove_session(session.session_id)

                    # Create a fresh session and retry
                    session = await self.session_manager.get_or_create_session(
                        user_id, working_directory
                    )
                    response = await self._execute(
                        prompt=prompt,
                        working_directory=working_directory,
                        user_id=user_id,
                        chat_id=chat_id,
                        message_thread_id=message_thread_id,
                        session_id=None,
                        continue_session=False,
                        stream_callback=on_stream,
                        provider=actual_provider,
                        copilot_model=copilot_model,
                        image_path=image_path,
                        reasoning_effort=reasoning_effort,
                        skill_directories=skill_directories,
                        disabled_skills=disabled_skills,
                        mcp_env_value_mode=mcp_env_value_mode,
                        external_cli_server=external_cli_server,
                    )
                else:
                    raise

            # Update session (assigns real session_id for new sessions)
            await self.session_manager.update_session(session, response)

            # Ensure response has the session's final ID
            response.session_id = session.session_id

            if not response.session_id:
                logger.warning(
                    "No session_id after execution; session cannot be resumed",
                    user_id=user_id,
                )

            logger.info(
                "Claude command completed",
                session_id=response.session_id,
                cost=response.cost,
                duration_ms=response.duration_ms,
                num_turns=response.num_turns,
                is_error=response.is_error,
            )

            return response

        except Exception as e:
            logger.error(
                "Claude command failed",
                error=str(e),
                user_id=user_id,
                session_id=session.session_id,
            )
            raise

    async def _execute(
        self,
        prompt: str,
        working_directory: Path,
        user_id: int = 0,
        chat_id: int = 0,
        message_thread_id: Optional[int] = None,
        session_id: Optional[str] = None,
        continue_session: bool = False,
        stream_callback: Optional[Callable] = None,
        provider: Optional[ProviderType] = None,
        copilot_model: Optional[str] = None,
        image_path: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        skill_directories: Optional[List[str]] = None,
        disabled_skills: Optional[List[str]] = None,
        mcp_env_value_mode: Optional[str] = None,
        external_cli_server: Optional[str] = None,
    ) -> ClaudeResponse:
        """Execute command via SDK or Copilot."""
        actual_provider = provider or self.default_provider

        if actual_provider == "copilot":
            return await self.copilot_manager.execute_full(
                prompt=prompt,
                working_directory=working_directory,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                session_id=session_id,
                continue_session=continue_session,
                stream_callback=stream_callback,
                model=copilot_model,
                image_path=image_path,
                reasoning_effort=reasoning_effort,
                skill_directories=skill_directories,
                disabled_skills=disabled_skills,
                mcp_env_value_mode=mcp_env_value_mode,
                external_cli_server=external_cli_server,
            )

        return await self.sdk_manager.execute_command(
            prompt=prompt,
            working_directory=working_directory,
            session_id=session_id,
            continue_session=continue_session,
            stream_callback=stream_callback,
        )

    async def _find_resumable_session(
        self,
        user_id: int,
        working_directory: Path,
    ) -> Optional["ClaudeSession"]:  # noqa: F821
        """Find the most recent resumable session for a user in a directory.

        Returns the session if one exists that is non-expired and has a real
        (non-temporary) session ID from Claude. Returns None otherwise.
        """

        sessions = await self.session_manager._get_user_sessions(user_id)

        matching_sessions = [
            s
            for s in sessions
            if s.project_path == working_directory
            and bool(s.session_id)
            and not s.is_expired(self.config.session_timeout_hours)
        ]

        if not matching_sessions:
            return None

        return max(matching_sessions, key=lambda s: s.last_used)

    async def continue_session(
        self,
        user_id: int,
        working_directory: Path,
        prompt: Optional[str] = None,
        on_stream: Optional[Callable[[StreamUpdate], None]] = None,
    ) -> Optional[ClaudeResponse]:
        """Continue the most recent session."""
        logger.info(
            "Continuing session",
            user_id=user_id,
            working_directory=str(working_directory),
            has_prompt=bool(prompt),
        )

        # Get user's sessions
        sessions = await self.session_manager._get_user_sessions(user_id)

        # Find most recent session in this directory (exclude sessions without IDs)
        matching_sessions = [
            s
            for s in sessions
            if s.project_path == working_directory and bool(s.session_id)
        ]

        if not matching_sessions:
            logger.info("No matching sessions found", user_id=user_id)
            return None

        # Get most recent
        latest_session = max(matching_sessions, key=lambda s: s.last_used)

        # Continue session with default prompt if none provided
        # Claude CLI requires a prompt, so we use a placeholder
        return await self.run_command(
            prompt=prompt or "Please continue where we left off",
            working_directory=working_directory,
            user_id=user_id,
            session_id=latest_session.session_id,
            on_stream=on_stream,
        )

    async def get_session_info(
        self, session_id: str, user_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get session information (scoped to requesting user)."""
        return await self.session_manager.get_session_info(session_id, user_id)

    async def get_user_sessions(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all sessions for a user."""
        sessions = await self.session_manager._get_user_sessions(user_id)
        return [
            {
                "session_id": s.session_id,
                "project_path": str(s.project_path),
                "created_at": s.created_at.isoformat(),
                "last_used": s.last_used.isoformat(),
                "total_cost": s.total_cost,
                "message_count": s.message_count,
                "tools_used": s.tools_used,
                "expired": s.is_expired(self.config.session_timeout_hours),
            }
            for s in sessions
        ]

    async def cleanup_expired_sessions(self) -> int:
        """Clean up expired sessions."""
        return await self.session_manager.cleanup_expired_sessions()

    async def get_user_summary(self, user_id: int) -> Dict[str, Any]:
        """Get comprehensive user summary."""
        session_summary = await self.session_manager.get_user_session_summary(user_id)

        return {
            "user_id": user_id,
            **session_summary,
        }

    async def shutdown(self) -> None:
        """Shutdown integration and cleanup resources."""
        logger.info("Shutting down Claude integration")

        await self.cleanup_expired_sessions()
        await self.copilot_manager.shutdown()

        logger.info("Claude integration shutdown complete")

    async def get_copilot_status(self) -> Dict[str, Any]:
        """Get Copilot runtime/introspection status."""
        return await self.copilot_manager.get_status()

    async def list_copilot_sessions(self) -> List[Dict[str, Any]]:
        """List known Copilot sessions."""
        return await self.copilot_manager.list_sessions()

    async def delete_copilot_session(self, session_id: str) -> Dict[str, Any]:
        """Delete a Copilot session."""
        return await self.copilot_manager.delete_session(session_id)

    def switch_copilot_session(
        self, *, user_id: int, working_directory: Path, session_id: str
    ) -> Dict[str, Any]:
        """Switch active Copilot session binding for current user/project."""
        return self.copilot_manager.switch_session(
            user_id=user_id,
            working_directory=working_directory,
            session_id=session_id,
        )

    def get_copilot_runtime_controls(self) -> Dict[str, Any]:
        """Get active Copilot runtime controls."""
        return self.copilot_manager.get_runtime_controls()

    def update_copilot_runtime_controls(
        self,
        *,
        reasoning_effort: Optional[str] = None,
        skill_directories: Optional[List[str]] = None,
        disabled_skills: Optional[List[str]] = None,
        mcp_env_value_mode: Optional[str] = None,
        external_cli_server: Optional[str] = None,
        external_cli_server_set: bool = False,
    ) -> Dict[str, Any]:
        """Update Copilot runtime controls."""
        return self.copilot_manager.update_runtime_controls(
            reasoning_effort=reasoning_effort,
            skill_directories=skill_directories,
            disabled_skills=disabled_skills,
            mcp_env_value_mode=mcp_env_value_mode,
            external_cli_server=external_cli_server,
            external_cli_server_set=external_cli_server_set,
        )

    async def get_copilot_reasoning_levels(self) -> List[str]:
        """Get currently supported Copilot reasoning levels."""
        return await self.copilot_manager.get_reasoning_levels()

    async def get_copilot_capabilities(self) -> Dict[str, Any]:
        """Get Copilot SDK capability probe."""
        return await self.copilot_manager.get_capabilities()

    async def get_copilot_doctor_report(self) -> Dict[str, Any]:
        """Get Copilot doctor report."""
        return await self.copilot_manager.get_doctor_report()

    def _get_admin_instructions(self, blocked_tools: List[str]) -> str:
        """Generate admin instructions for enabling blocked tools."""
        instructions = []

        # Check if settings file exists
        settings_file = Path(".env")

        if blocked_tools:
            # Get current allowed tools and create merged list without duplicates
            current_tools = [
                "Read",
                "Write",
                "Edit",
                "Bash",
                "Glob",
                "Grep",
                "LS",
                "Task",
                "TaskOutput",
                "MultiEdit",
                "NotebookRead",
                "NotebookEdit",
                "WebFetch",
                "TodoRead",
                "TodoWrite",
                "WebSearch",
            ]
            merged_tools = list(
                dict.fromkeys(current_tools + blocked_tools)
            )  # Remove duplicates while preserving order
            merged_tools_str = ",".join(merged_tools)
            merged_tools_py = ", ".join(f'"{tool}"' for tool in merged_tools)

            instructions.append("**For Administrators:**")
            instructions.append("")

            if settings_file.exists():
                instructions.append(
                    "To enable these tools, add them to your `.env` file:"
                )
                instructions.append("```")
                instructions.append(f'CLAUDE_ALLOWED_TOOLS="{merged_tools_str}"')
                instructions.append("```")
            else:
                instructions.append("To enable these tools:")
                instructions.append("1. Create a `.env` file in your project root")
                instructions.append("2. Add the following line:")
                instructions.append("```")
                instructions.append(f'CLAUDE_ALLOWED_TOOLS="{merged_tools_str}"')
                instructions.append("```")

            instructions.append("")
            instructions.append("Or modify the default in `src/config/settings.py`:")
            instructions.append("```python")
            instructions.append("claude_allowed_tools: Optional[List[str]] = Field(")
            instructions.append(f"    default=[{merged_tools_py}],")
            instructions.append('    description="List of allowed Claude tools",')
            instructions.append(")")
            instructions.append("```")

        return "\n".join(instructions)

    def _create_tool_error_message(
        self,
        blocked_tools: List[str],
        allowed_tools: List[str],
        admin_instructions: str,
    ) -> str:
        """Create a comprehensive error message for tool validation failures."""
        tool_list = ", ".join(f"`{tool}`" for tool in blocked_tools)
        allowed_list = (
            ", ".join(f"`{tool}`" for tool in allowed_tools)
            if allowed_tools
            else "None"
        )

        message = [
            "🚫 **Tool Access Blocked**",
            "",
            "Claude tried to use tools that are not currently allowed:",
            f"{tool_list}",
            "",
            "**Why this happened:**",
            "• Claude needs these tools to complete your request",
            "• These tools are not in the allowed tools list",
            "• This is a security feature to control what Claude can do",
            "",
            "**What you can do:**",
            "• Contact the administrator to request access to these tools",
            "• Try rephrasing your request to use different approaches",
            "• Use simpler requests that don't require these tools",
            "",
            "**Currently allowed tools:**",
            f"{allowed_list}",
            "",
            admin_instructions,
        ]

        return "\n".join(message)
