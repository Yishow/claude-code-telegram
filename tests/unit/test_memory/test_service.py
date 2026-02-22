"""Tests for memory service pipeline."""

import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.config import create_test_config
from src.memory import MemoryService
from src.storage.facade import Storage
from src.storage.models import MemoryItemModel


@pytest.fixture
async def memory_env():
    """Create memory service with isolated sqlite storage."""
    with tempfile.TemporaryDirectory() as temp_dir:
        base = Path(temp_dir)
        settings = create_test_config(
            approved_directory=str(base),
            memory_system_plus=True,
            memory_ai_timeout_seconds=1,
            memory_recall_limit=10,
            memory_injection_token_budget=500,
        )
        storage = Storage(f"sqlite:///{base / 'test.db'}")
        await storage.initialize()
        service = MemoryService(settings, storage)
        try:
            yield service, storage
        finally:
            await storage.close()


async def test_post_extract_and_pre_recall(memory_env):
    """Deterministic extract/recall should persist and inject scoped memory."""
    service, storage = memory_env
    scope = {
        "user_id": 11,
        "chat_id": -22,
        "message_thread_id": 0,
        "project_path": Path("/repo/a"),
    }
    runtime = await service.get_runtime_settings(
        user_id=scope["user_id"],
        chat_id=scope["chat_id"],
        message_thread_id=scope["message_thread_id"],
    )
    await service.apply_post_hook(
        prompt="Please remember we decided to use postgres for storage.",
        response="Decision confirmed: we will use postgres and add a TODO for migrations.",
        source_session_id="session-a",
        source_message_id=1001,
        runtime_settings=runtime,
        success=True,
        elapsed_ms=230,
        **scope,
    )

    pre = await service.apply_pre_hook(
        prompt="What is our database plan?",
        controls={
            "provider": "copilot",
            "copilot_model": "gpt-5-mini",
            "reasoning_effort": "medium",
            "skill_directories": [],
            "disabled_skills": [],
            "mcp_env_value_mode": "raw",
            "external_cli_server": None,
        },
        **scope,
    )

    assert "[Memory Context]" in pre.prompt
    assert "postgres" in pre.prompt

    candidates = await storage.memory.get_recall_candidates(
        user_id=11,
        chat_id=-22,
        message_thread_id=0,
        project_path="/repo/a",
        limit=20,
        now=datetime.now(UTC),
    )
    assert len(candidates) > 0


async def test_scope_and_directory_isolation(memory_env):
    """Memory must not leak across thread or project scopes."""
    service, _ = memory_env
    shared_controls = {
        "provider": "copilot",
        "copilot_model": "gpt-5-mini",
        "reasoning_effort": "medium",
        "skill_directories": [],
        "disabled_skills": [],
        "mcp_env_value_mode": "raw",
        "external_cli_server": None,
    }
    base_scope = {
        "user_id": 77,
        "chat_id": -88,
        "message_thread_id": 1,
        "project_path": Path("/project/a"),
    }
    runtime = await service.get_runtime_settings(
        user_id=base_scope["user_id"],
        chat_id=base_scope["chat_id"],
        message_thread_id=base_scope["message_thread_id"],
    )
    await service.apply_post_hook(
        prompt="Remember preference: use pytest for tests.",
        response="Noted. We'll keep pytest as the default.",
        source_session_id="scope-session",
        source_message_id=1,
        runtime_settings=runtime,
        success=True,
        elapsed_ms=100,
        **base_scope,
    )

    same_scope = await service.apply_pre_hook(
        prompt="Which testing framework do we use?",
        controls=shared_controls,
        **base_scope,
    )
    assert "[Memory Context]" in same_scope.prompt

    other_thread = await service.apply_pre_hook(
        prompt="Which testing framework do we use?",
        controls=shared_controls,
        user_id=77,
        chat_id=-88,
        message_thread_id=2,
        project_path=Path("/project/a"),
    )
    assert other_thread.prompt == "Which testing framework do we use?"

    other_project = await service.apply_pre_hook(
        prompt="Which testing framework do we use?",
        controls=shared_controls,
        user_id=77,
        chat_id=-88,
        message_thread_id=1,
        project_path=Path("/project/b"),
    )
    assert other_project.prompt == "Which testing framework do we use?"


