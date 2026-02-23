"""Thin compatibility wrapper for MessageOrchestrator."""

from .orchestrator_impl import (
    MessageOrchestrator,
    _redact_secrets,
)
from .copilot_control_plane import run_copilot_control_command

__all__ = ["MessageOrchestrator", "_redact_secrets", "run_copilot_control_command"]
