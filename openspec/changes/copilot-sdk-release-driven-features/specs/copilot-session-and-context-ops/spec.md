## ADDED Requirements

### Requirement: Copilot session operations are available from bot commands
The system SHALL provide commands to list and delete Copilot sessions for authorized scope.

#### Scenario: list sessions returns active session inventory
- **WHEN** user requests session list
- **THEN** bot returns known sessions with identifiers, workspace association, and recent usage metadata

#### Scenario: delete session removes selected session
- **WHEN** user requests session deletion for a valid session ID
- **THEN** the session is removed and deletion result is confirmed

### Requirement: Context drift is surfaced to users
The system SHALL process Copilot `context_changed` signals and SHALL notify users when active context diverges from expected session context.

#### Scenario: context_changed event triggers user notification
- **WHEN** Copilot emits a context_changed event for active session
- **THEN** bot posts a context drift notice and includes recovery actions
