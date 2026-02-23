"""Claude-specific exceptions."""


class ClaudeError(Exception):
    """Base Claude error."""


class ClaudeTimeoutError(ClaudeError):
    """Operation timed out."""


class ClaudeProcessError(ClaudeError):
    """Process execution failed."""


class CopilotAuthenticationError(ClaudeProcessError):
    """Copilot authentication failed."""


class ClaudeParsingError(ClaudeError):
    """Failed to parse output."""


class ClaudeSessionError(ClaudeError):
    """Session management error."""


class ClaudeMCPError(ClaudeError):
    """MCP server connection or configuration error."""

    def __init__(self, message: str, server_name: str = None):
        super().__init__(message)
        self.server_name = server_name


class ClaudeToolValidationError(ClaudeError):
    """Raised when a requested tool is blocked by policy/validation."""

    def __init__(
        self,
        message: str,
        blocked_tools: list[str] | None = None,
        allowed_tools: list[str] | None = None,
    ):
        super().__init__(message)
        self.blocked_tools = blocked_tools or []
        self.allowed_tools = allowed_tools or []
