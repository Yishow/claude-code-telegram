## Why

Current session continuity relies primarily on provider session resume. That works for short-term continuity, but it does not provide explicit, reusable long-term memory controls across conversations. We need a controlled memory pipeline now to reduce context bloat, improve relevance, and keep behavior predictable as usage grows.

## What Changes

- Add a configurable memory hooks pipeline with two stages:
  - post-session condensation hook: distill each completed interaction into structured memory artifacts.
  - pre-session context assembly hook: build a compact, task-relevant context package before sending a new prompt.
- Add explicit memory policies (scope, retention, token budget, and priority rules) to avoid unbounded history replay.
- Add kill-switch feature flags so memory hooks can be disabled quickly without changing core chat/session logic.
- Add observability for memory pipeline decisions (selected memories, dropped memories, token estimate, and fallback reason).
- Keep baseline behavior safe: when hooks are disabled or fail, the system falls back to current session resume behavior.

## Capabilities

### New Capabilities
- `memory-hooks-pipeline`: Introduce pre/post hooks to transform raw interaction history into reusable compact memory context.
- `memory-context-policy`: Define deterministic rules for memory selection, truncation, retention, and failure fallback.
- `memory-ops-observability`: Record memory pipeline decisions for debugging, auditability, and quality tuning.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `src/bot/handlers/message.py` for pre-send prompt assembly entrypoint.
  - `src/storage/*` for storing condensed memory artifacts and retrieval.
  - `src/config/settings.py` and `.env.example` for feature flags and policy configuration.
  - `src/claude/*` for prompt handoff integration and fallback handling.
- Affected systems:
  - Token usage profile and response relevance behavior.
  - Session continuity behavior under large conversation history.
- Operational impact:
  - Additional storage and indexing for memory artifacts.
  - New monitoring signals for memory quality and failure rate.