async def test_ttl_eviction_and_conflict_marking(memory_env):
    """Expired memories are evicted and contradiction links are tracked."""
    service, storage = memory_env
    now = datetime.now(UTC)
    await storage.memory.save_memory_item(
        MemoryItemModel(
            user_id=1,
            chat_id=2,
            message_thread_id=0,
            project_path="/ttl",
            memory_type="fact",
            content="old memory",
            priority=50,
            timestamp=now - timedelta(days=30),
            ttl_expires_at=now - timedelta(days=1),
        )
    )
    await storage.memory.save_memory_item(
        MemoryItemModel(
            user_id=1,
            chat_id=2,
            message_thread_id=0,
            project_path="/ttl",
            memory_type="decision",
            content="we will use postgres",
            priority=90,
            timestamp=now,
            ttl_expires_at=now + timedelta(days=30),
        )
    )

    runtime = await service.get_runtime_settings(
        user_id=1,
        chat_id=2,
        message_thread_id=0,
    )
    await service.apply_pre_hook(
        prompt="status?",
        controls={
            "provider": "copilot",
            "copilot_model": "gpt-5-mini",
            "reasoning_effort": "medium",
            "skill_directories": [],
            "disabled_skills": [],
            "mcp_env_value_mode": "raw",
            "external_cli_server": None,
        },
        user_id=1,
        chat_id=2,
        message_thread_id=0,
        project_path=Path("/ttl"),
    )

    async with storage.db_manager.get_connection() as conn:
        cursor = await conn.execute(
            "SELECT is_active, eviction_reason FROM memory_items WHERE content = ?",
            ("old memory",),
        )
        row = await cursor.fetchone()
        assert row["is_active"] == 0
        assert row["eviction_reason"] == "ttl_expired"

    await service.apply_post_hook(
        prompt="Please update decision.",
        response="Decision update: we will not use postgres.",
        user_id=1,
        chat_id=2,
        message_thread_id=0,
        project_path=Path("/ttl"),
        source_session_id="s2",
        source_message_id=2,
        runtime_settings=runtime,
        success=True,
        elapsed_ms=42,
    )

    async with storage.db_manager.get_connection() as conn:
        cursor = await conn.execute("""
            SELECT conflict_status FROM memory_items
            WHERE content LIKE '%not use postgres%'
            ORDER BY memory_id DESC LIMIT 1
        """)
        row = await cursor.fetchone()
        assert row is not None
        assert row["conflict_status"] == "conflict"


async def test_ai_timeout_fallback_and_metrics(memory_env):
    """AI timeout should fall back to deterministic flow and record metrics."""
    service, storage = memory_env

    async def slow_rerank(candidates, runtime):  # noqa: ARG001
        await asyncio.sleep(1.2)
        return list(candidates)

    async def slow_extract(items, runtime):  # noqa: ARG001
        await asyncio.sleep(1.2)
        return list(items)

    service._ai_rerank = slow_rerank  # type: ignore[method-assign]
    service._ai_extract = slow_extract  # type: ignore[method-assign]

    runtime = await service.get_runtime_settings(
        user_id=55,
        chat_id=-66,
        message_thread_id=0,
    )

    await service.apply_post_hook(
        prompt="Remember TODO: add health check endpoint.",
        response="Acknowledged TODO.",
        user_id=55,
        chat_id=-66,
        message_thread_id=0,
        project_path=Path("/slow"),
        source_session_id="slow-1",
        source_message_id=1,
        runtime_settings=runtime,
        success=True,
        elapsed_ms=100,
    )
    await service.apply_pre_hook(
        prompt="What should we do next?",
        controls={
            "provider": "claude",
            "copilot_model": "gpt-5-mini",
            "reasoning_effort": "medium",
            "skill_directories": [],
            "disabled_skills": [],
            "mcp_env_value_mode": "raw",
            "external_cli_server": None,
        },
        user_id=55,
        chat_id=-66,
        message_thread_id=0,
        project_path=Path("/slow"),
    )

    events = await storage.memory.get_recent_events(55, -66, 0, limit=50)
    fallback_reasons = {
        event.fallback_reason for event in events if event.fallback_reason
    }
    assert "ai_extractor_timeout" in fallback_reasons
    assert "ai_reranker_timeout" in fallback_reasons

    metrics = await service.get_metrics_summary(hours=24)
    assert "fallback_events" in metrics
    assert metrics["fallback_events"] >= 2


async def test_memory_system_plus_off_keeps_prompt_unchanged(memory_env):
    """Turning memory system off should preserve original prompt behavior."""
    service, _ = memory_env
    runtime = await service.update_runtime_settings(
        user_id=9,
        chat_id=10,
        message_thread_id=0,
        patch={"memory_system_plus_enabled": False},
        actor_user_id=9,
        source="test",
    )

    pre = await service.apply_pre_hook(
        prompt="plain prompt",
        controls={
            "provider": "copilot",
            "copilot_model": "gpt-5-mini",
            "reasoning_effort": "medium",
            "skill_directories": [],
            "disabled_skills": [],
            "mcp_env_value_mode": "raw",
            "external_cli_server": None,
        },
        user_id=9,
        chat_id=10,
        message_thread_id=0,
        project_path=Path("/off"),
    )
    assert runtime.memory_system_plus_enabled is False
    assert pre.prompt == "plain prompt"
