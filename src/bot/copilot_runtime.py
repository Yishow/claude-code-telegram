"""Shared runtime control helpers for bot handlers."""

from typing import Any, Dict, List

from ..config.settings import Settings

SESSION_PROVIDER_KEY = "provider"
SESSION_MODEL_KEY = "copilot_model"
SESSION_CLAUDE_MODEL_KEY = "claude_model"
SESSION_REASONING_KEY = "copilot_reasoning_effort"
SESSION_SKILL_DIRS_KEY = "copilot_skill_directories"
SESSION_DISABLED_SKILLS_KEY = "copilot_disabled_skills"
SESSION_MCP_ENV_MODE_KEY = "copilot_mcp_env_value_mode"
SESSION_EXTERNAL_SERVER_KEY = "copilot_external_cli_server"

ONCE_PROVIDER_KEY = "one_shot_provider"
ONCE_MODEL_KEY = "one_shot_copilot_model"
ONCE_CLAUDE_MODEL_KEY = "one_shot_claude_model"
ONCE_REASONING_KEY = "one_shot_reasoning_effort"


def _as_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return [str(value)]


def get_session_provider(settings: Settings, user_data: Dict[str, Any]) -> str:
    return str(user_data.get(SESSION_PROVIDER_KEY) or settings.default_provider)


def get_session_model(settings: Settings, user_data: Dict[str, Any]) -> str:
    return str(user_data.get(SESSION_MODEL_KEY) or settings.copilot_model)


def get_session_claude_model(settings: Settings, user_data: Dict[str, Any]) -> str:
    return str(user_data.get(SESSION_CLAUDE_MODEL_KEY) or settings.claude_model)


def get_runtime_snapshot(settings: Settings, user_data: Dict[str, Any]) -> Dict[str, Any]:
    provider = get_session_provider(settings, user_data)
    selected_model = (
        get_session_model(settings, user_data)
        if provider == "copilot"
        else get_session_claude_model(settings, user_data)
    )
    return {
        "provider": provider,
        "model": selected_model,
        "fallback_mode": settings.copilot_fallback_mode,
        "reasoning_effort": user_data.get(
            SESSION_REASONING_KEY, settings.copilot_reasoning_default
        ),
        "skill_directories": _as_string_list(
            user_data.get(SESSION_SKILL_DIRS_KEY, settings.copilot_skill_directories)
        ),
        "disabled_skills": _as_string_list(
            user_data.get(
                SESSION_DISABLED_SKILLS_KEY,
                settings.copilot_disabled_skills,
            )
        ),
        "mcp_env_value_mode": user_data.get(
            SESSION_MCP_ENV_MODE_KEY, settings.mcp_env_value_mode
        ),
        "external_cli_server": user_data.get(
            SESSION_EXTERNAL_SERVER_KEY, settings.copilot_external_cli_server
        ),
    }


def consume_request_controls(
    settings: Settings, user_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Return effective per-request controls and consume one-shot overrides."""
    provider = str(
        user_data.get(ONCE_PROVIDER_KEY) or get_session_provider(settings, user_data)
    )
    copilot_model = str(
        user_data.get(ONCE_MODEL_KEY) or get_session_model(settings, user_data)
    )
    claude_model = str(
        user_data.get(ONCE_CLAUDE_MODEL_KEY)
        or get_session_claude_model(settings, user_data)
    )

    reasoning_effort = user_data.get(ONCE_REASONING_KEY)
    if reasoning_effort is None:
        reasoning_effort = user_data.get(
            SESSION_REASONING_KEY, settings.copilot_reasoning_default
        )

    result = {
        "provider": provider,
        "copilot_model": copilot_model,
        "claude_model": claude_model,
        "reasoning_effort": reasoning_effort,
        "skill_directories": _as_string_list(
            user_data.get(SESSION_SKILL_DIRS_KEY, settings.copilot_skill_directories)
        ),
        "disabled_skills": _as_string_list(
            user_data.get(SESSION_DISABLED_SKILLS_KEY, settings.copilot_disabled_skills)
        ),
        "mcp_env_value_mode": user_data.get(
            SESSION_MCP_ENV_MODE_KEY, settings.mcp_env_value_mode
        ),
        "external_cli_server": user_data.get(
            SESSION_EXTERNAL_SERVER_KEY, settings.copilot_external_cli_server
        ),
    }

    for key in (
        ONCE_PROVIDER_KEY,
        ONCE_MODEL_KEY,
        ONCE_CLAUDE_MODEL_KEY,
        ONCE_REASONING_KEY,
    ):
        user_data.pop(key, None)

    return result
