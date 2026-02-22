"""Configuration management using Pydantic Settings.

Features:
- Environment variable loading
- Type validation
- Default values
- Computed properties
- Environment-specific settings
"""

import json
from pathlib import Path
from typing import Any, List, Literal, Optional

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.utils.constants import (
    DEFAULT_CLAUDE_MAX_COST_PER_USER,
    DEFAULT_CLAUDE_MAX_TURNS,
    DEFAULT_CLAUDE_TIMEOUT_SECONDS,
    DEFAULT_DATABASE_URL,
    DEFAULT_MAX_SESSIONS_PER_USER,
    DEFAULT_PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS,
    DEFAULT_RATE_LIMIT_BURST,
    DEFAULT_RATE_LIMIT_REQUESTS,
    DEFAULT_RATE_LIMIT_WINDOW,
    DEFAULT_SESSION_TIMEOUT_HOURS,
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Bot settings
    telegram_bot_token: SecretStr = Field(
        ..., description="Telegram bot token from BotFather"
    )
    telegram_bot_username: str = Field(..., description="Bot username without @")

    # Security
    approved_directory: Path = Field(..., description="Base directory for projects")
    allowed_users: Optional[List[int]] = Field(
        None, description="Allowed Telegram user IDs"
    )
    enable_token_auth: bool = Field(
        False, description="Enable token-based authentication"
    )
    auth_token_secret: Optional[SecretStr] = Field(
        None, description="Secret for auth tokens"
    )

    # Security relaxation (for trusted environments)
    disable_security_patterns: bool = Field(
        False,
        description=(
            "Disable dangerous pattern validation (pipes, redirections, etc.)"
        ),
    )
    disable_tool_validation: bool = Field(
        False,
        description="Allow all Claude tools by bypassing tool validation checks",
    )

    # Claude settings
    claude_binary_path: Optional[str] = Field(
        None, description="Path to Claude CLI binary (deprecated)"
    )
    claude_cli_path: Optional[str] = Field(
        None, description="Path to Claude CLI executable"
    )
    anthropic_api_key: Optional[SecretStr] = Field(
        None,
        description="Anthropic API key for SDK (optional if CLI logged in)",
    )
    claude_model: str = Field(
        "claude-3-5-sonnet-20241022", description="Claude model to use"
    )

    # Copilot settings
    copilot_binary_path: Optional[str] = Field(
        None, description="Path to Copilot CLI binary"
    )
    copilot_model: str = Field("gpt-5.3-codex", description="Copilot model to use")
    copilot_fallback_mode: Literal["sdk_only", "sdk_then_cli"] = Field(
        "sdk_then_cli",
        description="Copilot fallback policy: SDK only or SDK with CLI fallback",
    )
    copilot_external_cli_server: Optional[str] = Field(
        None,
        description="External Copilot CLI server endpoint (optional)",
    )
    copilot_config_dir_policy: Literal["global", "per_project"] = Field(
        "global",
        description="Copilot config dir policy for auth/cache isolation",
    )
    copilot_reasoning_default: Literal["low", "medium", "high", "xhigh"] = Field(
        "medium",
        description="Default Copilot reasoning effort",
    )
    copilot_enable_prerelease_features: bool = Field(
        False,
        description=(
            "Enable prerelease Copilot SDK features (preview/alpha/beta/rc). "
            "Keep disabled in production until API behavior stabilizes."
        ),
    )
    copilot_skill_directories: Optional[List[str]] = Field(
        default=[],
        description="Additional Copilot skill directories",
    )
    copilot_disabled_skills: Optional[List[str]] = Field(
        default=[],
        description="Disabled Copilot skills",
    )
    copilot_session_store_path: Path = Field(
        Path("data/copilot-session-map.json"),
        description="Persistent store path for Copilot session map",
    )
    copilot_infinite_sessions: bool = Field(
        True,
        description="Enable Copilot infinite sessions (auto context compaction)",
    )
    copilot_compaction_threshold: float = Field(
        0.80,
        description="Background compaction threshold (0.0-1.0) for infinite sessions",
        ge=0.0,
        le=1.0,
    )
    default_provider: Literal["claude", "copilot"] = Field(
        "claude", description="Default AI provider: claude or copilot"
    )
    claude_max_turns: int = Field(
        DEFAULT_CLAUDE_MAX_TURNS, description="Max conversation turns"
    )
    claude_timeout_seconds: int = Field(
        DEFAULT_CLAUDE_TIMEOUT_SECONDS, description="Claude timeout"
    )
    claude_max_cost_per_user: float = Field(
        DEFAULT_CLAUDE_MAX_COST_PER_USER, description="Max cost per user"
    )
    # NOTE: When changing this list, also update docs/tools.md,
    # docs/configuration.md, .env.example,
    # src/claude/facade.py (_get_admin_instructions),
    # and src/bot/orchestrator.py (_TOOL_ICONS).
    claude_allowed_tools: Optional[List[str]] = Field(
        default=[
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
            "Skill",
        ],
        description="List of allowed Claude tools",
    )
    claude_disallowed_tools: Optional[List[str]] = Field(
        default=[],
        description="List of explicitly disallowed Claude tools/commands",
    )

    # Sandbox settings
    sandbox_enabled: bool = Field(
        True,
        description="Enable OS-level bash sandboxing for approved dir",
    )
    sandbox_excluded_commands: Optional[List[str]] = Field(
        default=["git", "npm", "pip", "poetry", "make", "docker"],
        description="Commands that run outside the sandbox (need system access)",
    )

    # Rate limiting
    rate_limit_requests: int = Field(
        DEFAULT_RATE_LIMIT_REQUESTS, description="Requests per window"
    )
    rate_limit_window: int = Field(
        DEFAULT_RATE_LIMIT_WINDOW, description="Rate limit window seconds"
    )
    rate_limit_burst: int = Field(
        DEFAULT_RATE_LIMIT_BURST, description="Burst capacity"
    )

    # Storage
    database_url: str = Field(
        DEFAULT_DATABASE_URL, description="Database connection URL"
    )
    session_timeout_hours: int = Field(
        DEFAULT_SESSION_TIMEOUT_HOURS, description="Session timeout"
    )
    session_timeout_minutes: int = Field(
        default=120,
        description="Session timeout in minutes",
        ge=10,
        le=1440,  # Max 24 hours
    )
    max_sessions_per_user: int = Field(
        DEFAULT_MAX_SESSIONS_PER_USER, description="Max concurrent sessions"
    )

    # Features
    enable_mcp: bool = Field(False, description="Enable Model Context Protocol")
    mcp_config_path: Optional[Path] = Field(
        None, description="MCP configuration file path"
    )
    mcp_env_value_mode: Literal["raw", "masked", "omit"] = Field(
        "raw",
        description="MCP env value mode for Copilot SDK server config",
    )
    enable_git_integration: bool = Field(True, description="Enable git commands")
    enable_file_uploads: bool = Field(True, description="Enable file upload handling")
    enable_quick_actions: bool = Field(True, description="Enable quick action buttons")
    agentic_mode: bool = Field(
        True,
        description="Conversational agentic mode (default) vs classic command mode",
    )
    memory_system_plus: bool = Field(
        False,
        description="Enable memory system plus hooks pipeline",
    )
    memory_hooks_enabled: bool = Field(
        True,
        description="Enable memory pre/post hooks when memory_system_plus is enabled",
    )
    memory_pre_hook_enabled: bool = Field(
        True,
        description="Enable pre-send memory assembly hook",
    )
    memory_post_hook_enabled: bool = Field(
        True,
        description="Enable post-response memory condensation hook",
    )
    memory_ai_enhancement_enabled: bool = Field(
        True,
        description="Enable AI enhancement layer for memory operations",
    )
    memory_ai_extractor_enabled: bool = Field(
        True,
        description="Enable AI extractor submodule for post-session condensation",
    )
    memory_ai_reranker_enabled: bool = Field(
        True,
        description="Enable AI reranker submodule for pre-session recall ordering",
    )
    memory_ai_conflict_detector_enabled: bool = Field(
        True,
        description="Enable AI conflict detector submodule for memory contradictions",
    )
    memory_ai_periodic_review_enabled: bool = Field(
        True,
        description="Enable AI periodic review submodule for memory quality checks",
    )
    memory_ai_model: str = Field(
        "gpt-5-mini",
        description="Default AI model used by memory enhancement modules",
    )
    memory_ai_timeout_seconds: int = Field(
        20,
        description="Timeout for memory AI enhancement requests in seconds",
        ge=1,
        le=120,
    )
    memory_profile_default: Literal["fast", "balanced", "quality"] = Field(
        "balanced",
        description="Default memory AI profile",
    )
    memory_recall_limit: int = Field(
        20,
        description="Maximum number of memory candidates recalled before assembly",
        ge=1,
        le=200,
    )
    memory_injection_token_budget: int = Field(
        800,
        description="Approximate token budget for injected memory context",
        ge=100,
        le=8000,
    )
    memory_retention_days: int = Field(
        30,
        description="Default retention period in days for memory entries",
        ge=1,
        le=3650,
    )

    # Output verbosity (0=quiet, 1=normal, 2=detailed)
    verbose_level: int = Field(
        1,
        description=(
            "Bot output verbosity: 0=quiet (final response only), "
            "1=normal (tool names + reasoning), "
            "2=detailed (tool inputs + longer reasoning)"
        ),
        ge=0,
        le=2,
    )

    # Monitoring
    log_level: str = Field("INFO", description="Logging level")
    enable_telemetry: bool = Field(False, description="Enable anonymous telemetry")
    sentry_dsn: Optional[str] = Field(None, description="Sentry DSN for error tracking")

    # Development
    debug: bool = Field(False, description="Enable debug mode")
    development_mode: bool = Field(False, description="Enable development features")

    # Webhook settings (optional)
    webhook_url: Optional[str] = Field(None, description="Webhook URL for bot")
    webhook_port: int = Field(8443, description="Webhook port")
    webhook_path: str = Field("/webhook", description="Webhook path")
    telegram_webhook_secret_token: Optional[str] = Field(
        None,
        description=(
            "Optional secret token used by Telegram webhook "
            "(X-Telegram-Bot-Api-Secret-Token)"
        ),
    )

    # Agentic platform settings
    enable_api_server: bool = Field(False, description="Enable FastAPI webhook server")
    api_server_port: int = Field(8080, description="Webhook API server port")
    enable_scheduler: bool = Field(False, description="Enable job scheduler")
    github_webhook_secret: Optional[str] = Field(
        None, description="GitHub webhook HMAC secret"
    )
    webhook_api_secret: Optional[str] = Field(
        None, description="Shared secret for generic webhook providers"
    )
    notification_chat_ids: Optional[List[int]] = Field(
        None, description="Default Telegram chat IDs for proactive notifications"
    )
    enable_project_threads: bool = Field(
        False,
        description="Enable strict routing by Telegram forum project threads",
    )
    project_threads_mode: Literal["private", "group"] = Field(
        "private",
        description="Project thread mode: private chat topics or group forum topics",
    )
    project_threads_chat_id: Optional[int] = Field(
        None, description="Telegram forum chat ID where project topics are managed"
    )
    projects_config_path: Optional[Path] = Field(
        None, description="Path to YAML project registry for thread mode"
    )
    project_threads_sync_action_interval_seconds: float = Field(
        DEFAULT_PROJECT_THREADS_SYNC_ACTION_INTERVAL_SECONDS,
        description=(
            "Minimum delay between Telegram API calls during project topic sync"
        ),
        ge=0.0,
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    @field_validator("allowed_users", "notification_chat_ids", mode="before")
    @classmethod
    def parse_int_list(cls, v: Any) -> Optional[List[int]]:
        """Parse comma-separated integer lists."""
        if v is None:
            return None
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            return [int(uid.strip()) for uid in v.split(",") if uid.strip()]
        if isinstance(v, list):
            return [int(uid) for uid in v]
        return v  # type: ignore[no-any-return]

    @field_validator(
        "claude_allowed_tools",
        "claude_disallowed_tools",
        "sandbox_excluded_commands",
        "copilot_skill_directories",
        "copilot_disabled_skills",
        mode="before",
    )
    @classmethod
    def parse_string_list_values(cls, v: Any) -> Optional[List[str]]:
        """Parse list-like configuration from JSON array or comma-separated text."""
        if v is None:
            return None

        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]

        if isinstance(v, str):
            value = v.strip()
            if not value:
                return []
            if value.lower() in {"none", "null"}:
                return None
            if value.startswith("[") and value.endswith("]"):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [
                            str(item).strip() for item in parsed if str(item).strip()
                        ]
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in value.split(",") if item.strip()]

        return v  # type: ignore[no-any-return]

    @field_validator("copilot_external_cli_server", mode="before")
    @classmethod
    def normalize_optional_string(cls, v: Any) -> Optional[str]:
        """Normalize optional string values."""
        if v is None:
            return None
        if isinstance(v, str):
            value = v.strip()
            return value or None
        return str(v)

    @field_validator("approved_directory")
    @classmethod
    def validate_approved_directory(cls, v: Any) -> Path:
        """Ensure approved directory exists and is absolute."""
        if isinstance(v, str):
            v = Path(v)

        path = v.resolve()
        if not path.exists():
            raise ValueError(f"Approved directory does not exist: {path}")
        if not path.is_dir():
            raise ValueError(f"Approved directory is not a directory: {path}")
        return path  # type: ignore[no-any-return]

    @field_validator("mcp_config_path", mode="before")
    @classmethod
    def validate_mcp_config(cls, v: Any, info: Any) -> Optional[Path]:
        """Validate MCP configuration path if MCP is enabled."""
        if not v:
            return v  # type: ignore[no-any-return]
        if isinstance(v, str):
            v = Path(v)
        if not v.exists():
            raise ValueError(f"MCP config file does not exist: {v}")
        # Validate that the file contains valid JSON with mcpServers
        try:
            with open(v) as f:
                config_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"MCP config file is not valid JSON: {e}")
        if not isinstance(config_data, dict):
            raise ValueError("MCP config file must contain a JSON object")
        if "mcpServers" not in config_data:
            raise ValueError(
                "MCP config file must contain a 'mcpServers' key. "
                'Format: {"mcpServers": {"name": {"command": ...}}}'
            )
        if not isinstance(config_data["mcpServers"], dict):
            raise ValueError(
                "'mcpServers' must be an object mapping server names to configurations"
            )
        if not config_data["mcpServers"]:
            raise ValueError(
                "'mcpServers' must contain at least one server configuration"
            )
        return v  # type: ignore[no-any-return]

    @field_validator("copilot_session_store_path", mode="before")
    @classmethod
    def validate_copilot_session_store_path(cls, v: Any) -> Path:
        """Normalize Copilot session store path."""
        if isinstance(v, str):
            value = v.strip()
            if not value:
                raise ValueError("copilot_session_store_path cannot be empty")
            v = Path(value)
        if isinstance(v, Path):
            return v
        raise ValueError("copilot_session_store_path must be a valid filesystem path")

    @field_validator("projects_config_path", mode="before")
    @classmethod
    def validate_projects_config_path(cls, v: Any) -> Optional[Path]:
        """Validate projects config path if provided."""
        if not v:
            return None
        if isinstance(v, str):
            value = v.strip()
            if not value:
                return None
            v = Path(value)
        if not v.exists():
            raise ValueError(f"Projects config file does not exist: {v}")
        if not v.is_file():
            raise ValueError(f"Projects config path is not a file: {v}")
        return v  # type: ignore[no-any-return]

    @field_validator("project_threads_mode", mode="before")
    @classmethod
    def validate_project_threads_mode(cls, v: Any) -> str:
        """Validate project thread mode."""
        if v is None:
            return "private"
        mode = str(v).strip().lower()
        if mode not in {"private", "group"}:
            raise ValueError("project_threads_mode must be one of ['private', 'group']")
        return mode

    @field_validator("project_threads_chat_id", mode="before")
    @classmethod
    def validate_project_threads_chat_id(cls, v: Any) -> Optional[int]:
        """Allow empty chat ID for private mode by treating blank values as None."""
        if v is None:
            return None
        if isinstance(v, str):
            value = v.strip()
            if not value:
                return None
            return int(value)
        if isinstance(v, int):
            return v
        return v  # type: ignore[no-any-return]

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: Any) -> str:
        """Validate log level."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()  # type: ignore[no-any-return]

    @model_validator(mode="after")
    def validate_cross_field_dependencies(self) -> "Settings":
        """Validate dependencies between fields."""
        # Check auth token requirements
        if self.enable_token_auth and not self.auth_token_secret:
            raise ValueError(
                "auth_token_secret required when enable_token_auth is True"
            )

        # Check MCP requirements
        if self.enable_mcp and not self.mcp_config_path:
            raise ValueError("mcp_config_path required when enable_mcp is True")

        if self.enable_project_threads:
            if (
                self.project_threads_mode == "group"
                and self.project_threads_chat_id is None
            ):
                raise ValueError(
                    "project_threads_chat_id required when "
                    "project_threads_mode is 'group'"
                )
            if not self.projects_config_path:
                raise ValueError(
                    "projects_config_path required when enable_project_threads is True"
                )

        return self

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not (self.debug or self.development_mode)

    @property
    def database_path(self) -> Optional[Path]:
        """Extract path from SQLite database URL."""
        if self.database_url.startswith("sqlite:///"):
            db_path = self.database_url.replace("sqlite:///", "")
            return Path(db_path).resolve()
        return None

    @property
    def telegram_token_str(self) -> str:
        """Get Telegram token as string."""
        return self.telegram_bot_token.get_secret_value()

    @property
    def auth_secret_str(self) -> Optional[str]:
        """Get auth token secret as string."""
        if self.auth_token_secret:
            return self.auth_token_secret.get_secret_value()
        return None

    @property
    def anthropic_api_key_str(self) -> Optional[str]:
        """Get Anthropic API key as string."""
        return (
            self.anthropic_api_key.get_secret_value()
            if self.anthropic_api_key
            else None
        )
