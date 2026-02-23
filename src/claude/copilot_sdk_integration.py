"""Thin compatibility wrapper for copilot SDK integration."""

import asyncio

from .copilot_sdk_integration_impl import CopilotSDKManager, CopilotStreamUpdate

__all__ = ["CopilotSDKManager", "CopilotStreamUpdate", "asyncio"]
