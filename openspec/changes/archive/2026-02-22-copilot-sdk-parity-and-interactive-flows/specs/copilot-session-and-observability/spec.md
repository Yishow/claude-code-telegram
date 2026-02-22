## ADDED Requirements

### Requirement: Copilot session mapping survives process restarts
The system SHALL persist Copilot session mapping state so resumable Copilot sessions continue after bot restart.

#### Scenario: resumed session after restart
- **WHEN** user had a Copilot session before restart and sends a follow-up request
- **THEN** the system resumes the prior Copilot session context when session is still valid

### Requirement: Copilot provider supports image attachment wiring
The system SHALL pass processed Telegram image file paths as Copilot SDK attachments when provider is Copilot and attachment path is available.

#### Scenario: photo request includes attachment in Copilot SDK call
- **WHEN** user submits photo and request is routed to Copilot provider
- **THEN** Copilot SDK send request includes the corresponding file attachment payload

### Requirement: Copilot runtime emits actionable telemetry
The system SHALL emit structured counters/log events for interactive request outcomes, fallback usage, and timeout rates.

#### Scenario: ask_user timeout increments telemetry
- **WHEN** a Copilot ask_user request times out
- **THEN** telemetry records an ask_user timeout event with provider and context tags

#### Scenario: fallback attempt is tracked
- **WHEN** SDK execution falls back to CLI
- **THEN** telemetry records fallback attempted and fallback outcome events
