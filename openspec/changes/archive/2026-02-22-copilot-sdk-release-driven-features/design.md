## Context

GitHub Copilot SDK recent releases introduced new control-plane APIs and runtime options that are highly relevant to this bot: status/auth/model metadata probes, session list/delete operations, reasoning effort controls, context change events, external CLI server mode, config directory options, MCP env mode, and improved reliability behavior under heavy load. Current bot integration focuses on core prompt execution and lacks explicit surfaces for these controls.

This change adds a release-driven feature layer so operators can use these SDK capabilities safely from Telegram and configuration.

## Goals / Non-Goals

**Goals:**
- Convert recent Copilot SDK capabilities into explicit bot controls and diagnostics.
- Improve operator visibility into Copilot runtime/auth/model/session health.
- Strengthen reliability for concurrent workloads with watchdogs and telemetry.
- Keep compatibility with existing Copilot request path.

**Non-Goals:**
- Replacing existing interaction bridge design from parity change.
- Building a new admin web UI (Telegram-first control surface remains primary).
- Altering Claude provider behavior.

## Decisions

### 1. Add a control-plane command layer for Copilot
Decision:
- Implement command handlers for status, session ops, reasoning control, and skill/policy introspection.

Rationale:
- Release-driven features are operational controls; command layer is the fastest safe interface.

Alternative considered:
- Env-only configuration.
  - Rejected: lacks live diagnostics and runtime adjustability.

### 2. Separate data-plane from control-plane telemetry
Decision:
- Keep message execution telemetry separate from control-plane telemetry (status/session/policy operations).

Rationale:
- Easier incident triage and trend analysis.

Alternative considered:
- Single mixed telemetry stream.
  - Rejected: lowers signal quality for operations.

### 3. Use policy-gated runtime controls
Decision:
- Runtime controls (skills/reasoning/session deletion/external mode) require policy checks to prevent accidental misuse.

Rationale:
- These controls can materially change behavior and safety profile.

Alternative considered:
- Allow any authenticated user.
  - Rejected: too broad for multi-user bot deployments.

### 4. Project-scoped config isolation over global mutation
Decision:
- Implement per-project `configDir` mapping and keep defaults fallback-safe.

Rationale:
- Limits cross-project credential/cache leakage without forcing full migration.

Alternative considered:
- Single global configDir.
  - Rejected: weak isolation.

### 5. Reliability guardrails as wrappers, not SDK forks
Decision:
- Add timeout/watchdog and stream cleanup guardrails around SDK calls rather than modifying SDK internals.

Rationale:
- Maintains upgrade path with upstream SDK versions.

Alternative considered:
- Internal SDK monkey patches.
  - Rejected: brittle and hard to maintain.

## Risks / Trade-offs

- [Overexposing controls to non-admin users] -> Mitigation: role/policy gating and audit logging.
- [Increased command complexity] -> Mitigation: concise command set with clear `/status`-style outputs.
- [Runtime config fragmentation] -> Mitigation: source-of-truth precedence rules and documented defaults.
- [Telemetry noise] -> Mitigation: categorized event schema with bounded cardinality tags.

## Migration Plan

1. Add settings/schema fields for release-driven options with safe defaults.
2. Add command handlers and callback/action routing for control-plane operations.
3. Add SDK manager wrappers for status/session/reasoning/skill/external-mode/configDir features.
4. Add telemetry events and redaction-safe logging.
5. Add tests for authorization, command behavior, and reliability guards.
6. Update docs for commands and operational policy.

Rollback strategy:
- Disable new control commands via feature flag.
- Revert to previous SDK execution-only flow while keeping compatibility settings ignored.

## Open Questions

- Which controls should be admin-only vs standard authenticated users?
- Should session deletion require confirmation callbacks by default?
- Do we need per-chat or per-project limits for high reasoning effort mode?
