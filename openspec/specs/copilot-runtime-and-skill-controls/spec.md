# copilot-runtime-and-skill-controls Specification

## Purpose
TBD - created by archiving change copilot-sdk-release-driven-features. Update Purpose after archive.
## Requirements
### Requirement: Skill directories and disabled skill controls are configurable
The system SHALL support runtime configuration for `skillDirectories` and `disabledSkills` and SHALL expose current state via command output.

#### Scenario: operator updates disabled skills list
- **WHEN** operator applies a disabledSkills update
- **THEN** new sessions start with updated skill policy and status output reflects the change

### Requirement: Reasoning effort is user-selectable by policy
The system SHALL support `reasoning_effort` runtime selection with policy boundaries.

#### Scenario: user chooses high reasoning effort
- **WHEN** user sets reasoning mode to high for current session
- **THEN** subsequent Copilot requests include high reasoning effort until changed

### Requirement: Copilot execution mode supports external CLI server
The system SHALL support a mode that connects to an external Copilot CLI server endpoint instead of local default process startup.

#### Scenario: external CLI server mode is enabled
- **WHEN** runtime config enables external server mode
- **THEN** Copilot client initialization uses configured external endpoint and reports mode in status

### Requirement: Config directory isolation is supported per project scope
The system SHALL support per-project `configDir` isolation for Copilot SDK sessions.

#### Scenario: project-specific config dir is applied
- **WHEN** request is executed inside configured project scope
- **THEN** session uses project-scoped config directory for auth/cache isolation

