## Why

Current `provider=copilot` execution is only partially aligned with the Copilot SDK path. Interactive hooks (`ask_user`, `permission_request`) are emitted but not completed through Telegram, tool governance is weaker than the Claude provider path, and several SDK capabilities are implemented but not wired end-to-end in bot flows. This creates behavior gaps, unclear operator controls, and avoidable reliability issues.

## What Changes

- Implement end-to-end Copilot interactive workflows for `ask_user` and `permission_request` in both agentic and classic Telegram modes.
- Add callback and state-management primitives to resolve pending Copilot futures safely (reply routing, timeout, cleanup, conflict handling).
- Enforce Copilot tool governance parity with Claude path by integrating ToolMonitor validation in Copilot pre-tool flow.
- Add operator controls for provider/model selection and per-request override behavior.
- Add configurable Copilot fallback policy (SDK-only, SDK-with-CLI-fallback) with explicit user-visible behavior.
- Wire image message handling to Copilot SDK file attachments where supported.
- Persist Copilot session mapping for restart-safe resume behavior.
- Add structured telemetry and logs for Copilot interactive outcomes (timeouts, approvals, denials, fallbacks).
- Expand test coverage for interactive lifecycle, permission routing, tool denial behavior, and fallback policy.
- Update docs/config samples for Copilot runtime controls and operational guidance.

## Capabilities

### New Capabilities
- `copilot-interactive-bridge`: Deliver complete user-interaction bridge for Copilot `ask_user` and `permission_request` hooks, including Telegram UX, callback plumbing, and timeout-safe lifecycle management.
- `copilot-governance-parity`: Align Copilot tool execution safety and policy outcomes with existing Claude tool-validation expectations.
- `copilot-runtime-controls`: Provide runtime controls for provider/model/fallback behavior with clear operational visibility and deterministic behavior.
- `copilot-session-and-observability`: Improve Copilot operational reliability via session persistence, attachment wiring, and actionable telemetry.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `src/claude/copilot_sdk_integration.py`
  - `src/claude/copilot_integration.py`
  - `src/claude/facade.py`
  - `src/bot/orchestrator.py`
  - `src/bot/handlers/message.py`
  - `src/bot/handlers/callback.py`
  - `src/config/settings.py`
  - tests under `tests/unit/test_claude/` and `tests/unit/test_bot/`
- Config/API surface:
  - New/updated env settings for Copilot fallback and interaction behavior.
  - New bot-level command/callback behaviors for provider/model/interactions.
- Ops impact:
  - More deterministic Copilot behavior, fewer silent timeouts, clearer approval/audit trail.
