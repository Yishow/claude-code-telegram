## ADDED Requirements

### Requirement: Copilot tool execution enforces ToolMonitor policy
The system SHALL validate Copilot tool intents against the same ToolMonitor policy model used by Claude provider before tool execution is allowed.

#### Scenario: allowed Copilot tool passes validation
- **WHEN** Copilot requests a tool call that satisfies tool allowlist and security validation
- **THEN** execution is permitted and the tool call proceeds

#### Scenario: blocked Copilot tool is denied
- **WHEN** Copilot requests a tool call that violates allowlist or security validation
- **THEN** execution is denied and the user receives an actionable blocked-tool message

### Requirement: Governance outcomes are visible and auditable
The system SHALL emit structured logs for Copilot tool validation outcomes including allow/deny reason and tool metadata.

#### Scenario: denied tool call produces structured log
- **WHEN** a Copilot tool call is denied
- **THEN** logs include user context, tool name, and denial reason without leaking secrets

### Requirement: Copilot and Claude governance semantics remain consistent
The system SHALL provide equivalent governance behavior for critical tools across Copilot and Claude providers.

#### Scenario: critical tool policy behaves the same across providers
- **WHEN** a critical tool is disallowed by policy
- **THEN** both provider paths reject execution and surface equivalent user-facing guidance
