## 1. Configuration and Policy Surface

- [ ] 1.1 Add settings for release-driven controls (`external_cli_server`, `copilot_config_dir_policy`, `copilot_reasoning_default`, `copilot_skill_directories`, `copilot_disabled_skills`, `mcp_env_value_mode`)
- [ ] 1.2 Define precedence rules between env defaults and runtime command overrides
- [ ] 1.3 Add config validation tests for new enum/policy fields

## 2. Health and Introspection Commands

- [ ] 2.1 Implement Copilot status/introspection command that reports runtime, auth, and model metadata
- [ ] 2.2 Add safe redaction for status payload fields
- [ ] 2.3 Add tests for healthy/degraded status response formatting

## 3. Session and Context Operations

- [ ] 3.1 Implement commands for session listing and targeted session deletion
- [ ] 3.2 Integrate context_changed event handling into user-visible notifications
- [ ] 3.3 Add tests for session list/delete and context drift notifications

## 4. Runtime and Skill Controls

- [ ] 4.1 Implement command/control flow for reasoning_effort selection
- [ ] 4.2 Implement command/control flow for skillDirectories and disabledSkills management
- [ ] 4.3 Implement external CLI server mode and per-project configDir wiring in Copilot SDK manager
- [ ] 4.4 Add tests for runtime control persistence and policy enforcement

## 5. MCP Policy and Reliability Hardening

- [ ] 5.1 Add MCP envValueMode operator control and diagnostics output
- [ ] 5.2 Add watchdog timeout classification and stream unsubscribe safety wrappers
- [ ] 5.3 Emit telemetry for large payload handling, timeout domains, and cleanup outcomes
- [ ] 5.4 Add tests for timeout guardrails, cleanup behavior, and telemetry schema

## 6. Documentation and Verification

- [ ] 6.1 Update README/docs with new Copilot control-plane commands and policies
- [ ] 6.2 Add operational runbook for degraded status and recovery actions
- [ ] 6.3 Run targeted Copilot unit/integration suites and record results
