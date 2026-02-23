"""Split implementation facade for MessageOrchestrator."""

from .orchestrator_impl_base import MessageOrchestratorBase, _redact_secrets
from .orchestrator_impl_callbacks import MessageOrchestratorCallbacksMixin
from .orchestrator_impl_commands import MessageOrchestratorCommandsMixin
from .orchestrator_impl_media import MessageOrchestratorMediaMixin
from .orchestrator_impl_registration import MessageOrchestratorRegistrationMixin
from .orchestrator_impl_stream import MessageOrchestratorStreamMixin
from .orchestrator_impl_text import MessageOrchestratorTextMixin


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


__all__ = ["MessageOrchestrator", "_redact_secrets"]
