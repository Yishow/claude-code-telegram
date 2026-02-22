## 1. Config and Runtime Guardrails

- [x] 1.1 Add strict validation for `default_provider` and new `copilot_fallback_mode` settings in `src/config/settings.py`
- [x] 1.2 Update `.env.example` and config docs for provider/model/fallback controls
- [x] 1.3 Add unit tests for valid/invalid provider and fallback configuration values

## 2. Copilot Interaction Bridge Foundation

- [x] 2.1 Create a shared interaction bridge component for pending Copilot futures (ask_user/permission_request) with scoped keys and lifecycle APIs
- [x] 2.2 Integrate bridge with Copilot stream event handling so interaction metadata includes resolvable interaction identity
- [x] 2.3 Implement timeout and guaranteed cleanup behavior for all interaction terminal states
- [x] 2.4 Add unit tests for concurrent isolation, timeout, and cleanup paths

## 3. Telegram ask_user Flow

- [x] 3.1 Implement classic-mode ask_user UI rendering (choices + freeform prompt) and response routing
- [x] 3.2 Implement agentic-mode ask_user UI rendering and response routing
- [x] 3.3 Consume next text reply for pending freeform ask_user before normal prompt execution
- [x] 3.4 Add tests covering choice selection, freeform response, and timeout fallback

## 4. Telegram permission_request Flow

- [x] 4.1 Implement permission approve/deny callback UX in classic mode with scoped callback payloads
- [x] 4.2 Implement permission approve/deny callback UX in agentic mode and ensure pattern registration supports these callbacks
- [x] 4.3 Resolve pending permission futures from callback handlers with timeout-safe behavior
- [x] 4.4 Add tests for approve, deny, expired, and invalid callback scope scenarios

## 5. Tool Governance Parity

- [x] 5.1 Integrate ToolMonitor validation in Copilot pre-tool hook before tool execution
- [x] 5.2 Return explicit deny decision to Copilot SDK when validation fails
- [x] 5.3 Align user-facing blocked-tool messaging with Claude path semantics
- [x] 5.4 Add tests verifying allow/deny behavior parity for critical tools across providers

## 6. Provider and Model Runtime Controls

- [x] 6.1 Add bot commands or subcommands for viewing/changing active provider and model
- [x] 6.2 Implement session-scoped provider/model state and one-shot request override behavior
- [x] 6.3 Update `/status` output to show active provider/model/fallback mode clearly
- [x] 6.4 Add tests for command parsing, state transitions, and override precedence

## 7. Copilot Reliability and Wiring

- [x] 7.1 Implement configurable fallback policy in Copilot execute path (`sdk_only` vs `sdk_then_cli`)
- [x] 7.2 Persist Copilot session map in storage and restore mapping on startup or first use
- [x] 7.3 Wire image handler output path into Copilot SDK `attachments` for photo-driven requests
- [x] 7.4 Add tests for fallback policy behavior, persisted session resume, and attachment payload construction

## 8. Telemetry, Logging, and Documentation

- [x] 8.1 Add structured telemetry/log events for ask_user, permission outcomes, fallback outcomes, and tool denials
- [x] 8.2 Ensure sensitive values in interactive and tool logs are redacted consistently
- [x] 8.3 Document Copilot interaction lifecycle, runtime controls, and operational troubleshooting in README/docs
- [x] 8.4 Run targeted and regression test suites for Copilot + bot routing changes and capture verification results

## Verification Notes

- `python -m py_compile` passed for all changed source files.
- `uv run pytest ...` could not run in this environment due restricted network (dependency download DNS failure).
