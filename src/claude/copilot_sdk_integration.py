"""Split implementation facade for Copilot SDK manager."""

import asyncio

from .copilot_sdk_integration_base import CopilotSDKManagerBase, CopilotStreamUpdate
from .copilot_sdk_integration_diagnostics import CopilotSDKDiagnosticsMixin
from .copilot_sdk_integration_execute import CopilotSDKExecuteMixin
from .copilot_sdk_integration_hooks import CopilotSDKHooksMixin
from .copilot_sdk_integration_sessions import CopilotSDKSessionsMixin
from .copilot_sdk_integration_utils import CopilotSDKUtilsMixin


class CopilotSDKManager(
    CopilotSDKUtilsMixin,
    CopilotSDKDiagnosticsMixin,
    CopilotSDKSessionsMixin,
    CopilotSDKExecuteMixin,
    CopilotSDKHooksMixin,
    CopilotSDKManagerBase,
):
    pass


__all__ = ["CopilotSDKManager", "CopilotStreamUpdate", "asyncio"]
