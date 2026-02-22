# copilot-mcp-policy-and-reliability Specification

## Purpose
TBD - created by archiving change copilot-sdk-release-driven-features. Update Purpose after archive.
## Requirements
### Requirement: MCP env value mode is operator-configurable
The system SHALL allow operators to configure MCP `envValueMode` policy and SHALL expose active mode in diagnostics.

#### Scenario: env value mode set to masked
- **WHEN** operator sets env value mode policy to masked
- **THEN** MCP configuration uses masked mode and status output reflects masked policy

### Requirement: High-concurrency reliability guards are enforced
The system SHALL include reliability guards for high-concurrency Copilot workloads including stream lifecycle safety and timeout watchdog behavior.

#### Scenario: long-running request hits watchdog threshold
- **WHEN** a request exceeds configured watchdog timeout
- **THEN** system fails fast with timeout classification and emits telemetry

#### Scenario: stream unsubscribe/cleanup occurs after request completion
- **WHEN** request completes, fails, or is cancelled
- **THEN** event subscriptions are released and no orphan listener remains

### Requirement: Large payload handling is observable
The system SHALL capture telemetry for large JSON-RPC payload handling outcomes and degraded behavior.

#### Scenario: large payload processed successfully
- **WHEN** a large payload request is accepted
- **THEN** system logs payload-size bucket and success outcome metrics

