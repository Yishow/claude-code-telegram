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


class CopilotSDKDiagnosticsMixin:
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
