## ADDED Requirements

### Requirement: Copilot ask_user interactions are completed end-to-end
The system SHALL route Copilot `ask_user` requests to Telegram users and SHALL return user answers back to the originating Copilot future within the active request lifecycle.

#### Scenario: ask_user with choices is answered by user
- **WHEN** Copilot emits an `ask_user` request containing a question and choices
- **THEN** the bot presents the question to the same user context and resolves the pending Copilot future with the selected answer

#### Scenario: ask_user allows freeform response
- **WHEN** Copilot emits an `ask_user` request with freeform enabled
- **THEN** the bot accepts a text reply from the same user context and resolves the pending Copilot future with that text

### Requirement: Copilot permission_request decisions are completed end-to-end
The system SHALL route Copilot `permission_request` to Telegram approval UX and SHALL return an explicit allow/deny decision to the originating Copilot future.

#### Scenario: permission request approved by user
- **WHEN** a permission request is presented and user approves within timeout
- **THEN** the system responds to Copilot with an approved decision for that tool call

#### Scenario: permission request denied or expired
- **WHEN** a permission request is denied by user or receives no decision before timeout
- **THEN** the system responds to Copilot with a deny decision and records timeout or denial outcome

### Requirement: Interactive futures are isolated and safely cleaned up
The system SHALL isolate pending Copilot interaction futures by user/session/request scope and SHALL cleanup pending state on completion, timeout, cancellation, or execution termination.

#### Scenario: concurrent interactions do not cross-resolve
- **WHEN** two different user contexts have pending Copilot interactions concurrently
- **THEN** a response in one context MUST NOT resolve any future in the other context

#### Scenario: stale pending entries are cleaned
- **WHEN** an interaction is resolved, times out, or command execution fails
- **THEN** the corresponding pending interaction entry is removed from runtime state
