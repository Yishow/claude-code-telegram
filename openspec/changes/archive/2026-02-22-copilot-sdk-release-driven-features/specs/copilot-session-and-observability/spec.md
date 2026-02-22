## ADDED Requirements

### Requirement: Copilot observability includes release-driven operational signals
The system SHALL extend Copilot observability with release-driven signals covering status probes, session operations, context drift, and reliability outcomes.

#### Scenario: telemetry event includes operation category
- **WHEN** any Copilot control-plane operation completes
- **THEN** telemetry includes operation category, outcome, and duration tags

#### Scenario: observability dashboard can differentiate failure domains
- **WHEN** Copilot failure events occur
- **THEN** telemetry classifies failures by auth, connectivity, timeout, policy, or runtime domains
