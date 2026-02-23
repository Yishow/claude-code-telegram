"""Data access layer using repository pattern.

Features:
- Clean data access API
- Query optimization
- Error handling
"""

import json
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

import structlog

from .database import DatabaseManager
from .models import (
    AuditLogModel,
    CostTrackingModel,
    MemoryEventModel,
    MemoryItemModel,
    MemoryRuntimeSettingsModel,
    MessageModel,
    ProjectThreadModel,
    SessionModel,
    ToolUsageModel,
    UserModel,
)

logger = structlog.get_logger()


class UserRepository:
    """User data access."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    async def get_user(self, user_id: int) -> Optional[UserModel]:
        """Get user by ID."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            return UserModel.from_row(row) if row else None

    async def create_user(self, user: UserModel) -> UserModel:
        """Create new user."""
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO users
                (user_id, telegram_username, first_seen,
                 last_active, is_allowed)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    user.user_id,
                    user.telegram_username,
                    user.first_seen or datetime.now(UTC),
                    user.last_active or datetime.now(UTC),
                    user.is_allowed,
                ),
            )
            await conn.commit()

            logger.info(
                "Created user", user_id=user.user_id, username=user.telegram_username
            )
            return user

    async def update_user(self, user: UserModel):
        """Update user data."""
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                UPDATE users
                SET telegram_username = ?, last_active = ?,
                    total_cost = ?, message_count = ?, session_count = ?
                WHERE user_id = ?
            """,
                (
                    user.telegram_username,
                    user.last_active or datetime.now(UTC),
                    user.total_cost,
                    user.message_count,
                    user.session_count,
                    user.user_id,
                ),
            )
            await conn.commit()

    async def get_allowed_users(self) -> List[int]:
        """Get list of allowed user IDs."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT user_id FROM users WHERE is_allowed = TRUE"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def set_user_allowed(self, user_id: int, allowed: bool):
        """Set user allowed status."""
        async with self.db.get_connection() as conn:
            await conn.execute(
                "UPDATE users SET is_allowed = ? WHERE user_id = ?", (allowed, user_id)
            )
            await conn.commit()

            logger.info("Updated user permissions", user_id=user_id, allowed=allowed)

    async def get_all_users(self) -> List[UserModel]:
        """Get all users."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute("SELECT * FROM users ORDER BY first_seen DESC")
            rows = await cursor.fetchall()
            return [UserModel.from_row(row) for row in rows]


class SessionRepository:
    """Session data access."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    async def get_session(self, session_id: str) -> Optional[SessionModel]:
        """Get session by ID."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            return SessionModel.from_row(row) if row else None

    async def create_session(self, session: SessionModel) -> SessionModel:
        """Create new session."""
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO sessions
                (session_id, user_id, project_path, created_at, last_used, display_name)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    session.session_id,
                    session.user_id,
                    session.project_path,
                    session.created_at,
                    session.last_used,
                    session.display_name,
                ),
            )
            await conn.commit()

            logger.info(
                "Created session",
                session_id=session.session_id,
                user_id=session.user_id,
            )
            return session

    async def update_session(self, session: SessionModel):
        """Update session data."""
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                UPDATE sessions
                SET last_used = ?, total_cost = ?, total_turns = ?,
                    message_count = ?, display_name = ?, is_active = ?
                WHERE session_id = ?
            """,
                (
                    session.last_used,
                    session.total_cost,
                    session.total_turns,
                    session.message_count,
                    session.display_name,
                    session.is_active,
                    session.session_id,
                ),
            )
            await conn.commit()

    async def get_user_sessions(
        self, user_id: int, active_only: bool = True
    ) -> List[SessionModel]:
        """Get sessions for user."""
        async with self.db.get_connection() as conn:
            query = "SELECT * FROM sessions WHERE user_id = ?"
            params = [user_id]

            if active_only:
                query += " AND is_active = TRUE"

            query += " ORDER BY last_used DESC"

            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [SessionModel.from_row(row) for row in rows]

    async def cleanup_old_sessions(self, days: int = 30) -> int:
        """Mark old sessions as inactive."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE sessions
                SET is_active = FALSE
                WHERE last_used < datetime('now', '-' || ? || ' days')
                  AND is_active = TRUE
            """,
                (days,),
            )
            await conn.commit()

            affected = cursor.rowcount
            logger.info("Cleaned up old sessions", count=affected, days=days)
            return affected

    async def get_sessions_by_project(self, project_path: str) -> List[SessionModel]:
        """Get sessions for a specific project."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM sessions
                WHERE project_path = ? AND is_active = TRUE
                ORDER BY last_used DESC
            """,
                (project_path,),
            )
            rows = await cursor.fetchall()
            return [SessionModel.from_row(row) for row in rows]


