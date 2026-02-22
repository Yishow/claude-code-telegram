## Why

Current session continuity relies primarily on provider session resume. That works for short-term continuity, but it does not provide explicit, reusable long-term memory controls across conversations. We need a controlled memory pipeline now to reduce context bloat, improve relevance, and keep behavior predictable as usage grows.

## What Changes

- Add a configurable memory hooks pipeline with two stages:
  - post-session condensation hook: distill each completed interaction into structured memory artifacts.
  - pre-session context assembly hook: build a compact, task-relevant context package before sending a new prompt.
- Add a two-layer memory architecture:
  - deterministic base layer (rule-based extraction/retrieval) as the always-safe foundation.
  - AI enhancement layer (quality boost for extraction/reranking/conflict checks) enabled by default.
- Set AI enhancement default to Copilot `gpt-5-mini` where available, with automatic fallback to deterministic base layer when unavailable or failing.
- Split AI enhancement into independently switchable modules (extractor, reranker, conflict_detector, periodic_review) instead of a single monolithic toggle.
- Add Telegram runtime controls so users can freely toggle each AI module and select AI profiles during chat sessions.
- Persist AI module/profile runtime settings in application storage (not provider session metadata) so settings survive process restarts.
- Add a top-level feature switch `memory_system_plus` as an opt-in gate. When disabled, the system MUST keep original conversation/session logic unchanged.
- Add explicit memory policies (scope, retention, token budget, and priority rules) to avoid unbounded history replay.
- Add kill-switch feature flags so memory hooks can be disabled quickly without changing core chat/session logic.
- Add observability for memory pipeline decisions (selected memories, dropped memories, token estimate, and fallback reason).
- Keep baseline behavior safe: when hooks are disabled or fail, the system falls back to current session resume behavior.

## Capabilities

### New Capabilities
- `memory-hooks-pipeline`: Introduce pre/post hooks to transform raw interaction history into reusable compact memory context.
- `memory-context-policy`: Define deterministic rules for memory selection, truncation, retention, and failure fallback.
- `memory-ai-enhancement`: Use an AI assistant (default `gpt-5-mini`) to improve memory extraction quality and retrieval relevance with fine-grained module switches and deterministic fallback.
- `memory-ops-observability`: Record memory pipeline decisions for debugging, auditability, and quality tuning.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `src/bot/handlers/message.py` for pre-send prompt assembly entrypoint.
  - `src/bot/handlers/command.py` and callback handlers for Telegram-side AI module toggles/profiles.
  - `src/bot/copilot_control_plane.py` and runtime-control helpers for command integration.
  - `src/storage/*` for storing condensed memory artifacts and retrieval.
  - `src/config/settings.py` and `.env.example` for feature flags and policy configuration.
  - `src/claude/*` for provider-aware enhancement calls, prompt handoff integration, and fallback handling.
- Affected systems:
  - Token usage profile and response relevance behavior.
  - Session continuity behavior under large conversation history.
  - Compatibility safety between legacy flow and opt-in memory+ flow.
- Operational impact:
  - Additional storage and indexing for memory artifacts.
  - New monitoring signals for memory quality, failure rate, and regression risk.
