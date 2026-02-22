# copilot-health-and-introspection Specification

## Purpose
TBD - created by archiving change copilot-sdk-release-driven-features. Update Purpose after archive.
## Requirements
### Requirement: Copilot health/introspection command exposes SDK runtime status
The system SHALL provide a bot command that reports Copilot SDK runtime status including client availability, auth state, and model metadata.

#### Scenario: status command returns healthy runtime state
- **WHEN** user runs Copilot status command while SDK client is available
- **THEN** the bot displays runtime health, auth summary, and model/runtime metadata

#### Scenario: status command surfaces degraded state
- **WHEN** SDK client is unavailable or auth fails
- **THEN** the bot displays a degraded status with actionable recovery guidance

### Requirement: Introspection output is safe and operator-friendly
The system SHALL redact sensitive values and SHALL return concise operator-friendly diagnostics.

#### Scenario: sensitive values are redacted
- **WHEN** status output includes token-like or secret-like fields
- **THEN** those fields are masked before rendering to users

