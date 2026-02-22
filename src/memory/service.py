"""Memory hooks pipeline service."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import structlog

from ..config.settings import Settings
from ..storage.facade import Storage
from ..storage.models import (
    MemoryEventModel,
    MemoryItemModel,
    MemoryRuntimeSettingsModel,
)

logger = structlog.get_logger()

PROFILE_REASONING = {
    "fast": "low",
    "balanced": "medium",
    "quality": "high",
}

PROFILE_OVERRIDES = {
    "fast": {
        "memory_ai_extractor_enabled": True,
        "memory_ai_reranker_enabled": False,
        "memory_ai_conflict_detector_enabled": False,
        "memory_ai_periodic_review_enabled": False,
        "memory_ai_timeout_seconds": 8,
        "memory_recall_limit": 8,
        "memory_injection_token_budget": 400,
    },
    "balanced": {},
    "quality": {
        "memory_ai_extractor_enabled": True,
        "memory_ai_reranker_enabled": True,
        "memory_ai_conflict_detector_enabled": True,
        "memory_ai_periodic_review_enabled": True,
        "memory_ai_timeout_seconds": 45,
        "memory_recall_limit": 40,
        "memory_injection_token_budget": 1600,
    },
}

BOOLEAN_RUNTIME_FIELDS = {
    "memory_system_plus_enabled",
    "memory_hooks_enabled",
    "memory_pre_hook_enabled",
    "memory_post_hook_enabled",
    "memory_ai_enhancement_enabled",
    "memory_ai_extractor_enabled",
    "memory_ai_reranker_enabled",
    "memory_ai_conflict_detector_enabled",
    "memory_ai_periodic_review_enabled",
}

NEGATION_WORDS = {"not", "never", "no", "don't", "cant", "can't", "避免", "不要", "不"}


@dataclass
class MemoryPreHookResult:
    """Result of memory pre-hook processing."""

    prompt: str
    controls: Dict[str, Any]
    runtime_settings: MemoryRuntimeSettingsModel


class MemoryService:
    """Memory system deterministic + AI enhancement orchestrator."""

    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage

    @staticmethod
    def normalize_thread_id(message_thread_id: Optional[int]) -> int:
        """Normalize nullable thread id to scope key integer."""
        return message_thread_id if isinstance(message_thread_id, int) else 0

    async def get_runtime_settings(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
    ) -> MemoryRuntimeSettingsModel:
        """Load persisted runtime settings for scope, creating defaults when missing."""
        thread_id = self.normalize_thread_id(message_thread_id)
        settings = await self.storage.get_memory_runtime_settings(
            user_id, chat_id, thread_id
        )
        if settings:
            return settings

        default_settings = self._default_runtime_settings(
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=thread_id,
        )
        return await self.storage.save_memory_runtime_settings(default_settings)

    async def toggle_runtime_setting(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        field: str,
        actor_user_id: int,
        source: str,
    ) -> MemoryRuntimeSettingsModel:
        """Toggle a boolean runtime setting field."""
        if field not in BOOLEAN_RUNTIME_FIELDS:
            raise ValueError(f"Unsupported toggle field: {field}")

        current = await self.get_runtime_settings(
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )
        current_value = bool(getattr(current, field))
        return await self.update_runtime_settings(
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            patch={field: not current_value},
            actor_user_id=actor_user_id,
            source=source,
        )

    async def set_runtime_profile(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        profile: str,
        actor_user_id: int,
        source: str,
    ) -> MemoryRuntimeSettingsModel:
        """Apply a named runtime profile."""
        return await self.update_runtime_settings(
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            patch={"memory_profile": profile},
            actor_user_id=actor_user_id,
            source=source,
        )

    async def update_runtime_settings(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        patch: Dict[str, Any],
        actor_user_id: int,
        source: str,
    ) -> MemoryRuntimeSettingsModel:
        """Patch runtime settings, persist, and emit audit events."""
        current = await self.get_runtime_settings(
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
        )
        before = current.to_dict()
        before.pop("updated_at", None)

        data = current.to_dict()
        data.pop("updated_at", None)

        for key, value in patch.items():
            if key in BOOLEAN_RUNTIME_FIELDS:
                data[key] = bool(value)
            elif key == "memory_profile":
                profile = str(value).strip().lower()
                if profile not in PROFILE_OVERRIDES:
                    raise ValueError(
                        "memory profile must be fast, balanced, or quality"
                    )
                data["memory_profile"] = profile
                self._apply_profile_overrides(data, profile)
            elif key == "memory_ai_model":
                data[key] = str(value).strip() or self.settings.memory_ai_model
            elif key in {
                "memory_ai_timeout_seconds",
                "memory_recall_limit",
                "memory_injection_token_budget",
            }:
                data[key] = int(value)
            else:
                raise ValueError(f"Unsupported runtime field: {key}")

        updated = MemoryRuntimeSettingsModel(**data)
        persisted = await self.storage.save_memory_runtime_settings(updated)

        after = persisted.to_dict()
        after.pop("updated_at", None)
        await self._log_event(
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=persisted.message_thread_id,
            project_path=None,
            event_type="memory_toggle_audit",
            payload={
                "actor_user_id": actor_user_id,
                "source": source,
                "before": before,
                "after": after,
            },
        )

        if before["memory_system_plus_enabled"] != after["memory_system_plus_enabled"]:
            metrics = await self.get_metrics_summary(hours=24)
            await self._log_event(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=persisted.message_thread_id,
                project_path=None,
                event_type="memory_baseline_switch",
                payload={
                    "before_enabled": before["memory_system_plus_enabled"],
                    "after_enabled": after["memory_system_plus_enabled"],
                    "metrics_24h": metrics,
                },
            )

        return persisted

    async def apply_pre_hook(
        self,
        *,
        prompt: str,
        controls: Dict[str, Any],
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        project_path: Path,
    ) -> MemoryPreHookResult:
        """Apply pre-send memory recall + assembly pipeline."""
        thread_id = self.normalize_thread_id(message_thread_id)
        try:
            runtime = await self.get_runtime_settings(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
            )

            resolved_controls = await self._map_provider_controls(
                runtime_settings=runtime,
                controls=controls,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=runtime.message_thread_id,
                project_path=project_path,
            )

            if not runtime.memory_system_plus_enabled:
                return MemoryPreHookResult(
                    prompt=prompt,
                    controls=resolved_controls,
                    runtime_settings=runtime,
                )
            if not runtime.memory_hooks_enabled or not runtime.memory_pre_hook_enabled:
                return MemoryPreHookResult(
                    prompt=prompt,
                    controls=resolved_controls,
                    runtime_settings=runtime,
                )

            now = datetime.now(UTC)
            evicted = await self.storage.memory.mark_expired_items(
                now,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=runtime.message_thread_id,
                project_path=str(project_path.resolve()),
            )
            candidates = await self.storage.memory.get_recall_candidates(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=runtime.message_thread_id,
                project_path=str(project_path.resolve()),
                limit=runtime.memory_recall_limit,
                now=now,
            )

            ranked_candidates = candidates
            rerank_fallback_reason: Optional[str] = None
            if (
                runtime.memory_ai_enhancement_enabled
                and runtime.memory_ai_reranker_enabled
                and candidates
            ):
                try:
                    ranked_candidates = await asyncio.wait_for(
                        self._ai_rerank(candidates, runtime),
                        timeout=runtime.memory_ai_timeout_seconds,
                    )
                except TimeoutError:
                    rerank_fallback_reason = "ai_reranker_timeout"
                    ranked_candidates = candidates
                except Exception as exc:
                    rerank_fallback_reason = f"ai_reranker_error:{type(exc).__name__}"
                    ranked_candidates = candidates

            assembled_context = self._assemble_memory_context(
                ranked_candidates,
                runtime.memory_injection_token_budget,
            )
            assembled_prompt = prompt
            injection_chars = 0
            if assembled_context:
                injection_chars = len(assembled_context)
                assembled_prompt = f"{assembled_context}\n\n---\n{prompt}"

            await self._log_event(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=runtime.message_thread_id,
                project_path=str(project_path.resolve()),
                event_type="memory_assembly",
                payload={
                    "hit_count": len(candidates),
                    "evicted_count": evicted,
                    "reranked_count": len(ranked_candidates),
                    "injection_size_chars": injection_chars,
                    "memory_profile": runtime.memory_profile,
                    "model": runtime.memory_ai_model,
                },
                fallback_reason=rerank_fallback_reason,
            )

            return MemoryPreHookResult(
                prompt=assembled_prompt,
                controls=resolved_controls,
                runtime_settings=runtime,
            )
        except Exception as exc:
            fallback_runtime = self._default_runtime_settings(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=thread_id,
            )
            await self._log_event(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=thread_id,
                project_path=str(project_path.resolve()),
                event_type="memory_hook_fallback",
                payload={
                    "phase": "pre_hook",
                    "error_type": type(exc).__name__,
                },
                fallback_reason=f"pre_hook_error:{type(exc).__name__}",
            )
            return MemoryPreHookResult(
                prompt=prompt,
                controls=controls,
                runtime_settings=fallback_runtime,
            )

    async def apply_post_hook(
        self,
        *,
        prompt: str,
        response: str,
        user_id: int,
        chat_id: int,
        message_thread_id: Optional[int],
        project_path: Path,
        source_session_id: Optional[str],
        source_message_id: Optional[int],
        runtime_settings: Optional[MemoryRuntimeSettingsModel],
        success: bool,
        elapsed_ms: int,
    ) -> None:
        """Apply post-response extraction/persistence pipeline."""
        thread_id = self.normalize_thread_id(message_thread_id)
        project_scope = str(project_path.resolve())
        try:
            runtime = runtime_settings or await self.get_runtime_settings(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
            )
            thread_id = runtime.message_thread_id

            if (
                not runtime.memory_system_plus_enabled
                or not runtime.memory_hooks_enabled
                or not runtime.memory_post_hook_enabled
                or not success
            ):
                await self._log_event(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    project_path=project_scope,
                    event_type="memory_baseline_sample",
                    payload={
                        "memory_system_plus_enabled": runtime.memory_system_plus_enabled,
                        "success": success,
                        "elapsed_ms": elapsed_ms,
                    },
                )
                return

            now = datetime.now(UTC)
            validated_source_session_id: Optional[str] = None
            if source_session_id:
                existing_session = await self.storage.sessions.get_session(
                    source_session_id
                )
                if existing_session:
                    validated_source_session_id = source_session_id
            validated_source_message_id: Optional[int] = None
            if source_message_id is not None:
                async with self.storage.db_manager.get_connection() as conn:
                    cursor = await conn.execute(
                        "SELECT 1 FROM messages WHERE message_id = ?",
                        (source_message_id,),
                    )
                    if await cursor.fetchone():
                        validated_source_message_id = source_message_id
            items = self._extract_deterministic_items(
                prompt=prompt,
                response=response,
                runtime_settings=runtime,
                now=now,
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=thread_id,
                project_path=project_scope,
                source_session_id=validated_source_session_id,
                source_message_id=validated_source_message_id,
            )

            fallback_reason: Optional[str] = None
            if (
                runtime.memory_ai_enhancement_enabled
                and runtime.memory_ai_extractor_enabled
            ):
                try:
                    items = await asyncio.wait_for(
                        self._ai_extract(items, runtime),
                        timeout=runtime.memory_ai_timeout_seconds,
                    )
                except TimeoutError:
                    fallback_reason = "ai_extractor_timeout"
                except Exception as exc:
                    fallback_reason = f"ai_extractor_error:{type(exc).__name__}"

            inserted_ids: List[int] = []
            if items:
                inserted_ids = await self.storage.memory.save_memory_items(items)

            conflict_links = 0
            if (
                runtime.memory_ai_enhancement_enabled
                and runtime.memory_ai_conflict_detector_enabled
                and inserted_ids
            ):
                conflict_links = await self._run_conflict_detection(
                    runtime_settings=runtime,
                    project_path=project_scope,
                    now=now,
                    inserted_items=items,
                    inserted_ids=inserted_ids,
                )

            if (
                runtime.memory_ai_enhancement_enabled
                and runtime.memory_ai_periodic_review_enabled
            ):
                reviewed = await self.storage.memory.mark_expired_items(
                    now,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=thread_id,
                    project_path=project_scope,
                )
                if reviewed:
                    await self._log_event(
                        user_id=user_id,
                        chat_id=chat_id,
                        message_thread_id=thread_id,
                        project_path=project_scope,
                        event_type="memory_periodic_review",
                        payload={"evicted_count": reviewed},
                    )

            await self._log_event(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=thread_id,
                project_path=project_scope,
                event_type="memory_extraction",
                payload={
                    "extracted_count": len(items),
                    "saved_count": len(inserted_ids),
                    "conflict_links": conflict_links,
                    "elapsed_ms": elapsed_ms,
                    "memory_profile": runtime.memory_profile,
                },
                fallback_reason=fallback_reason,
            )
        except Exception as exc:
            await self._log_event(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=thread_id,
                project_path=project_scope,
                event_type="memory_hook_fallback",
                payload={
                    "phase": "post_hook",
                    "error_type": type(exc).__name__,
                    "success": success,
                },
                fallback_reason=f"post_hook_error:{type(exc).__name__}",
            )

    async def get_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Return memory metrics summary for observability panels."""
        return await self.storage.memory.get_metrics_summary(hours=hours)

    def _default_runtime_settings(
        self, *, user_id: int, chat_id: int, message_thread_id: int
    ) -> MemoryRuntimeSettingsModel:
        """Build default runtime settings model for a scope."""
        return MemoryRuntimeSettingsModel(
            scope_key=self.storage.memory.build_scope_key(
                user_id, chat_id, message_thread_id
            ),
            user_id=user_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            memory_system_plus_enabled=self.settings.memory_system_plus,
            memory_hooks_enabled=self.settings.memory_hooks_enabled,
            memory_pre_hook_enabled=self.settings.memory_pre_hook_enabled,
            memory_post_hook_enabled=self.settings.memory_post_hook_enabled,
            memory_ai_enhancement_enabled=self.settings.memory_ai_enhancement_enabled,
            memory_ai_extractor_enabled=self.settings.memory_ai_extractor_enabled,
            memory_ai_reranker_enabled=self.settings.memory_ai_reranker_enabled,
            memory_ai_conflict_detector_enabled=self.settings.memory_ai_conflict_detector_enabled,
            memory_ai_periodic_review_enabled=self.settings.memory_ai_periodic_review_enabled,
            memory_profile=self.settings.memory_profile_default,
            memory_ai_model=self.settings.memory_ai_model,
            memory_ai_timeout_seconds=self.settings.memory_ai_timeout_seconds,
            memory_recall_limit=self.settings.memory_recall_limit,
            memory_injection_token_budget=self.settings.memory_injection_token_budget,
        )

    def _apply_profile_overrides(self, data: Dict[str, Any], profile: str) -> None:
        """Apply profile-specific runtime overrides."""
        overrides = PROFILE_OVERRIDES.get(profile, {})
        for key, value in overrides.items():
            if key == "memory_ai_timeout_seconds":
                data[key] = max(1, min(int(value), 120))
            elif key == "memory_recall_limit":
                data[key] = max(1, min(int(value), 200))
            elif key == "memory_injection_token_budget":
                data[key] = max(100, min(int(value), 8000))
            else:
                data[key] = value

    async def _map_provider_controls(
        self,
        *,
        runtime_settings: MemoryRuntimeSettingsModel,
        controls: Dict[str, Any],
        user_id: int,
        chat_id: int,
        message_thread_id: int,
        project_path: Path,
    ) -> Dict[str, Any]:
        """Map persisted memory runtime settings to provider execution controls."""
        mapped = dict(controls)
        if not runtime_settings.memory_system_plus_enabled:
            return mapped
        if not runtime_settings.memory_ai_enhancement_enabled:
            return mapped

        provider = str(mapped.get("provider") or "").lower()
        downgraded: Dict[str, str] = {}

        desired_reasoning = PROFILE_REASONING.get(
            runtime_settings.memory_profile, "medium"
        )
        desired_model = runtime_settings.memory_ai_model

        if provider == "copilot":
            mapped["copilot_model"] = desired_model
            mapped["reasoning_effort"] = desired_reasoning
        else:
            downgraded["copilot_model"] = "provider_not_copilot"
            downgraded["reasoning_effort"] = "provider_not_copilot"

        if downgraded:
            await self._log_event(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                project_path=str(project_path.resolve()),
                event_type="provider_downgrade",
                payload={
                    "provider": provider,
                    "dropped": downgraded,
                    "strategy": "ignore_unsupported_and_continue",
                },
            )
        return mapped

    def _extract_deterministic_items(
        self,
        *,
        prompt: str,
        response: str,
        runtime_settings: MemoryRuntimeSettingsModel,
        now: datetime,
        user_id: int,
        chat_id: int,
        message_thread_id: int,
        project_path: str,
        source_session_id: Optional[str],
        source_message_id: Optional[int],
    ) -> List[MemoryItemModel]:
        """Extract deterministic structured memories from interaction text."""
        candidates = self._split_sentences(prompt) + self._split_sentences(response)
        deduped: List[str] = []
        seen = set()
        for sentence in candidates:
            normalized = self._normalize_sentence(sentence)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(sentence.strip())
            if len(deduped) >= 12:
                break

        items: List[MemoryItemModel] = []
        for sentence in deduped:
            memory_type = self._classify_memory_type(sentence)
            ttl_days = self._ttl_days_for_type(memory_type)
            ttl_expires_at = now + timedelta(days=ttl_days)
            items.append(
                MemoryItemModel(
                    user_id=user_id,
                    chat_id=chat_id,
                    message_thread_id=message_thread_id,
                    project_path=project_path,
                    memory_type=memory_type,
                    content=sentence[:500],
                    priority=self._priority_for_type(memory_type, sentence),
                    source_session_id=source_session_id,
                    source_message_id=source_message_id,
                    timestamp=now,
                    ttl_expires_at=ttl_expires_at,
                    conflict_status="none",
                    is_active=True,
                )
            )

        items.sort(key=lambda item: (item.priority, item.timestamp), reverse=True)
        return items

    def _assemble_memory_context(
        self, items: Sequence[MemoryItemModel], token_budget: int
    ) -> str:
        """Build injected memory context block under token budget."""
        if not items:
            return ""

        lines = ["[Memory Context]", "Use these scoped memories when relevant:"]
        approx_used = self._estimate_tokens("\n".join(lines))
        for index, item in enumerate(items, start=1):
            snippet = item.content.strip()
            if len(snippet) > 180:
                snippet = f"{snippet[:177]}..."
            candidate_line = f"{index}. [{item.memory_type}] {snippet}"
            candidate_tokens = self._estimate_tokens(candidate_line)
            if approx_used + candidate_tokens > token_budget:
                break
            lines.append(candidate_line)
            approx_used += candidate_tokens

        return "\n".join(lines) if len(lines) > 2 else ""

    async def _run_conflict_detection(
        self,
        *,
        runtime_settings: MemoryRuntimeSettingsModel,
        project_path: str,
        now: datetime,
        inserted_items: Sequence[MemoryItemModel],
        inserted_ids: Sequence[int],
    ) -> int:
        """Mark potential conflicts against existing memory."""
        existing = await self.storage.memory.get_recall_candidates(
            user_id=runtime_settings.user_id,
            chat_id=runtime_settings.chat_id,
            message_thread_id=runtime_settings.message_thread_id,
            project_path=project_path,
            limit=50,
            now=now,
        )

        conflict_count = 0
        for new_item, new_id in zip(inserted_items, inserted_ids):
            if new_item.memory_type not in {"decision", "preference"}:
                continue
            for old in existing:
                if old.memory_id == new_id:
                    continue
                if old.memory_type != new_item.memory_type:
                    continue
                if self._is_conflicting_text(old.content, new_item.content):
                    if old.memory_id is not None:
                        await self.storage.memory.mark_memory_conflict(
                            memory_id=new_id,
                            conflict_with_id=old.memory_id,
                            conflict_status="conflict",
                        )
                        conflict_count += 1
                        break
        return conflict_count

    async def _ai_rerank(
        self,
        candidates: Sequence[MemoryItemModel],
        runtime_settings: MemoryRuntimeSettingsModel,
    ) -> List[MemoryItemModel]:
        """AI reranker placeholder implementation."""
        ranked = list(candidates)
        ranked.sort(
            key=lambda item: (
                item.priority + (15 if item.memory_type in {"decision", "todo"} else 0),
                item.timestamp,
            ),
            reverse=True,
        )
        return ranked

    async def _ai_extract(
        self,
        items: Sequence[MemoryItemModel],
        runtime_settings: MemoryRuntimeSettingsModel,
    ) -> List[MemoryItemModel]:
        """AI extraction enhancement placeholder implementation."""
        enhanced: List[MemoryItemModel] = []
        for item in items:
            bonus = 10 if item.memory_type in {"decision", "todo"} else 0
            enhanced.append(
                MemoryItemModel(
                    memory_id=item.memory_id,
                    user_id=item.user_id,
                    chat_id=item.chat_id,
                    message_thread_id=item.message_thread_id,
                    project_path=item.project_path,
                    memory_type=item.memory_type,
                    content=item.content,
                    priority=min(100, item.priority + bonus),
                    source_session_id=item.source_session_id,
                    source_message_id=item.source_message_id,
                    timestamp=item.timestamp,
                    ttl_expires_at=item.ttl_expires_at,
                    conflict_with_id=item.conflict_with_id,
                    conflict_status=item.conflict_status,
                    is_active=item.is_active,
                    eviction_reason=item.eviction_reason,
                )
            )
        return enhanced

    async def _log_event(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_thread_id: int,
        project_path: Optional[str],
        event_type: str,
        payload: Dict[str, Any],
        fallback_reason: Optional[str] = None,
    ) -> None:
        """Persist sanitized memory event with best-effort behavior."""
        try:
            event = MemoryEventModel(
                user_id=user_id,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                project_path=project_path,
                event_type=event_type,
                event_payload=self._sanitize_payload(payload),
                fallback_reason=fallback_reason,
                timestamp=datetime.now(UTC),
            )
            await self.storage.log_memory_event(event)
        except Exception as exc:
            logger.warning(
                "Failed to persist memory event", error=str(exc), event_type=event_type
            )

    def _sanitize_payload(self, value: Any) -> Any:
        """Sanitize observability payload to avoid storing raw sensitive text."""
        if isinstance(value, dict):
            return {str(k): self._sanitize_payload(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._sanitize_payload(v) for v in value]
        if isinstance(value, str):
            return self._sanitize_text(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self._sanitize_text(str(value))

    @staticmethod
    def _sanitize_text(value: str) -> str:
        """Return hashed text descriptor instead of raw text."""
        normalized = value.strip()
        if not normalized:
            return "[empty]"
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        return f"[sha256:{digest};len:{len(normalized)}]"

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """Split text into candidate memory sentences."""
        if not text:
            return []
        compact = re.sub(r"\s+", " ", text.strip())
        parts = re.split(r"[。\n.!?]+", compact)
        return [part.strip() for part in parts if len(part.strip()) >= 8]

    @staticmethod
    def _normalize_sentence(text: str) -> str:
        """Normalize sentence for deduplication."""
        return re.sub(r"\s+", " ", text.lower().strip())

    @staticmethod
    def _classify_memory_type(sentence: str) -> str:
        """Classify memory type by sentence keywords."""
        lowered = sentence.lower()
        if any(
            token in lowered for token in ("todo", "to-do", "待辦", "next step", "must")
        ):
            return "todo"
        if any(token in lowered for token in ("prefer", "偏好", "請用", "always use")):
            return "preference"
        if any(
            token in lowered
            for token in ("decide", "decision", "決定", "we will", "i will", "將")
        ):
            return "decision"
        return "fact"

    def _ttl_days_for_type(self, memory_type: str) -> int:
        """Return ttl retention days by memory type."""
        base = self.settings.memory_retention_days
        if memory_type == "todo":
            return max(3, min(base, 21))
        if memory_type == "decision":
            return max(base, 45)
        if memory_type == "preference":
            return max(base, 60)
        return base

    @staticmethod
    def _priority_for_type(memory_type: str, content: str) -> int:
        """Return priority score (higher is stronger)."""
        base = {
            "decision": 85,
            "todo": 80,
            "preference": 75,
            "fact": 60,
        }.get(memory_type, 50)
        if "urgent" in content.lower() or "立即" in content:
            base += 10
        return min(base, 100)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate using char length heuristic."""
        return max(1, len(text) // 4)

    def _is_conflicting_text(self, lhs: str, rhs: str) -> bool:
        """Detect likely contradiction between two memory statements."""
        lhs_tokens = set(re.findall(r"[a-zA-Z\u4e00-\u9fff]+", lhs.lower()))
        rhs_tokens = set(re.findall(r"[a-zA-Z\u4e00-\u9fff]+", rhs.lower()))
        if lhs_tokens == rhs_tokens:
            return False
        overlap = lhs_tokens.intersection(rhs_tokens)
        if len(overlap) < 2:
            return False
        lhs_neg = bool(lhs_tokens.intersection(NEGATION_WORDS))
        rhs_neg = bool(rhs_tokens.intersection(NEGATION_WORDS))
        return lhs_neg != rhs_neg
