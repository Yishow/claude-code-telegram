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


class CopilotSDKUtilsMixin:
    @staticmethod
    def _payload_bucket(size: int) -> str:
        if size < 4096:
            return "small"
        if size < 32768:
            return "medium"
        if size < 262144:
            return "large"
        return "xlarge"

    @staticmethod
    def _safe_unsubscribe(unsubscribe: Any) -> None:
        if not unsubscribe:
            return
        try:
            if callable(unsubscribe):
                unsubscribe()
        except Exception as e:
            logger.warning("Failed to unsubscribe Copilot stream", error=str(e))

    def _local_session_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for key, sid in sorted(self._session_map.items()):
            if ":" not in key:
                continue
            user_str, project = key.split(":", 1)
            rows.append(
                {
                    "session_id": sid,
                    "user_id": self._safe_int(user_str),
                    "project_path": project,
                    "source": "local_map",
                }
            )
        return rows

    def _normalize_sdk_sessions_payload(self, payload: Any) -> List[Dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, dict):
            for key in ("sessions", "items", "data"):
                nested = payload.get(key)
                if isinstance(nested, list):
                    payload = nested
                    break

        if not isinstance(payload, list):
            return []

        rows: List[Dict[str, Any]] = []
        for item in payload:
            row = self._session_row_from_obj(item)
            if row:
                rows.append(row)
        return rows

    def _session_row_from_obj(self, item: Any) -> Optional[Dict[str, Any]]:
        if isinstance(item, dict):
            getter = item.get
        else:

            def getter(k: str, default: Any = None) -> Any:
                return getattr(item, k, default)

        session_id = (
            getter("session_id")
            or getter("sessionId")
            or getter("id")
            or getter("session")
        )
        if not session_id:
            return None

        project_path = (
            getter("project_path")
            or getter("projectPath")
            or getter("workspace_path")
            or getter("workspacePath")
            or getter("cwd")
            or ""
        )
        user_id = getter("user_id") or getter("userId")

        return {
            "session_id": str(session_id),
            "user_id": self._safe_int(user_id),
            "project_path": str(project_path),
            "source": "sdk",
        }

    def _detect_sdk_package(self) -> Dict[str, Any]:
        distribution = None
        version = None
        for name in ("github-copilot-sdk", "copilot"):
            try:
                version = importlib_metadata.version(name)
                distribution = name
                break
            except importlib_metadata.PackageNotFoundError:
                continue

        module_spec = importlib.util.find_spec("copilot")
        module_path = module_spec.origin if module_spec else None
        return {
            "distribution": distribution,
            "version": version,
            "module_found": bool(module_spec),
            "module_path": module_path,
        }

    @staticmethod
    def _version_at_least(version: str, minimum: tuple[int, int, int]) -> bool:
        match = _SEMVER_RE.match(version.strip())
        if not match:
            return False
        parsed = tuple(int(part) for part in match.groups())
        return parsed >= minimum

    @staticmethod
    def _is_prerelease_version(version: str) -> bool:
        lowered = version.lower()
        return any(tag in lowered for tag in ("preview", "alpha", "beta", "rc"))

    @staticmethod
    def _has_any_method(target: Any, *names: str) -> bool:
        return any(hasattr(target, name) for name in names)

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _redact_sensitive(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            redacted: Dict[str, Any] = {}
            for k, v in payload.items():
                key = str(k).lower()
                if any(
                    token in key
                    for token in ("token", "secret", "password", "key", "authorization")
                ):
                    redacted[k] = "***"
                else:
                    redacted[k] = self._redact_sensitive(v)
            return redacted
        if isinstance(payload, list):
            return [self._redact_sensitive(x) for x in payload]
        return payload
