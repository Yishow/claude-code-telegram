"""GitHub Copilot SDK integration.

Uses github-copilot-sdk via JSON-RPC and exposes a bot-friendly runtime
surface (interactive bridge, status/introspection, session operations,
policy-aware runtime controls, and reliability guardrails).
"""

import asyncio
import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

import structlog

from ..config.settings import Settings
from .copilot_interaction_bridge import CopilotInteractionBridge
from .exceptions import ClaudeProcessError, ClaudeTimeoutError

try:
    from .exceptions import CopilotAuthenticationError
except ImportError:  # pragma: no cover - mixed-version runtime fallback
    CopilotAuthenticationError = ClaudeProcessError
from .monitor import ToolMonitor

logger = structlog.get_logger()
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


_SPLIT_EXPORTS = (
    hashlib,
    importlib,
    importlib_metadata,
    json,
    re,
    Path,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Union,
    ClaudeProcessError,
    ClaudeTimeoutError,
    CopilotAuthenticationError,
)


AskUserRequest = Dict[str, Any]
AskUserResponse = Dict[str, Any]
SessionConfig = Any


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


class CopilotSDKManagerBase:
    def __init__(
        self,
        config: Settings,
        *,
        interaction_bridge: Optional[CopilotInteractionBridge] = None,
        tool_monitor: Optional[ToolMonitor] = None,
    ):
        self.config = config
        self.tool_monitor = tool_monitor
        self.interaction_bridge = interaction_bridge or CopilotInteractionBridge(
            ask_user_timeout_seconds=int(
                getattr(config, "copilot_ask_user_timeout_seconds", 300)
            ),
            permission_timeout_seconds=int(
                getattr(config, "copilot_permission_timeout_seconds", 120)
            )
        )

        self._client: Optional[Any] = None
        self._client_lock = asyncio.Lock()
        self._session_map: Dict[str, str] = {}

        self._runtime_controls: Dict[str, Any] = {
            "reasoning_effort": getattr(config, "copilot_reasoning_default", "medium"),
            "skill_directories": list(
                getattr(config, "copilot_skill_directories", []) or []
            ),
            "disabled_skills": list(
                getattr(config, "copilot_disabled_skills", []) or []
            ),
            "mcp_env_value_mode": getattr(config, "mcp_env_value_mode", "raw"),
            "external_cli_server": getattr(config, "copilot_external_cli_server", None),
        }

        raw_store = getattr(
            config, "copilot_session_store_path", Path("data/copilot-session-map.json")
        )
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

    @staticmethod
    def _read_field(payload: Any, *names: str, default: Any = None) -> Any:
        """Read a field from dict-like or object payloads."""
        if isinstance(payload, dict):
            for name in names:
                value = payload.get(name)
                if value is not None:
                    return value
            return default

        for name in names:
            value = getattr(payload, name, None)
            if value is not None:
                return value

        return default

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
            "reasoning_effort": self._runtime_controls.get(
                "reasoning_effort", "medium"
            ),
            "skill_directories": list(
                self._runtime_controls.get("skill_directories", []) or []
            ),
            "disabled_skills": list(
                self._runtime_controls.get("disabled_skills", []) or []
            ),
            "mcp_env_value_mode": self._runtime_controls.get(
                "mcp_env_value_mode", "raw"
            ),
            "external_cli_server": self._runtime_controls.get("external_cli_server"),
            "config_dir_policy": getattr(
                self.config, "copilot_config_dir_policy", "global"
            ),
        }

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
        """Apply runtime control updates and return the effective state."""
        if reasoning_effort is not None:
            self._runtime_controls["reasoning_effort"] = reasoning_effort
        if skill_directories is not None:
            self._runtime_controls["skill_directories"] = list(skill_directories)
        if disabled_skills is not None:
            self._runtime_controls["disabled_skills"] = list(disabled_skills)
        if mcp_env_value_mode is not None:
            self._runtime_controls["mcp_env_value_mode"] = mcp_env_value_mode
        if external_cli_server_set or external_cli_server is not None:
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

        project_hash = hashlib.sha1(
            str(working_directory.resolve()).encode("utf-8")
        ).hexdigest()[:12]
        base = Path(self.config.approved_directory) / ".copilot-config"
        target = base / project_hash
        target.mkdir(parents=True, exist_ok=True)
        return str(target)
