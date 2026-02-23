"""Split implementation facade for MessageOrchestrator."""

from .copilot_control_plane import run_copilot_control_command
from .orchestrator_base import MessageOrchestratorBase, _redact_secrets
from .orchestrator_callbacks import MessageOrchestratorCallbacksMixin
from .orchestrator_commands import MessageOrchestratorCommandsMixin
from .orchestrator_media import MessageOrchestratorMediaMixin
from .orchestrator_registration import MessageOrchestratorRegistrationMixin
from .orchestrator_stream import MessageOrchestratorStreamMixin
from .orchestrator_text import MessageOrchestratorTextMixin


class MessageOrchestrator(
    MessageOrchestratorCallbacksMixin,
    MessageOrchestratorMediaMixin,
    MessageOrchestratorTextMixin,
    MessageOrchestratorStreamMixin,
    MessageOrchestratorCommandsMixin,
    MessageOrchestratorRegistrationMixin,
    MessageOrchestratorBase,
):
    """Routes messages based on mode. Single entry point for all Telegram updates."""


__all__ = ["MessageOrchestrator", "_redact_secrets", "run_copilot_control_command"]
