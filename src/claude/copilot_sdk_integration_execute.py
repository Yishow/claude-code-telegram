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


class CopilotSDKExecuteMixin:
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
