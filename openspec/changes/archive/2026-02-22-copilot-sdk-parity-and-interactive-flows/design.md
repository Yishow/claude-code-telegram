## Context

Current Copilot provider flow is SDK-first with CLI fallback, but interactive hooks are only emitted and not fully consumed by Telegram handlers. This leads to unresolved futures (`ask_user`, `permission_request`) and timeout-driven behavior. Additionally, Copilot tool policy enforcement is weaker than Claude path, and some SDK capabilities (image attachments, richer runtime controls, persistent Copilot session mapping) are not fully wired into bot-facing behavior.

Constraints:
- Existing architecture uses `ClaudeIntegration` facade with provider dispatch.
- Bot supports both agentic and classic modes with different callback routing.
- Security posture depends on ToolMonitor and directory-bounded validation.
- Existing users should keep current behavior unless explicitly changing runtime controls.

Stakeholders:
- Bot operators (need deterministic controls and observability)
- End users (need reliable interaction prompts and approvals)
- Maintainers (need parity across providers and testable behavior)

## Goals / Non-Goals

**Goals:**
- Complete Copilot interactive request lifecycle (`ask_user`, `permission_request`) end-to-end.
- Enforce ToolMonitor-based governance parity for Copilot tool flow.
- Add deterministic runtime controls for provider/model/fallback behavior.
- Improve reliability with Copilot session-map persistence and attachment wiring.
- Add observable telemetry for interactive and fallback outcomes.

**Non-Goals:**
- Rewriting overall bot orchestration architecture.
- Introducing a new persistence backend beyond current SQLite stack.
- Changing default user-facing text style beyond required Copilot interaction UX.
- Replacing existing Claude provider behavior.

## Decisions

### 1. Introduce a dedicated Copilot interaction bridge state
Decision:
- Add a dedicated interaction registry component to hold pending futures keyed by scoped identity: `{chat/thread scope, user_id, session_id, interaction_id}`.
- Registry manages create/resolve/timeout/cleanup lifecycle.

Rationale:
- Avoids leaking raw futures into generic handler state.
- Prevents cross-resolution between concurrent users/sessions.
- Keeps timeout and cleanup logic testable and centralized.

Alternatives considered:
- Store futures directly in `context.user_data` without abstraction.
  - Rejected: hard to test, easy to leak stale entries, coupled to Telegram runtime objects.

### 2. Standardize interactive event transport through StreamUpdate metadata
Decision:
- Preserve Copilot event types (`ask_user`, `permission_request`, `tool`, `result`, `reasoning`) and define normalized metadata contract consumed by orchestrator/message handlers.
- For interactive types, include opaque interaction IDs used by callback routing.

Rationale:
- Keeps provider abstraction intact at facade boundary.
- Enables both classic and agentic handlers to share interaction resolution logic.

Alternatives considered:
- Create separate callback channel bypassing stream callback.
  - Rejected: duplicates plumbing and complicates fallback behavior.

### 3. Extend Telegram callback routing for Copilot interactions
Decision:
- Extend callback patterns in orchestrator/classic callbacks to handle permission and choice actions using signed callback payloads.
- For freeform ask_user, mark session as “awaiting_user_input” and consume next text message as interaction answer before normal Claude/Copilot execution.

Rationale:
- Uses existing Telegram interaction model (inline buttons + text reply).
- Minimizes UI churn while unblocking Copilot interaction flows.

Alternatives considered:
- Require only freeform replies (no buttons).
  - Rejected: worse UX and higher user error for permission decisions.

### 4. Enforce Copilot tool validation via ToolMonitor before execution
Decision:
- In Copilot SDK pre-tool hook, call ToolMonitor validation with tool name/args and context.
- Return deny decision to SDK when validation fails.
- Surface user-facing blocked-tool guidance consistent with Claude path.

Rationale:
- Aligns security behavior across providers.
- Removes silent divergence in policy enforcement.

Alternatives considered:
- Keep passive logging only.
  - Rejected: does not satisfy parity or security requirement.

### 5. Add explicit fallback policy config
Decision:
- Add config enum-style setting (e.g., `copilot_fallback_mode`) with values:
  - `sdk_only`
  - `sdk_then_cli`
- Default remains compatible with current behavior (`sdk_then_cli`) unless operator changes it.

Rationale:
- Operators need deterministic behavior in compliance or debugging environments.

Alternatives considered:
- Hardcode fallback always on.
  - Rejected: no operator control.

### 6. Validate provider config and add runtime controls
Decision:
- Validate `default_provider` against allowed set at config parse time.
- Add bot commands and session state support for provider/model controls with clear scope (session default + one-shot override).

Rationale:
- Prevents silent misconfiguration.
- Gives operators/users controlled runtime flexibility.

Alternatives considered:
- Env-only control.
  - Rejected: too static for interactive bot workflows.

### 7. Persist Copilot session map in existing storage
Decision:
- Store `{user_id, project_path, copilot_session_id, updated_at}` in SQLite via existing storage layer.
- Hydrate map on startup/lazy-load and update after successful Copilot responses.

Rationale:
- Survives process restart while reusing existing persistence stack.

Alternatives considered:
- Keep in-memory only.
  - Rejected: loses resume continuity on restart.

### 8. Wire image attachments in Copilot path
Decision:
- Pass processed image file path from photo handlers through facade to Copilot SDK `attachments` payload.
- Keep Claude path unchanged.

Rationale:
- Uses already-implemented SDK feature with minimal extension.

Alternatives considered:
- Defer attachment support.
  - Rejected: existing code already supports it and gap is wiring only.

### 9. Add structured Copilot telemetry
Decision:
- Emit structured logs/counters for:
  - ask_user requested/resolved/timeout
  - permission requested/approved/denied/timeout
  - fallback attempted/succeeded/failed
  - tool validation deny reasons

Rationale:
- Enables operational debugging and SLA monitoring.

Alternatives considered:
- Keep ad-hoc logs only.
  - Rejected: hard to measure regression and reliability.

## Risks / Trade-offs

- [Interactive deadlocks due to missed callback resolution] -> Mitigation: explicit timeout defaults, always-cleanup in `finally`, and integration tests for timeout path.
- [Callback data tampering or replay] -> Mitigation: include scoped identifiers and short-lived validation token in callback payload.
- [Cross-mode complexity (agentic vs classic)] -> Mitigation: shared interaction bridge helper used by both routing modes.
- [Behavioral surprises from provider/model runtime controls] -> Mitigation: clear `/status` output showing current provider/model/fallback state.
- [Persistence drift for stale Copilot session IDs] -> Mitigation: on resume failure, rotate to new session and update storage atomically.

## Migration Plan

1. Add config fields with backward-compatible defaults (`sdk_then_cli`).
2. Introduce interaction bridge and callback handlers behind capability tests.
3. Integrate ToolMonitor validation in Copilot pre-tool hook.
4. Add provider/model runtime commands and status output updates.
5. Add session map persistence repository and hydration path.
6. Wire photo attachment path to Copilot provider calls.
7. Add telemetry hooks and docs updates.
8. Run focused unit tests then broader regression suites.

Rollback strategy:
- Revert to previous behavior by toggling fallback mode and disabling new runtime commands if severe regression occurs.
- In worst case, switch default provider to Claude and disable Copilot path operationally.

## Open Questions

- Command UX naming: should runtime controls be `/provider` + `/model`, or a unified `/copilot` subcommand set?
- How long should interaction tokens remain valid for callback replay protection?
- Should fallback mode be globally configurable only, or overridable per session by privileged users?
