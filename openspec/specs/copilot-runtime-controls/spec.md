# copilot-runtime-controls Specification

## Purpose
TBD - created by archiving change copilot-sdk-parity-and-interactive-flows. Update Purpose after archive.
## Requirements
### Requirement: Provider defaults are validated and deterministic
The system SHALL validate configured default provider values and SHALL fail fast on unsupported provider configuration.

#### Scenario: invalid provider configuration is rejected
- **WHEN** configuration contains an unsupported `default_provider` value
- **THEN** application startup fails with a clear configuration error

### Requirement: Runtime provider and model selection is user-controllable
The system SHALL provide bot-level controls to view and update active provider/model behavior with scope rules defined by session context.

#### Scenario: user changes provider for current session
- **WHEN** user invokes provider control command to switch to Copilot
- **THEN** subsequent commands in that session use Copilot unless explicitly overridden

#### Scenario: user overrides model for a single request
- **WHEN** user sends a command with one-time model override
- **THEN** only that request uses the specified model and later requests return to session default

### Requirement: Copilot fallback policy is configurable
The system SHALL support configurable fallback policy modes including SDK-only and SDK-with-CLI-fallback.

#### Scenario: SDK-only mode avoids CLI fallback
- **WHEN** fallback mode is SDK-only and SDK execution fails
- **THEN** request fails with explicit Copilot SDK failure message and does not invoke Copilot CLI

#### Scenario: fallback mode allows CLI failover
- **WHEN** fallback mode allows CLI and SDK execution fails with recoverable error
- **THEN** system attempts CLI fallback and reports fallback path in logs

