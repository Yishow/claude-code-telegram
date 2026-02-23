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


class CopilotSDKSessionsMixin:
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
