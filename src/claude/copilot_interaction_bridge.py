"""Shared interaction bridge for Copilot ask_user/permission flows."""

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _InteractionRecord:
    interaction_id: str
    kind: str
    user_id: int
    chat_id: int
    message_thread_id: Optional[int]
    created_at: datetime
    timeout_seconds: int
    future: "asyncio.Future[Any]"
    metadata: Dict[str, Any] = field(default_factory=dict)
    timeout_task: Optional["asyncio.Task[None]"] = None

    @property
    def scope(self) -> str:
        return f"{self.user_id}:{self.chat_id}:{self.message_thread_id or 0}"


class CopilotInteractionBridge:
    """Tracks and resolves Copilot interactive requests by scoped identity."""

    def __init__(
        self,
        ask_user_timeout_seconds: int = 300,
        permission_timeout_seconds: int = 120,
    ):
        self.ask_user_timeout_seconds = ask_user_timeout_seconds
        self.permission_timeout_seconds = permission_timeout_seconds
        self._records: Dict[str, _InteractionRecord] = {}
        self._lock = asyncio.Lock()

    async def create_ask_user(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        question: str,
        choices: List[str],
        allow_freeform: bool,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create ask_user interaction and return render metadata."""
        timeout = int(timeout_seconds or self.ask_user_timeout_seconds)
        record = self._new_record(
            kind="ask_user",
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            timeout_seconds=timeout,
            metadata={
                "question": question,
                "choices": choices,
                "allow_freeform": allow_freeform,
            },
        )
        await self._store_record(record)
        return {
            "interaction_id": record.interaction_id,
            "question": question,
            "choices": choices,
            "allow_freeform": allow_freeform,
        }

    async def create_permission_request(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        kind: str,
        tool_call_id: str,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create permission request interaction and return render metadata."""
        timeout = int(timeout_seconds or self.permission_timeout_seconds)
        record = self._new_record(
            kind="permission_request",
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            timeout_seconds=timeout,
            metadata={"kind": kind, "tool_call_id": tool_call_id},
        )
        await self._store_record(record)
        return {
            "interaction_id": record.interaction_id,
            "kind": kind,
            "tool_call_id": tool_call_id,
        }

    async def wait_for_result(self, interaction_id: str) -> Any:
        """Wait for interaction resolution, with timeout-safe default fallback."""
        async with self._lock:
            record = self._records.get(interaction_id)
        if not record:
            return None

        try:
            result = await asyncio.wait_for(
                asyncio.shield(record.future),
                timeout=record.timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "Copilot interaction timed out",
                interaction_id=interaction_id,
                kind=record.kind,
                scope=record.scope,
            )
            default = "" if record.kind == "ask_user" else False
            await self.resolve(
                interaction_id=interaction_id,
                value=default,
                user_id=record.user_id,
                chat_id=record.chat_id,
                message_thread_id=record.message_thread_id,
            )
            return default

    async def resolve(
        self,
        *,
        interaction_id: str,
        value: Any,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
    ) -> bool:
        """Resolve an interaction if scope matches and it is still pending."""
        async with self._lock:
            record = self._records.get(interaction_id)
            if not record:
                return False
            if not self._scope_matches(record, user_id, chat_id, message_thread_id):
                return False
            if record.future.done():
                self._cleanup_record(record)
                return False
            record.future.set_result(value)
            self._cleanup_record(record)
            return True

    async def resolve_pending_freeform(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        value: str,
    ) -> Optional[str]:
        """Resolve latest ask_user interaction in scope that allows freeform input."""
        async with self._lock:
            candidates = [
                r
                for r in self._records.values()
                if r.kind == "ask_user"
                and not r.future.done()
                and bool(r.metadata.get("allow_freeform", True))
                and self._scope_matches(r, user_id, chat_id, message_thread_id)
            ]
            if not candidates:
                return None

            record = max(candidates, key=lambda r: r.created_at)
            record.future.set_result(value)
            interaction_id = record.interaction_id
            self._cleanup_record(record)
            return interaction_id

    async def get(self, interaction_id: str) -> Optional[Dict[str, Any]]:
        """Get pending interaction metadata."""
        async with self._lock:
            record = self._records.get(interaction_id)
            if not record:
                return None
            data = dict(record.metadata)
            data.update(
                {
                    "interaction_id": record.interaction_id,
                    "kind": record.kind,
                    "user_id": record.user_id,
                    "chat_id": record.chat_id,
                    "message_thread_id": record.message_thread_id,
                    "created_at": record.created_at.isoformat(),
                    "timeout_seconds": record.timeout_seconds,
                }
            )
            return data

    async def pending_count(self) -> int:
        """Return number of pending interactions."""
        async with self._lock:
            return len(self._records)

    def _new_record(
        self,
        *,
        kind: str,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        timeout_seconds: int,
        metadata: Dict[str, Any],
    ) -> _InteractionRecord:
        loop = asyncio.get_running_loop()
        interaction_id = secrets.token_hex(6)
        return _InteractionRecord(
            interaction_id=interaction_id,
            kind=kind,
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            created_at=_utc_now(),
            timeout_seconds=timeout_seconds,
            future=loop.create_future(),
            metadata=metadata,
        )

    async def _store_record(self, record: _InteractionRecord) -> None:
        async with self._lock:
            self._records[record.interaction_id] = record
            record.timeout_task = asyncio.create_task(
                self._auto_timeout(record.interaction_id, record.timeout_seconds)
            )
        logger.info(
            "Copilot interaction created",
            interaction_id=record.interaction_id,
            kind=record.kind,
            scope=record.scope,
        )

    async def _auto_timeout(self, interaction_id: str, timeout_seconds: int) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
            async with self._lock:
                record = self._records.get(interaction_id)
                if not record or record.future.done():
                    return
                default = "" if record.kind == "ask_user" else False
                record.future.set_result(default)
                self._cleanup_record(record)
            logger.warning(
                "Copilot interaction auto-expired",
                interaction_id=interaction_id,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            return

    def _cleanup_record(self, record: _InteractionRecord) -> None:
        self._records.pop(record.interaction_id, None)
        task = record.timeout_task
        if task and not task.done():
            task.cancel()

    @staticmethod
    def _scope_matches(
        record: _InteractionRecord,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
    ) -> bool:
        return (
            record.user_id == user_id
            and record.chat_id == chat_id
            and (record.message_thread_id or 0) == (message_thread_id or 0)
        )