class ProjectThreadRepository:
    """Project-thread mapping data access."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    async def get_by_chat_thread(
        self, chat_id: int, message_thread_id: int
    ) -> Optional[ProjectThreadModel]:
        """Find active mapping by chat+thread."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM project_threads
                WHERE chat_id = ? AND message_thread_id = ? AND is_active = TRUE
            """,
                (chat_id, message_thread_id),
            )
            row = await cursor.fetchone()
            return ProjectThreadModel.from_row(row) if row else None

    async def get_by_chat_project(
        self, chat_id: int, project_slug: str
    ) -> Optional[ProjectThreadModel]:
        """Find mapping by chat+project slug."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM project_threads
                WHERE chat_id = ? AND project_slug = ?
            """,
                (chat_id, project_slug),
            )
            row = await cursor.fetchone()
            return ProjectThreadModel.from_row(row) if row else None

    async def upsert_mapping(
        self,
        project_slug: str,
        chat_id: int,
        message_thread_id: int,
        topic_name: str,
        is_active: bool = True,
    ) -> ProjectThreadModel:
        """Create or update mapping by unique chat+project key."""
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO project_threads (
                    project_slug, chat_id, message_thread_id, topic_name, is_active
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, project_slug) DO UPDATE SET
                    message_thread_id = excluded.message_thread_id,
                    topic_name = excluded.topic_name,
                    is_active = excluded.is_active,
                    updated_at = CURRENT_TIMESTAMP
            """,
                (project_slug, chat_id, message_thread_id, topic_name, is_active),
            )
            await conn.commit()

        mapping = await self.get_by_chat_project(
            chat_id=chat_id, project_slug=project_slug
        )
        if not mapping:
            raise RuntimeError("Failed to upsert project thread mapping")
        return mapping

    async def deactivate_missing_projects(
        self, chat_id: int, active_project_slugs: List[str]
    ) -> int:
        """Deactivate mappings for projects no longer enabled/present."""
        async with self.db.get_connection() as conn:
            if active_project_slugs:
                placeholders = ",".join("?" for _ in active_project_slugs)
                query = f"""
                    UPDATE project_threads
                    SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE chat_id = ?
                      AND project_slug NOT IN ({placeholders})
                      AND is_active = TRUE
                """
                params = [chat_id] + active_project_slugs
                cursor = await conn.execute(query, params)
            else:
                cursor = await conn.execute(
                    """
                    UPDATE project_threads
                    SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE chat_id = ? AND is_active = TRUE
                """,
                    (chat_id,),
                )
            await conn.commit()
            return cursor.rowcount

    async def list_stale_active_mappings(
        self, chat_id: int, active_project_slugs: List[str]
    ) -> List[ProjectThreadModel]:
        """List active mappings that are no longer enabled/present."""
        async with self.db.get_connection() as conn:
            if active_project_slugs:
                placeholders = ",".join("?" for _ in active_project_slugs)
                query = f"""
                    SELECT * FROM project_threads
                    WHERE chat_id = ?
                      AND is_active = TRUE
                      AND project_slug NOT IN ({placeholders})
                    ORDER BY project_slug ASC
                """
                params = [chat_id] + active_project_slugs
                cursor = await conn.execute(query, params)
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM project_threads
                    WHERE chat_id = ? AND is_active = TRUE
                    ORDER BY project_slug ASC
                """,
                    (chat_id,),
                )
            rows = await cursor.fetchall()
            return [ProjectThreadModel.from_row(row) for row in rows]

    async def set_active(self, chat_id: int, project_slug: str, is_active: bool) -> int:
        """Set active flag for a mapping by chat+project."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE project_threads
                SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE chat_id = ? AND project_slug = ?
            """,
                (is_active, chat_id, project_slug),
            )
            await conn.commit()
            return cursor.rowcount

    async def list_by_chat(
        self, chat_id: int, active_only: bool = True
    ) -> List[ProjectThreadModel]:
        """List mappings for a chat."""
        async with self.db.get_connection() as conn:
            query = "SELECT * FROM project_threads WHERE chat_id = ?"
            params = [chat_id]
            if active_only:
                query += " AND is_active = TRUE"
            query += " ORDER BY project_slug ASC"
            cursor = await conn.execute(query, params)
            rows = await cursor.fetchall()
            return [ProjectThreadModel.from_row(row) for row in rows]


class MemoryRepository:
    """Memory system data access."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    @staticmethod
    def build_scope_key(user_id: int, chat_id: int, message_thread_id: int = 0) -> str:
        """Build stable scope key."""
        return f"{user_id}:{chat_id}:{message_thread_id}"

    async def get_runtime_settings(
        self, user_id: int, chat_id: int, message_thread_id: int = 0
    ) -> Optional[MemoryRuntimeSettingsModel]:
        """Get memory runtime settings for a scope."""
        scope_key = self.build_scope_key(user_id, chat_id, message_thread_id)
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM memory_runtime_settings
                WHERE scope_key = ?
            """,
                (scope_key,),
            )
            row = await cursor.fetchone()
            return MemoryRuntimeSettingsModel.from_row(row) if row else None

    async def upsert_runtime_settings(
        self, settings: MemoryRuntimeSettingsModel
    ) -> MemoryRuntimeSettingsModel:
        """Create or update runtime settings for a scope."""
        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO memory_runtime_settings (
                    scope_key,
                    user_id,
                    chat_id,
                    message_thread_id,
                    memory_system_plus_enabled,
                    memory_hooks_enabled,
                    memory_pre_hook_enabled,
                    memory_post_hook_enabled,
                    memory_ai_enhancement_enabled,
                    memory_ai_extractor_enabled,
                    memory_ai_reranker_enabled,
                    memory_ai_conflict_detector_enabled,
                    memory_ai_periodic_review_enabled,
                    memory_profile,
                    memory_ai_model,
                    memory_ai_timeout_seconds,
                    memory_recall_limit,
                    memory_injection_token_budget
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    memory_system_plus_enabled=excluded.memory_system_plus_enabled,
                    memory_hooks_enabled=excluded.memory_hooks_enabled,
                    memory_pre_hook_enabled=excluded.memory_pre_hook_enabled,
                    memory_post_hook_enabled=excluded.memory_post_hook_enabled,
                    memory_ai_enhancement_enabled=excluded.memory_ai_enhancement_enabled,
                    memory_ai_extractor_enabled=excluded.memory_ai_extractor_enabled,
                    memory_ai_reranker_enabled=excluded.memory_ai_reranker_enabled,
                    memory_ai_conflict_detector_enabled=excluded.memory_ai_conflict_detector_enabled,
                    memory_ai_periodic_review_enabled=excluded.memory_ai_periodic_review_enabled,
                    memory_profile=excluded.memory_profile,
                    memory_ai_model=excluded.memory_ai_model,
                    memory_ai_timeout_seconds=excluded.memory_ai_timeout_seconds,
                    memory_recall_limit=excluded.memory_recall_limit,
                    memory_injection_token_budget=excluded.memory_injection_token_budget,
                    updated_at=CURRENT_TIMESTAMP
            """,
                (
                    settings.scope_key,
                    settings.user_id,
                    settings.chat_id,
                    settings.message_thread_id,
                    settings.memory_system_plus_enabled,
                    settings.memory_hooks_enabled,
                    settings.memory_pre_hook_enabled,
                    settings.memory_post_hook_enabled,
                    settings.memory_ai_enhancement_enabled,
                    settings.memory_ai_extractor_enabled,
                    settings.memory_ai_reranker_enabled,
                    settings.memory_ai_conflict_detector_enabled,
                    settings.memory_ai_periodic_review_enabled,
                    settings.memory_profile,
                    settings.memory_ai_model,
                    settings.memory_ai_timeout_seconds,
                    settings.memory_recall_limit,
                    settings.memory_injection_token_budget,
                ),
            )
            await conn.commit()

        refreshed = await self.get_runtime_settings(
            settings.user_id,
            settings.chat_id,
            settings.message_thread_id,
        )
        if not refreshed:
            raise RuntimeError("Failed to upsert memory runtime settings")
        return refreshed

    async def save_memory_item(self, memory_item: MemoryItemModel) -> int:
        """Persist a memory item and return memory_id."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO memory_items (
                    user_id,
                    chat_id,
                    message_thread_id,
                    project_path,
                    memory_type,
                    content,
                    priority,
                    source_session_id,
                    source_message_id,
                    timestamp,
                    ttl_expires_at,
                    conflict_with_id,
                    conflict_status,
                    is_active,
                    eviction_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    memory_item.user_id,
                    memory_item.chat_id,
                    memory_item.message_thread_id,
                    memory_item.project_path,
                    memory_item.memory_type,
                    memory_item.content,
                    memory_item.priority,
                    memory_item.source_session_id,
                    memory_item.source_message_id,
                    memory_item.timestamp,
                    memory_item.ttl_expires_at,
                    memory_item.conflict_with_id,
                    memory_item.conflict_status,
                    memory_item.is_active,
                    memory_item.eviction_reason,
                ),
            )
            await conn.commit()
            return cursor.lastrowid

    async def save_memory_items(self, memory_items: List[MemoryItemModel]) -> List[int]:
        """Persist multiple memory items."""
        ids: List[int] = []
        for item in memory_items:
            ids.append(await self.save_memory_item(item))
        return ids

    async def get_recall_candidates(
        self,
        user_id: int,
        chat_id: int,
        message_thread_id: int,
        project_path: str,
        limit: int,
        now: datetime,
    ) -> List[MemoryItemModel]:
        """Get active memory items for current scope and directory."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM memory_items
                WHERE user_id = ?
                  AND chat_id = ?
                  AND message_thread_id = ?
                  AND project_path = ?
                  AND is_active = TRUE
                  AND (
                      ttl_expires_at IS NULL
                      OR ttl_expires_at > ?
                  )
                ORDER BY priority DESC, timestamp DESC, memory_id DESC
                LIMIT ?
            """,
                (
                    user_id,
                    chat_id,
                    message_thread_id,
                    project_path,
                    now,
                    limit,
                ),
            )
            rows = await cursor.fetchall()
            return [MemoryItemModel.from_row(row) for row in rows]

    async def mark_expired_items(
        self,
        now: datetime,
        user_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        message_thread_id: Optional[int] = None,
        project_path: Optional[str] = None,
    ) -> int:
        """Evict expired memory items and mark reason.

        Optional scope filters can reduce write contention during request-time cleanup.
        """
        scope_clauses: List[str] = []
        scope_params: List[Any] = []
        if user_id is not None:
            scope_clauses.append("user_id = ?")
            scope_params.append(user_id)
        if chat_id is not None:
            scope_clauses.append("chat_id = ?")
            scope_params.append(chat_id)
        if message_thread_id is not None:
            scope_clauses.append("message_thread_id = ?")
            scope_params.append(message_thread_id)
        if project_path is not None:
            scope_clauses.append("project_path = ?")
            scope_params.append(project_path)

        scope_sql = ""
        if scope_clauses:
            scope_sql = " AND " + " AND ".join(scope_clauses)

        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                f"""
                UPDATE memory_items
                SET is_active = FALSE,
                    eviction_reason = COALESCE(eviction_reason, 'ttl_expired')
                WHERE is_active = TRUE
                  AND ttl_expires_at IS NOT NULL
                  AND ttl_expires_at <= ?
                  {scope_sql}
            """,
                (now, *scope_params),
            )
            await conn.commit()
            return cursor.rowcount

    async def mark_memory_conflict(
        self,
        memory_id: int,
        conflict_with_id: int,
        conflict_status: str = "conflict",
    ) -> int:
        """Mark conflict relationship for a memory item."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE memory_items
                SET conflict_with_id = ?,
                    conflict_status = ?
                WHERE memory_id = ?
            """,
                (conflict_with_id, conflict_status, memory_id),
            )
            await conn.commit()
            return cursor.rowcount

    async def log_event(self, event: MemoryEventModel) -> int:
        """Persist a structured memory event."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO memory_events (
                    user_id,
                    chat_id,
                    message_thread_id,
                    project_path,
                    event_type,
                    event_payload,
                    fallback_reason,
                    timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    event.user_id,
                    event.chat_id,
                    event.message_thread_id,
                    event.project_path,
                    event.event_type,
                    json.dumps(event.event_payload or {}),
                    event.fallback_reason,
                    event.timestamp,
                ),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_recent_events(
        self,
        user_id: int,
        chat_id: int,
        message_thread_id: int = 0,
        limit: int = 20,
    ) -> List[MemoryEventModel]:
        """Get recent memory events for a scope."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM memory_events
                WHERE user_id = ?
                  AND chat_id = ?
                  AND message_thread_id = ?
                ORDER BY timestamp DESC, event_id DESC
                LIMIT ?
            """,
                (user_id, chat_id, message_thread_id, limit),
            )
            rows = await cursor.fetchall()
            return [MemoryEventModel.from_row(row) for row in rows]

    async def get_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get basic memory pipeline metrics summary."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    SUM(CASE WHEN fallback_reason IS NOT NULL THEN 1 ELSE 0 END) AS fallback_events,
                    SUM(CASE WHEN event_type = 'memory_assembly' THEN 1 ELSE 0 END) AS assembly_events,
                    SUM(CASE WHEN event_type = 'memory_profile_switch' THEN 1 ELSE 0 END) AS profile_switch_events
                FROM memory_events
                WHERE timestamp >= datetime('now', '-' || ? || ' hours')
            """,
                (hours,),
            )
            row = await cursor.fetchone()
            result = dict(row) if row else {}
            return {
                "hours": hours,
                "total_events": int(result.get("total_events") or 0),
                "fallback_events": int(result.get("fallback_events") or 0),
                "assembly_events": int(result.get("assembly_events") or 0),
                "profile_switch_events": int(result.get("profile_switch_events") or 0),
            }


class MessageRepository:
    """Message data access."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    async def save_message(self, message: MessageModel) -> int:
        """Save message and return ID."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO messages
                (session_id, user_id, timestamp, prompt,
                 response, cost, duration_ms, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    message.session_id,
                    message.user_id,
                    message.timestamp,
                    message.prompt,
                    message.response,
                    message.cost,
                    message.duration_ms,
                    message.error,
                ),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_session_messages(
        self, session_id: str, limit: int = 50
    ) -> List[MessageModel]:
        """Get messages for session."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (session_id, limit),
            )
            rows = await cursor.fetchall()
            return [MessageModel.from_row(row) for row in rows]

    async def get_user_messages(
        self, user_id: int, limit: int = 100
    ) -> List[MessageModel]:
        """Get messages for user."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM messages
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()
            return [MessageModel.from_row(row) for row in rows]

    async def get_recent_messages(self, hours: int = 24) -> List[MessageModel]:
        """Get recent messages."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM messages
                WHERE timestamp > datetime('now', '-' || ? || ' hours')
                ORDER BY timestamp DESC
            """,
                (hours,),
            )
            rows = await cursor.fetchall()
            return [MessageModel.from_row(row) for row in rows]


class ToolUsageRepository:
    """Tool usage data access."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    async def save_tool_usage(self, tool_usage: ToolUsageModel) -> int:
        """Save tool usage and return ID."""
        async with self.db.get_connection() as conn:
            tool_input_json = (
                json.dumps(tool_usage.tool_input) if tool_usage.tool_input else None
            )

            cursor = await conn.execute(
                """
                INSERT INTO tool_usage
                (session_id, message_id, tool_name, tool_input,
                 timestamp, success, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    tool_usage.session_id,
                    tool_usage.message_id,
                    tool_usage.tool_name,
                    tool_input_json,
                    tool_usage.timestamp,
                    tool_usage.success,
                    tool_usage.error_message,
                ),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_session_tool_usage(self, session_id: str) -> List[ToolUsageModel]:
        """Get tool usage for session."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM tool_usage
                WHERE session_id = ?
                ORDER BY timestamp DESC
            """,
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [ToolUsageModel.from_row(row) for row in rows]

    async def get_user_tool_usage(self, user_id: int) -> List[ToolUsageModel]:
        """Get tool usage for user."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT tu.* FROM tool_usage tu
                JOIN sessions s ON tu.session_id = s.session_id
                WHERE s.user_id = ?
                ORDER BY tu.timestamp DESC
            """,
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [ToolUsageModel.from_row(row) for row in rows]

    async def get_tool_stats(self) -> List[Dict[str, any]]:
        """Get tool usage statistics."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute("""
                SELECT
                    tool_name,
                    COUNT(*) as usage_count,
                    COUNT(DISTINCT session_id) as sessions_used,
                    SUM(CASE WHEN success = TRUE THEN 1 ELSE 0 END) as success_count,
                    SUM(CASE WHEN success = FALSE THEN 1 ELSE 0 END) as error_count
                FROM tool_usage
                GROUP BY tool_name
                ORDER BY usage_count DESC
            """)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


class AuditLogRepository:
    """Audit log data access."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    async def log_event(self, audit_log: AuditLogModel) -> int:
        """Log audit event and return ID."""
        async with self.db.get_connection() as conn:
            event_data_json = (
                json.dumps(audit_log.event_data) if audit_log.event_data else None
            )

            cursor = await conn.execute(
                """
                INSERT INTO audit_log
                (user_id, event_type, event_data, success, timestamp, ip_address)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    audit_log.user_id,
                    audit_log.event_type,
                    event_data_json,
                    audit_log.success,
                    audit_log.timestamp,
                    audit_log.ip_address,
                ),
            )
            await conn.commit()
            return cursor.lastrowid

    async def get_user_audit_log(
        self, user_id: int, limit: int = 100
    ) -> List[AuditLogModel]:
        """Get audit log for user."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM audit_log
                WHERE user_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()
            return [AuditLogModel.from_row(row) for row in rows]

    async def get_recent_audit_log(self, hours: int = 24) -> List[AuditLogModel]:
        """Get recent audit log entries."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM audit_log
                WHERE timestamp > datetime('now', '-' || ? || ' hours')
                ORDER BY timestamp DESC
            """,
                (hours,),
            )
            rows = await cursor.fetchall()
            return [AuditLogModel.from_row(row) for row in rows]


class CostTrackingRepository:
    """Cost tracking data access."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    async def update_daily_cost(self, user_id: int, cost: float, date: str = None):
        """Update daily cost for user."""
        if not date:
            date = datetime.now(UTC).strftime("%Y-%m-%d")

        async with self.db.get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO cost_tracking (user_id, date, daily_cost, request_count)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(user_id, date)
                DO UPDATE SET
                    daily_cost = daily_cost + ?,
                    request_count = request_count + 1
            """,
                (user_id, date, cost, cost),
            )
            await conn.commit()

    async def get_user_daily_costs(
        self, user_id: int, days: int = 30
    ) -> List[CostTrackingModel]:
        """Get user's daily costs."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM cost_tracking
                WHERE user_id = ? AND date >= date('now', '-' || ? || ' days')
                ORDER BY date DESC
            """,
                (user_id, days),
            )
            rows = await cursor.fetchall()
            return [CostTrackingModel.from_row(row) for row in rows]

    async def get_total_costs(self, days: int = 30) -> List[Dict[str, any]]:
        """Get total costs by day."""
        async with self.db.get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT
                    date,
                    SUM(daily_cost) as total_cost,
                    SUM(request_count) as total_requests,
                    COUNT(DISTINCT user_id) as active_users
                FROM cost_tracking
                WHERE date >= date('now', '-' || ? || ' days')
                GROUP BY date
                ORDER BY date DESC
            """,
                (days,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


class AnalyticsRepository:
    """Analytics and reporting."""

    def __init__(self, db_manager: DatabaseManager):
        """Initialize repository."""
        self.db = db_manager

    async def get_user_stats(self, user_id: int) -> Dict[str, any]:
        """Get user statistics."""
        async with self.db.get_connection() as conn:
            # User summary
            cursor = await conn.execute(
                """
                SELECT
                    COUNT(DISTINCT session_id) as total_sessions,
                    COUNT(*) as total_messages,
                    SUM(cost) as total_cost,
                    AVG(cost) as avg_cost,
                    MAX(timestamp) as last_activity,
                    AVG(duration_ms) as avg_duration
                FROM messages
                WHERE user_id = ?
            """,
                (user_id,),
            )

            summary = dict(await cursor.fetchone())

            # Daily usage (last 30 days)
            cursor = await conn.execute(
                """
                SELECT
                    date(timestamp) as date,
                    COUNT(*) as messages,
                    SUM(cost) as cost,
                    COUNT(DISTINCT session_id) as sessions
                FROM messages
                WHERE user_id = ? AND timestamp >= datetime('now', '-30 days')
                GROUP BY date(timestamp)
                ORDER BY date DESC
            """,
                (user_id,),
            )

            daily_usage = [dict(row) for row in await cursor.fetchall()]

            # Most used tools
            cursor = await conn.execute(
                """
                SELECT
                    tu.tool_name,
                    COUNT(*) as usage_count
                FROM tool_usage tu
                JOIN sessions s ON tu.session_id = s.session_id
                WHERE s.user_id = ?
                GROUP BY tu.tool_name
                ORDER BY usage_count DESC
                LIMIT 10
            """,
                (user_id,),
            )

            top_tools = [dict(row) for row in await cursor.fetchall()]

            return {
                "summary": summary,
                "daily_usage": daily_usage,
                "top_tools": top_tools,
            }

    async def get_system_stats(self) -> Dict[str, any]:
        """Get system-wide statistics."""
        async with self.db.get_connection() as conn:
            # Overall stats
            cursor = await conn.execute("""
                SELECT
                    COUNT(DISTINCT user_id) as total_users,
                    COUNT(DISTINCT session_id) as total_sessions,
                    COUNT(*) as total_messages,
                    SUM(cost) as total_cost,
                    AVG(duration_ms) as avg_duration
                FROM messages
            """)

            overall = dict(await cursor.fetchone())

            # Active users (last 7 days)
            cursor = await conn.execute("""
                SELECT COUNT(DISTINCT user_id) as active_users
                FROM messages
                WHERE timestamp > datetime('now', '-7 days')
            """)

            active_users = (await cursor.fetchone())[0]
            overall["active_users_7d"] = active_users

            # Top users by cost
            cursor = await conn.execute("""
                SELECT
                    u.user_id,
                    u.telegram_username,
                    SUM(m.cost) as total_cost,
                    COUNT(m.message_id) as total_messages
                FROM messages m
                JOIN users u ON m.user_id = u.user_id
                GROUP BY u.user_id
                ORDER BY total_cost DESC
                LIMIT 10
            """)

            top_users = [dict(row) for row in await cursor.fetchall()]

            # Tool usage stats
            cursor = await conn.execute("""
                SELECT
                    tool_name,
                    COUNT(*) as usage_count,
                    COUNT(DISTINCT session_id) as sessions_used
                FROM tool_usage
                GROUP BY tool_name
                ORDER BY usage_count DESC
                LIMIT 10
            """)

            tool_stats = [dict(row) for row in await cursor.fetchall()]

            # Daily activity (last 30 days)
            cursor = await conn.execute("""
                SELECT
                    date(timestamp) as date,
                    COUNT(DISTINCT user_id) as active_users,
                    COUNT(*) as total_messages,
                    SUM(cost) as total_cost
                FROM messages
                WHERE timestamp >= datetime('now', '-30 days')
                GROUP BY date(timestamp)
                ORDER BY date DESC
            """)

            daily_activity = [dict(row) for row in await cursor.fetchall()]

            return {
                "overall": overall,
                "top_users": top_users,
                "tool_stats": tool_stats,
                "daily_activity": daily_activity,
            }
