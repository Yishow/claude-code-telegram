# Copilot Degraded Status Runbook

Use this when `/copilot status` reports degraded health.

## Quick Triage

1. Run `/copilot status` and capture:
   - `health`
   - `reason`
   - runtime `fallback_mode`
   - auth/model sections (if present)
2. Confirm provider/model scope:
   - `/provider`
   - `/model`
3. Check active sessions:
   - `/copilot sessions`

## Common Failure Domains

### Auth failures

- Symptoms: auth errors, unauthorized/forbidden in status output.
- Actions:
  1. Re-authenticate Copilot CLI on host.
  2. Re-run `/copilot status`.
  3. If still failing, temporarily switch to `DEFAULT_PROVIDER=claude` or `/provider claude`.

### SDK runtime unavailable

- Symptoms: client not started, runtime degraded.
- Actions:
  1. Retry a small prompt.
  2. If `COPILOT_FALLBACK_MODE=sdk_then_cli`, verify CLI fallback path works.
  3. Restart bot process.

### Session drift / context_changed events

- Symptoms: context drift notices in chat.
- Actions:
  1. Use `/copilot sessions` to inspect session map.
  2. Remove stale session with `/copilot delete <session_id>`.
  3. Use `/new` to start a clean conversation.

### Tool policy denials

- Symptoms: blocked tool messages.
- Actions:
  1. Review `CLAUDE_ALLOWED_TOOLS` / `CLAUDE_DISALLOWED_TOOLS`.
  2. Re-run with lower-privilege request or adjust policy intentionally.

## Recovery Validation

1. `/copilot status` shows `health=healthy`.
2. Ask a small test prompt.
3. Verify no orphan interactive requests remain (no stale ask/permission prompts).
