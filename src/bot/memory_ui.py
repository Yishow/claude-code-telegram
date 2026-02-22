"""Telegram UI helpers for memory runtime controls."""

from __future__ import annotations

from typing import Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from ..storage.models import MemoryRuntimeSettingsModel
from .utils.html_format import escape_html

TOGGLE_CODE_TO_FIELD = {
    "ms": "memory_system_plus_enabled",
    "mh": "memory_hooks_enabled",
    "pre": "memory_pre_hook_enabled",
    "post": "memory_post_hook_enabled",
    "ai": "memory_ai_enhancement_enabled",
    "ex": "memory_ai_extractor_enabled",
    "rr": "memory_ai_reranker_enabled",
    "cf": "memory_ai_conflict_detector_enabled",
    "pr": "memory_ai_periodic_review_enabled",
}

TOGGLE_ALIASES = {
    "system": "memory_system_plus_enabled",
    "system_plus": "memory_system_plus_enabled",
    "memory_system_plus": "memory_system_plus_enabled",
    "hooks": "memory_hooks_enabled",
    "pre": "memory_pre_hook_enabled",
    "post": "memory_post_hook_enabled",
    "ai": "memory_ai_enhancement_enabled",
    "extractor": "memory_ai_extractor_enabled",
    "reranker": "memory_ai_reranker_enabled",
    "conflict": "memory_ai_conflict_detector_enabled",
    "conflict_detector": "memory_ai_conflict_detector_enabled",
    "periodic": "memory_ai_periodic_review_enabled",
    "periodic_review": "memory_ai_periodic_review_enabled",
}


def resolve_toggle_field(name: str) -> Optional[str]:
    """Resolve callback code or CLI alias to runtime field name."""
    key = name.strip().lower()
    if key in TOGGLE_CODE_TO_FIELD:
        return TOGGLE_CODE_TO_FIELD[key]
    return TOGGLE_ALIASES.get(key)


def scope_from_update(update: Update) -> Tuple[int, int, Optional[int]]:
    """Extract scope tuple from an update object."""
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    message = update.effective_message
    thread_id = getattr(message, "message_thread_id", None) if message else None
    if not isinstance(thread_id, int):
        thread_id = None
    return user_id, chat_id, thread_id


def scope_from_query(query) -> Tuple[int, int, Optional[int]]:
    """Extract scope tuple from callback query."""
    user_id = query.from_user.id if query and query.from_user else 0
    chat_id = (
        query.message.chat.id if query and query.message and query.message.chat else 0
    )
    thread_id = (
        getattr(query.message, "message_thread_id", None)
        if query and query.message
        else None
    )
    if not isinstance(thread_id, int):
        thread_id = None
    return user_id, chat_id, thread_id


def format_memory_status(
    runtime: MemoryRuntimeSettingsModel, metrics_24h: Optional[dict] = None
) -> str:
    """Build memory status panel text."""
    metrics = metrics_24h or {}
    total_events = int(metrics.get("total_events", 0))
    fallback_events = int(metrics.get("fallback_events", 0))
    fallback_rate = (fallback_events / total_events * 100.0) if total_events else 0.0

    def flag(value: bool) -> str:
        return "✅" if value else "❌"

    scope_label = f"user:{runtime.user_id} chat:{runtime.chat_id} thread:{runtime.message_thread_id}"
    return (
        "🧠 <b>記憶系統+</b>\n\n"
        f"• 總開關: {flag(runtime.memory_system_plus_enabled)}\n"
        f"• Hooks: {flag(runtime.memory_hooks_enabled)} "
        f"(pre {flag(runtime.memory_pre_hook_enabled)} / "
        f"post {flag(runtime.memory_post_hook_enabled)})\n"
        f"• AI 增強: {flag(runtime.memory_ai_enhancement_enabled)} "
        f"(extractor {flag(runtime.memory_ai_extractor_enabled)}, "
        f"reranker {flag(runtime.memory_ai_reranker_enabled)}, "
        f"conflict {flag(runtime.memory_ai_conflict_detector_enabled)}, "
        f"periodic {flag(runtime.memory_ai_periodic_review_enabled)})\n"
        f"• Profile: <code>{escape_html(runtime.memory_profile)}</code>\n"
        f"• Model: <code>{escape_html(runtime.memory_ai_model)}</code>\n"
        f"• Timeout: <code>{runtime.memory_ai_timeout_seconds}s</code>\n"
        f"• Recall limit: <code>{runtime.memory_recall_limit}</code>\n"
        f"• Injection budget: <code>{runtime.memory_injection_token_budget}</code>\n"
        f"• Scope: <code>{escape_html(scope_label)}</code>\n\n"
        "📈 <b>觀測(24h)</b>\n"
        f"• total_events: <code>{total_events}</code>\n"
        f"• fallback_events: <code>{fallback_events}</code>\n"
        f"• fallback_rate: <code>{fallback_rate:.1f}%</code>"
    )


def build_memory_keyboard(runtime: MemoryRuntimeSettingsModel) -> InlineKeyboardMarkup:
    """Build inline keyboard for memory controls."""

    def label(enabled: bool, title: str) -> str:
        return f"{'✅' if enabled else '❌'} {title}"

    profile_buttons = []
    for profile in ("fast", "balanced", "quality"):
        selected = "● " if runtime.memory_profile == profile else ""
        profile_buttons.append(
            InlineKeyboardButton(
                f"{selected}{profile}",
                callback_data=f"memory:profile:{profile}",
            )
        )

    keyboard = [
        [
            InlineKeyboardButton(
                label(runtime.memory_system_plus_enabled, "記憶系統+"),
                callback_data="memory:toggle:ms",
            ),
            InlineKeyboardButton(
                label(runtime.memory_ai_enhancement_enabled, "AI 增強"),
                callback_data="memory:toggle:ai",
            ),
        ],
        [
            InlineKeyboardButton(
                label(runtime.memory_hooks_enabled, "Hooks"),
                callback_data="memory:toggle:mh",
            ),
            InlineKeyboardButton(
                label(runtime.memory_pre_hook_enabled, "Pre"),
                callback_data="memory:toggle:pre",
            ),
            InlineKeyboardButton(
                label(runtime.memory_post_hook_enabled, "Post"),
                callback_data="memory:toggle:post",
            ),
        ],
        [
            InlineKeyboardButton(
                label(runtime.memory_ai_extractor_enabled, "Extractor"),
                callback_data="memory:toggle:ex",
            ),
            InlineKeyboardButton(
                label(runtime.memory_ai_reranker_enabled, "Reranker"),
                callback_data="memory:toggle:rr",
            ),
        ],
        [
            InlineKeyboardButton(
                label(runtime.memory_ai_conflict_detector_enabled, "Conflict"),
                callback_data="memory:toggle:cf",
            ),
            InlineKeyboardButton(
                label(runtime.memory_ai_periodic_review_enabled, "Periodic"),
                callback_data="memory:toggle:pr",
            ),
        ],
        profile_buttons,
        [InlineKeyboardButton("🔄 Refresh", callback_data="memory:panel")],
    ]
    return InlineKeyboardMarkup(keyboard)
