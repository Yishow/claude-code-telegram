"""Tests for CopilotInteractionBridge."""

import asyncio

import pytest

from src.claude.copilot_interaction_bridge import CopilotInteractionBridge


class TestCopilotInteractionBridge:
    async def test_ask_user_choice_resolution(self):
        bridge = CopilotInteractionBridge()
        meta = await bridge.create_ask_user(
            user_id=1,
            chat_id=100,
            message_thread_id=None,
            question="pick one",
            choices=["a", "b"],
            allow_freeform=True,
        )
        waiter = asyncio.create_task(bridge.wait_for_result(meta["interaction_id"]))

        resolved = await bridge.resolve(
            interaction_id=meta["interaction_id"],
            value="b",
            user_id=1,
            chat_id=100,
            message_thread_id=None,
        )
        assert resolved is True

        assert await waiter == "b"

    async def test_freeform_resolution_by_scope(self):
        bridge = CopilotInteractionBridge()
        meta = await bridge.create_ask_user(
            user_id=7,
            chat_id=900,
            message_thread_id=11,
            question="details?",
            choices=[],
            allow_freeform=True,
        )

        resolved_id = await bridge.resolve_pending_freeform(
            user_id=7,
            chat_id=900,
            message_thread_id=11,
            value="my answer",
        )
        assert resolved_id == meta["interaction_id"]

    async def test_scope_isolation(self):
        bridge = CopilotInteractionBridge()
        meta = await bridge.create_permission_request(
            user_id=1,
            chat_id=200,
            message_thread_id=None,
            kind="shell",
            tool_call_id="t1",
        )

        resolved = await bridge.resolve(
            interaction_id=meta["interaction_id"],
            value=True,
            user_id=2,  # wrong user
            chat_id=200,
            message_thread_id=None,
        )
        assert resolved is False

    async def test_timeout_auto_resolution(self):
        bridge = CopilotInteractionBridge(
            ask_user_timeout_seconds=1,
            permission_timeout_seconds=1,
        )
        meta = await bridge.create_permission_request(
            user_id=3,
            chat_id=333,
            message_thread_id=None,
            kind="write",
            tool_call_id="tc",
            timeout_seconds=1,
        )
        await asyncio.sleep(1.1)
        # Expired interaction should no longer be pending.
        assert await bridge.get(meta["interaction_id"]) is None
