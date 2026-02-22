## Why

Recent Copilot SDK releases (v0.1.21 to v0.1.26-preview.0) introduced APIs and runtime options that this bot does not yet expose. Adding these capabilities will reduce operational blind spots, improve multi-session reliability, and let operators use SDK-native controls instead of ad-hoc workarounds.

## What Changes

- Add a bot-level Copilot health/introspection command backed by SDK status/auth/models metadata APIs.
- Add skill management controls for `skillDirectories` and `disabledSkills`.
- Add a session operations console to list and delete Copilot sessions.
- Add runtime reasoning mode control via `reasoning_effort`.
- Add context-drift detection UX using `context_changed` events.
- Add execution mode to connect to an external Copilot CLI server.
- Add per-project `configDir` isolation for auth/cache separation.
- Harden multimodal request path and attachment validation for Copilot SDK message APIs.
- Add operator controls for MCP `envValueMode` policy.
- Add reliability and observability upgrades for high-concurrency usage (large JSON-RPC payload handling, unsubscribe safety, timeout/watchdog telemetry).

## Capabilities

### New Capabilities
- `copilot-health-and-introspection`: Expose SDK health metadata, auth status, and model/runtime metadata in bot commands.
- `copilot-session-and-context-ops`: Add session listing/deletion and context drift awareness using SDK session/context events.
- `copilot-runtime-and-skill-controls`: Add operator/user controls for skills, reasoning effort, external server mode, and per-project config isolation.
- `copilot-mcp-policy-and-reliability`: Add MCP env policy controls plus reliability hardening and telemetry for concurrency and stream lifecycle.

### Modified Capabilities
- `copilot-session-and-observability`: Extend existing observability expectations with new SDK-native telemetry signals and operational dashboards.

## Impact

- Affected code areas:
  - `src/claude/copilot_sdk_integration.py`
  - `src/claude/copilot_integration.py`
  - `src/bot/orchestrator.py`
  - `src/bot/handlers/command.py`
  - `src/bot/handlers/callback.py`
  - `src/config/settings.py`
  - `src/storage/*` (if persisting new runtime/session metadata)
  - tests in `tests/unit/test_claude/`, `tests/unit/test_bot/`, and integration tests
- Config and operational impact:
  - New runtime switches for CLI server mode, configDir policy, skill directories, reasoning defaults, and MCP env mode.
  - New command surfaces for Copilot status/session/runtime control.
